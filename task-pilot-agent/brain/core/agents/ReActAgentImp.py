from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from brain.core.agents.base_agent import AgentState
from brain.core.agents.react_agent import ReActAgent
from brain.core.context import AgentContext
from config.config import agentSettings
from llm.manager import react_mgr
from llm.types import LLMMessage, RoleType, ToolCall
from utils.logger import get_logger

logger = get_logger(__name__)


class ReActAgentImp(ReActAgent):
    """Concrete ReAct agent that decides on tool usage and executes them."""

    def __init__(self, context: AgentContext, prompt_template: str, max_steps: int) -> None:
        super().__init__(
            name="react",
            description="Tool-assisted reasoning agent",
            context=context,
            systemPrompt=prompt_template,
            config=agentSettings,
            maxSteps=max(1, max_steps),
            llm_manager=react_mgr,
        )
        self._prompt = prompt_template
        self.history: List[Dict[str, Any]] = []
        self.evidence: List[str] = []
        self.final_answer: Optional[str] = None
        self._last_decision: Optional[Dict[str, Any]] = None
        self.thought_message: Optional[LLMMessage] = None

    async def think(self) -> Optional[LLMMessage]:
        decision = await self._ask_for_decision(self.history, self.current_step)
        if decision is None:
            self.evidence.append(f"第 {self.current_step} 步：模型未返回有效决策，结束。")
            self.set_state(AgentState.FINISHED)
            return None

        self._last_decision = decision
        thought = decision.get("thought", "").strip()
        action = (decision.get("action") or "").strip()
        tool_args = decision.get("input") if isinstance(decision.get("input"), dict) else {}
        answer_text = (decision.get("answer") or "").strip()

        raw_tool_calls: List[tuple[str, Dict[str, Any]]] = []
        tool_calls: Optional[List[ToolCall]] = None
        if action and action.lower() not in {"finish", "stop", "final", "answer", "respond", "done"}:
            raw_tool_calls.append((action, tool_args))
            tool_calls = [ToolCall(name=action, arguments=tool_args)]

        self._emit_thought(raw_tool_calls)

        content = thought or answer_text or ""
        return LLMMessage(
            role=RoleType.ASSISTANT.value,
            content=content,
            toolCalls=tool_calls,
        )

    async def act(self, thought: Optional[LLMMessage]) -> Optional[str]:
        decision = self._last_decision
        self._last_decision = None

        if thought is None or decision is None:
            self.set_state(AgentState.FINISHED)
            return None

        step = self.current_step
        action = (decision.get("action") or "").strip()
        action_lower = action.lower()
        tool_args = decision.get("input") if isinstance(decision.get("input"), dict) else {}
        thought_text = decision.get("thought", "").strip()
        answer_text = (decision.get("answer") or "").strip()

        if not action or action_lower in {"finish", "stop", "final", "answer", "respond", "done"}:
            final_answer = answer_text or thought_text
            self.final_answer = final_answer
            self.history.append(
                {
                    "step": step,
                    "thought": thought_text,
                    "action": "finish",
                    "observation": final_answer,
                }
            )
            if thought_text:
                self.evidence.append(f"第 {step} 步思考：{thought_text}")
            if final_answer:
                self.evidence.append(f"模型给出的最终答案：{final_answer}")
            self.set_state(AgentState.FINISHED)
            return final_answer

        if not self._has_tool(action):
            msg = f"工具 `{action}` 不存在或不可用，提前结束。"
            self.history.append(
                {
                    "step": step,
                    "thought": thought_text,
                    "action": action,
                    "input": tool_args,
                    "observation": msg,
                }
            )
            self.evidence.append(msg)
            if answer_text:
                self.final_answer = answer_text
                self.evidence.append(f"模型尝试给出的答案：{answer_text}")
                self.set_state(AgentState.FINISHED)
                return answer_text
            self.set_state(AgentState.FINISHED)
            return msg

        observation = await self._invoke_tool(action, tool_args)
        self.history.append(
            {
                "step": step,
                "thought": thought_text,
                "action": action,
                "input": tool_args,
                "observation": observation,
            }
        )
        if thought_text:
            self.evidence.append(f"{step}. 思考：{thought_text}")
        self.evidence.append(f"工具 `{action}` 返回：{observation}")
        return observation

    async def _ask_for_decision(
        self,
        history: List[Dict[str, Any]],
        step: int,
    ) -> Optional[Dict[str, Any]]:
        system_content = self._build_system_prompt(history, step)
        messages = [
            LLMMessage(role=RoleType.SYSTEM.value, content=system_content),
            LLMMessage(role=RoleType.USER.value, content=self.context.query),
        ]
        tools = self.context.toolCollection.to_openai_tools() if getattr(self.context, "toolCollection", None) else None
        try:
            if tools:
                tool_calls, assistant_text = await self.llm.ask_tool_async(
                    messages,
                    tools=tools,
                    tool_choice="auto",
                )
                tool_calls = tool_calls or []
                if tool_calls:
                    name, args = tool_calls[0]
                    return {
                        "thought": assistant_text or "",
                        "action": name or "",
                        "input": args or {},
                        "answer": "",
                    }
                return {
                    "thought": assistant_text or "",
                    "action": "",
                    "input": {},
                    "answer": assistant_text or "",
                }
            response = await self.llm.generate_async(messages, stream=False)
        except Exception as exc:
            msg = str(exc)
            if "Field required" in msg or "function_call" in msg:
                logger.warning("工具调用不被当前模型支持，退回纯文本回答。错误：%s", msg)
                response = await self.llm.generate_async(messages, stream=False)
            else:
                logger.exception("获取 ReAct 决策失败，请求 %s", self.context.requestId)
                raise

        text = response if isinstance(response, str) else getattr(response, "text", "") or ""
        cleaned = text.strip()
        if not cleaned:
            return None
        return {
            "thought": cleaned,
            "action": "",
            "input": {},
            "answer": cleaned,
        }

    def _build_system_prompt(
        self,
        history: List[Dict[str, Any]],
        step: int,
    ) -> str:
        remaining = max(0, self.maxSteps - step)
        tools_history: List[str] = []
        if history:
            for item in history:
                line = f"- Step {item.get('step')}: thought={item.get('thought', '')!r}, action={item.get('action', '')!r}"
                if item.get("input"):
                    line += f", input={json.dumps(item['input'], ensure_ascii=False)}"
                if item.get("observation"):
                    line += f", observation={item['observation']}"
                tools_history.append(line)
        else:
            tools_history.append("- (none yet)")

        tool_lines: List[str] = []
        for spec in self._collect_tool_specs():
            desc = spec.get("description") or ""
            schema = spec.get("schema")
            schema_str = json.dumps(schema, ensure_ascii=False) if schema else ""
            tool_lines.append(f"- {spec['name']}: {desc}{(' | schema: ' + schema_str) if schema_str else ''}")
        if not tool_lines:
            tool_lines.append("- (none)")

        template_vars = {
            "remaining_iterations": remaining,
            "dialogue_history": self._format_conversation_history(),
            "tool_history": "\n".join(tools_history),
            "available_tools": "\n".join(tool_lines),
        }

        if not isinstance(self._prompt, str):
            raise TypeError("React system prompt must be a string template.")
        return self._prompt.format(**template_vars).strip()

    def _collect_tool_specs(self) -> List[Dict[str, Any]]:
        specs: List[Dict[str, Any]] = []
        tool_collection = getattr(self.context, "toolCollection", None)
        if not tool_collection:
            return specs
        for name, tool in tool_collection.tool_map.items():
            schema = None
            if hasattr(tool, "input_schema") and isinstance(tool.input_schema, dict):
                schema = tool.input_schema
            elif hasattr(tool, "to_params"):
                try:
                    schema = tool.to_params()  # type: ignore[call-arg]
                except Exception:
                    schema = None
            specs.append(
                {
                    "name": name,
                    "description": getattr(tool, "description", ""),
                    "schema": schema,
                }
            )
        return specs

    def _emit_thought(self, tool_calls: List[tuple[str, Dict[str, Any]]]) -> None:
        self.thought_message = LLMMessage(
            role=RoleType.ASSISTANT.value,
            content="",
            toolCalls=[ToolCall(name=name, arguments=args) for name, args in tool_calls] or None,
        )
        self.context.printer.send(
            None,
            "tool_thought",
            {
                "current_step": self.current_msg or "",
                "tool_calls": tool_calls,
            },
            None,
            True,
        )

    def _has_tool(self, name: str) -> bool:
        return bool(getattr(self.context, "toolCollection", None) and name in self.context.toolCollection.tool_map)

    async def _invoke_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        payload = await self._execute_tool(name, arguments)
        self.context.printer.send(None, payload["message_type"], payload["payload"], None, True)
        return payload["observation"]

    async def _execute_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, str]:
        try:
            raw = await self.context.toolCollection.execute(name, arguments) if getattr(self.context, "toolCollection", None) else None
            message_type = "tool_result"
            payload = {
                "tool": name,
                "arguments": arguments,
                "result": raw,
                **self._tool_execution_metadata(name),
            }
            observation = self._stringify(raw)
            evidence = f"工具 `{name}` 输出：{observation}"
        except Exception as exc:
            logger.exception("Tool %s execution failed", name)
            message_type = "notifications"
            payload = {"process_message": f"工具 `{name}` 调用失败：{exc}"}
            observation = f"调用失败：{exc}"
            evidence = observation
        return {
            "message_type": message_type,
            "payload": payload,
            "observation": observation,
            "evidence": evidence,
        }

    def _tool_execution_metadata(self, tool_name: str) -> Dict[str, Any]:
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
            }
        }

    @staticmethod
    def _stringify(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
