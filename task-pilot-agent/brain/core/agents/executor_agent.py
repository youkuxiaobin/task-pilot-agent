from __future__ import annotations

import json
from typing import List, Optional

from langfuse import observe
from brain.core.agents.base_agent import AgentState
from brain.core.agents.react_agent import ReActAgent

from brain.core.context import AgentContext
from llm.manager import store as prompt_store, executor_mgr
from config.config import agentSettings
#from memory.memory_mgr import Message, RoleType
from llm.types import LLMMessage, parse_openai_tool_calls, ToolCall, RoleType, LLMResponse
from utils.logger import get_logger

logger = get_logger(__name__)

class ExecutorAgent(ReActAgent):
    """ReAct executor that performs each plan step with tools."""

    def __init__(
        self,
        context: AgentContext,

    ) -> None:

        system_prompt = prompt_store.get_prompt("executor_system")

        super().__init__(
            name="executor",
            description="Executes plan steps using tools",
            context=context,
            systemPrompt=system_prompt,
            config=agentSettings,
            maxSteps=agentSettings.core.executor_max_steps,
            llm_manager=executor_mgr,
        )
        self.context = context
        self._pending_response: Optional[LLMResponse] = None
        
        #self.add_message(Message(role="system", content=system_prompt))

    def set_step(self, step: str) -> None:
        self.current_step = step
        self.memory.add_memory(
            LLMMessage(
                role="user",
                content="Execute the following step:\n{}".format(step)),
            user_id=self.context.user_id,
            agent_id=self.context.agent_id,
            run_id=self.context.run_id,
        )
        #step.mark_running()

    def generate_tool_call_message(self) -> List[LLMMessage]:
        agent_messages = self.get_messages(type_name=self.name)
        agent_history = ""
        current_msg: Optional[LLMMessage] = None
        if agent_messages:
            current_msg = agent_messages[-1]
            if len(agent_messages) > 1:
                agent_history = "\n".join(f"<step><task>{msg.role}</task> <task_result>'{msg.content}'</task_result></step>" for msg in agent_messages[:-1])

        if current_msg is None:
            fallback_content = self.current_msg if isinstance(self.current_msg, str) else ""
            current_msg = LLMMessage(role=RoleType.USER.value, content=fallback_content)

        #conversation_history = self._format_conversation_history()

        files_desc = self._format_files()

        prompt = self.system_prompt.format(
            query=self.context.query,
            date=self.context.dateInfo or "",
            files=files_desc,
            history_step=agent_history,
            originTask=self.context.query,
            request_id=self.context.requestId,
        )
        prompt = self.context.compose_system_prompt(prompt)
        logger.debug(
            "Executor prepared tool-call prompt for request %s: prompt_len=%s current_msg_len=%s",
            self.context.requestId,
            len(prompt),
            len(current_msg.content or ""),
        )
        return [
            LLMMessage(role=RoleType.SYSTEM.value, content=prompt),
            LLMMessage(role=RoleType.USER.value, content=current_msg.content),
        ]

    @observe(name="executor_think")
    async def think(self) -> Optional[LLMMessage]:
        if self.current_step is None:
            self.set_state(AgentState.FINISHED)
            return None
        
        
        tool_calls, content = await self.llm.ask_tool_async(
            self.generate_tool_call_message(),
            tools=self.context.toolCollection.to_openai_tools(),
            tool_choice="auto",
        )

        thought = LLMMessage(
            role=RoleType.ASSISTANT.value,
            content=content,
            toolCalls=[ToolCall(name=tool_call[0], arguments=tool_call[1]) for tool_call in tool_calls],
        )
        self.context.printer.send(None, "tool_thought", { "current_step": self.current_msg, "tool_calls": tool_calls}, None, True)
        logger.debug(
            "Executor think finished for request %s with %s tool call(s)",
            self.context.requestId,
            len(thought.toolCalls or []),
        )
    
        #self.add_message(thought)
        return thought

    @observe(name="executor_act")
    async def act(self, thought: Optional[LLMMessage]) -> Optional[str]:
        if thought is None :
            self.set_state(AgentState.FINISHED)
            return None

        if not thought.toolCalls:
            self.set_state(AgentState.FINISHED)
            return thought.content

        outputs = []
        for call in thought.toolCalls:
            
            result = await self.context.toolCollection.execute(call.name, call.arguments)
            #call_result = json.loads(result)
            
            logger.debug(
                "Executor completed tool call for request %s: tool=%s arg_keys=%s result_type=%s",
                self.context.requestId,
                call.name,
                sorted((call.arguments or {}).keys()),
                type(result).__name__,
            )
            outputs.append({"tool": call.name, "result": result})
            self.add_message(
                LLMMessage(
                    role=RoleType.ASSISTANT.value,
                    content=prompt_store.get_prompt("exectutor_to_str").format(tool_name=call.name, result=result),
                )
            )

            self.context.printer.send(
                None,
                "tool_result",
                {
                    "tool": call.name,
                    "arguments": call.arguments,
                    "result": result,
                    **self._tool_execution_metadata(call.name),
                },
                None,
                True,
            )

        self.set_state(AgentState.FINISHED)
        return json.dumps(outputs)

    def _tool_execution_metadata(self, tool_name: str) -> dict:
        meta = getattr(self.context.toolCollection, "last_execution", None)
        if not isinstance(meta, dict) or meta.get("tool") != tool_name:
            return {}
        return {
            key: value
            for key, value in meta.items()
            if key
            in {
                "durationMs",
                "failed",
                "resultSummary",
                "error",
                "startedAt",
                "completedAt",
                "userId",
                "agentId",
                "taskId",
                "requestId",
                "runId",
                "sessionId",
                "runEnvironment",
                "workDir",
            }
        }
