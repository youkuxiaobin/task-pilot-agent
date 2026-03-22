from __future__ import annotations
import os
import json
import asyncio
from dataclasses import dataclass
from typing import List, Optional

from brain.core.context import AgentContext
from brain.models.agent import Message, ToolCall, ToolFunction


@dataclass
class ToolCallResponse:
    content: str
    toolCalls: List[ToolCall]
    finishReason: Optional[str] = None
    totalTokens: Optional[int] = None
    duration: Optional[int] = None


class LLM:
    def __init__(self, model_name: str, llm_erp: str = ""):
        self.model = model_name
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.base_url = os.getenv("LLM_BASE_URL", "")
        self.interface_url = os.getenv("LLM_INTERFACE_URL", "/v1/chat/completions")
        self.function_call_type = os.getenv("LLM_FUNCTION_CALL_TYPE", "function_call")

    async def ask(self, context: AgentContext, messages: List[Message], system_messages: List[Message], stream: bool, temperature: float) -> str:
        # Minimal stub: echo last user ask
        text = ""
        for m in reversed(messages):
            if m.role.value == "user":
                text = m.content
                break
        if not text:
            text = "OK"
        # Small delay to simulate latency
        await asyncio.sleep(0.05)
        return f"已记录任务：{text}"

    async def ask_tool(self,
                       context: AgentContext,
                       messages: List[Message],
                       system_msg: Message,
                       tools: "ToolCollection",  # type: ignore
                       tool_choice: str,
                       temperature: Optional[float],
                       stream: bool,
                       timeout: int) -> ToolCallResponse:
        # Minimal stub: think with no tool calls
        thought = "根据当前状态和可用工具，确定下一步行动。"
        # You can add heuristics to propose a tool call based on keywords
        last_user = next((m for m in reversed(messages) if m.role.value == "user"), None)
        tool_calls: List[ToolCall] = []
        if last_user and any(k in last_user.content.lower() for k in ["搜索", "search"]):
            # propose deep_search tool
            args = json.dumps({"query": last_user.content})
            tool_calls = [ToolCall(id=str(id(self)), type="function", function=ToolFunction(name="deep_search", arguments=args))]
        await asyncio.sleep(0.05)
        return ToolCallResponse(content=thought, toolCalls=tool_calls, finishReason="stop")
