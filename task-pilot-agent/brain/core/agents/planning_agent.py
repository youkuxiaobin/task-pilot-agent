from __future__ import annotations

import json
from typing import List, Optional

from llm.types import LLMMessage, RoleType, ToolCall

from brain.core.agents.base_agent import AgentState
from brain.core.agents.react_agent import ReActAgent
from brain.core.context import AgentContext
from config.config import agentSettings
from llm.manager import store as prompt_store, planner_mgr

from brain.core.tools.plan_tool import PlanFunctionTool

from utils.logger import get_logger

logger = get_logger(__name__)
class PlanningAgent(ReActAgent):
    """Planning agent implemented as a ReAct agent with a dedicated plan tool."""

    def __init__(self, context: AgentContext) -> None:
        self.context = context
        self.plan_tool = PlanFunctionTool()
      

        super().__init__(
            name="planning",
            description="Creates and manages task plans",
            context=context,
            systemPrompt=prompt_store.get_prompt("plan_system"),
            config=agentSettings,
            maxSteps=max(1, agentSettings.core.planer_max_steps),
            llm_manager=planner_mgr,
        )


    async def think(self) -> Optional[LLMMessage]:
        """Ask LLM to decide how to operate the plan tool."""
        messages = self._build_conversation()
        tool_spec = [self.plan_tool.to_openai_tool()]
        try:
            tool_calls, result = await self.llm.ask_tool_async(messages, tools=tool_spec, tool_choice="auto")
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.error("Planning think phase failed: %s", exc, exc_info=True)
            self.set_state(AgentState.ERROR)
            return None

        # ask_tool may return None if provider already executed tool internally
        normalized_calls: List[ToolCall] = []
        if tool_calls:
            for name, args in tool_calls:
                normalized_calls.append(ToolCall(name=name, arguments=args))

        thought = LLMMessage(
            role=RoleType.ASSISTANT.value,
            content=result,
            toolCalls=normalized_calls if normalized_calls else None,
        )
        self.context.printer.send(
            None,
            "plan_thought",
            {
                "request_id": self.context.requestId,
                "tool_calls": [
                    {"name": call.name, "arguments": call.arguments}
                    for call in (normalized_calls or [])
                ],
            },
            None,
            True,
        )
        #self.add_message(thought)
        return thought

    async def act(self, thought: Optional[LLMMessage]) -> Optional[str]:
        if thought is None:
            self.set_state(AgentState.FINISHED)
            return None

        results = []
        if thought.toolCalls:
            for call in thought.toolCalls:
                try:
                    outcome = self.plan_tool.execute(call.arguments)
                except Exception as exc:  # pragma: no cover - ensure visibility
                    self.logger.error("Planning tool execution failed: %s", exc, exc_info=True)
                    outcome = f"plan tool execution failed: {exc}"
                results.append({"tool": call.name, "result": outcome})

        else:
            # if no tool calls, then finish
            self.set_state(AgentState.FINISHED)
            return json.dumps({"message": thought.content})

        plan_dict = self.plan_tool.plan_dict()
        if plan_dict:
            #self.context.printer.send(None, "plan", plan_dict, None, True)
            payload = {
                "title": plan_dict["title"],
                "steps": plan_dict["steps"],
                "step_status": plan_dict.get("step_status", []),
                "notes": plan_dict.get("notes", []),
            }
        else:
            payload = {"title": "", "steps": [], "step_status": [], "notes": []}

        payload["tool_results"] = results
        payload["command"] = self.plan_tool.current_command or ""
        self.add_message(
            LLMMessage(
                role=RoleType.ASSISTANT.value,
                content=self.plan_tool.to_str(),
                toolCallId=call.name,
            )
        )
        self.set_state(AgentState.FINISHED)
        return json.dumps(payload, ensure_ascii=False)

    # ------------------------------------------------------------------
    def _build_conversation(self) -> List[LLMMessage]:
        system_message = LLMMessage.system(self._render_system_prompt())
        user_message = LLMMessage.user(self.context.query)
        return [system_message, user_message]

    def _render_system_prompt(self) -> str:
        files_desc = self._format_files()

        tool_prompt = self.context.toolCollection.to_str()
        prompt_template = self.system_prompt
        context_payload = json.loads(self.current_msg)

        current_plan = context_payload.get("current_plan")
        history_dialogue = self._format_conversation_history(context_payload.get("history"))
        if not history_dialogue:
            history_dialogue = self._format_conversation_history()

        plan_str = ""
        finish_steps_str = ""
        next_step = ""
        if current_plan:
            steps = current_plan.get("steps", []) or []
            next_step = current_plan.get("next_step", "") or ""
            finish_steps = current_plan.get("finish_steps", []) or []
            plan_str = "\n".join(f"<plan_step>{str(step)}</plan_step>" for step in steps)

            for finish_step in finish_steps:
                logger.info("planning reuse past step with status=%s", finish_step.get("status"))
                step_text = finish_step.get("step_text", "") or ""
                tool_output = finish_step.get("tool_outputs", "") or ""
                #if step_text and tool_output:
                finish_steps_str += f"<step>{step_text}</step><step_answer>{tool_output}</step_answer>\n"

        prompt = prompt_template.format(
            tools=tool_prompt,
            date=self.context.dateInfo or "",
            files=files_desc,
            history_dialogue=history_dialogue,
            query=self.context.query or "",
            min_step=str(agentSettings.core.planer_min_steps),
            max_step=str(agentSettings.core.planer_max_steps),
            current_step=next_step,
            plan=plan_str,
            past_steps=finish_steps_str,
        )
        prompt = self.context.compose_system_prompt(prompt)
        logger.debug("planning system prompt prepared: len=%s", len(prompt))
        return prompt
