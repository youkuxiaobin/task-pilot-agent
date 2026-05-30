from __future__ import annotations

from typing import List, Optional

from brain.core.agents.ReActAgentImp import ReActAgentImp
from brain.core.agents.summary_agent import SummaryAgent
from brain.core.context import AgentContext
from brain.core.handlers.base import AgentHandlerService
from brain.models.requests import AgentRequest
from config.config import agentSettings
from llm.manager import store as prompt_store
from utils.logger import get_logger

logger = get_logger(__name__)


class ReactHandler(AgentHandlerService):
    """
    A ReAct-style orchestrator that now delegates the think-act loop to a dedicated agent.
    """

    def __init__(self) -> None:
        self._prompt = prompt_store.get_prompt("react_system")
        self._max_steps = max(1, getattr(agentSettings.core, "react_max_steps", 6))

    def support(self, ctx: AgentContext, req: AgentRequest) -> bool:
        mode = getattr(req, "mode", None) or getattr(ctx, "mode", None) or ""
        return mode.lower() == "react"

    async def handle(self, ctx: AgentContext, req: AgentRequest) -> None:
        summary_agent = SummaryAgent(ctx)
        prompt = ctx.agent_system_prompt or self._prompt
        react_agent = ReActAgentImp(ctx, prompt, self._max_steps)

        result = await react_agent.run(ctx.query)

        evidence: List[str] = list(react_agent.evidence)
        if not react_agent.final_answer and react_agent.current_step >= react_agent.maxSteps:
            evidence.append("达到最大迭代次数，停止推理。")

        final_answer: Optional[str] = react_agent.final_answer or (result if isinstance(result, str) and result else None)
        if final_answer and not any("最终答案" in item for item in evidence):
            evidence.append(f"最终答案：{final_answer}")

        try:
            await summary_agent.summarize(ctx.query, [], evidence)
        except Exception:
            logger.exception("React summary agent failed for request %s", ctx.requestId)
            raise
