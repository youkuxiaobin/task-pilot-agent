from __future__ import annotations
from typing import Iterable, List, Optional

from langfuse import observe
from brain.core.agents.base_agent import BaseAgent
from brain.core.context import AgentContext
from llm.manager import store as prompt_store, summary_mgr
from llm.types import LLMMessage, RoleType, LLMResponse
from config.config import agentSettings


class SummaryAgent(BaseAgent):
    def __init__(self, ctx: AgentContext):
        super().__init__(
            name="summary",
            description="summarize task results",
            systemPrompt="Summarize user queries and executed steps into a final answer.",
            context=ctx,
            config=agentSettings,
            maxSteps=1,
            llm_manager=summary_mgr,
        )
        summary_cfg = getattr(self.config, "summary_agent", None)
        self._default_enable_thinking = bool(getattr(summary_cfg, "enable_thinking", False))
        self._default_discard_reasoning = bool(getattr(summary_cfg, "discard_reasoning_content", True))

    @observe(name="summary_summarize")
    async def summarize(
        self,
        query: str,
        plan_steps: Iterable[str],
        evidences: Iterable[str],
        *,
        enable_thinking: Optional[bool] = None,
        discard_reasoning_content: Optional[bool] = None,
    ) -> str:
        """Generate a streamed summary based on executed plan evidence."""
        plan_steps_list = list(plan_steps)
        evidences_list = list(evidences)
        thinking_enabled = self._default_enable_thinking if enable_thinking is None else enable_thinking
        discard_reasoning = self._default_discard_reasoning if discard_reasoning_content is None else discard_reasoning_content
        call_tools = []
        for item in evidences_list:
            call_tools.append(item.strip())
            call_tools.append("")
        
        call_tools_history = "\n".join(call_tools)
        prompt_key = f"summary_{self.context.outputStyle}_prompt"
        prompt_content = prompt_store.get_prompt(prompt_key) 
        prompt_content = prompt_content.format(
            tool_call_history=call_tools_history,
            task=query,
            current_time=self.context.dateInfo
        )
       
        messages: List[LLMMessage] = []
        agent_prompt = (self.context.agent_system_prompt or "").strip()
        if agent_prompt:
            messages.append(LLMMessage(role=RoleType.SYSTEM.value, content=agent_prompt))
        messages.append(LLMMessage(role=RoleType.USER.value, content=prompt_content))

        streamed_chunks: List[str] = []
        def handle_chunk(chunk: str) -> None:
            if not chunk:
                return
            streamed_chunks.append(chunk)
            self.context.printer.send(None, "result", chunk, None, False)

        stream_kwargs = {}
        has_call_tools = bool(call_tools_history.strip())
        if not plan_steps_list or not has_call_tools:
            stream_kwargs["max_tokens"] = 8000
        stream_kwargs["thinking_budget"] = 8192
        final_response = await self.llm.stream_generate_async(
            messages,
            chunk_callback=handle_chunk,
            enable_thinking=thinking_enabled,
            discard_reasoning_content=discard_reasoning,
            **stream_kwargs,
        )
        if streamed_chunks:
            final_text = "".join(streamed_chunks)
        elif isinstance(final_response, LLMResponse) and final_response.text:
            final_text = final_response.text
        else:
            final_text = ""

        if final_text and not streamed_chunks:
            self.context.printer.send(None, "result", final_text, None, False)



        #if final_text:
            #TODO 这里应该记录 user query 和 answer 到数据库中
            #self.context.printer.send(
            #    None,
            #    "task_summary",
            #    {"taskSummary": final_text},
            #    None,
            #    True,
            #)
        return final_text

    async def step(self) -> str:  # type: ignore[override]
        history = [f"{msg.role}: {msg.content}" for msg in self.get_messages()]
        return await self.summarize(self.context.query, [], ["\n".join(history)] if history else [])
    
