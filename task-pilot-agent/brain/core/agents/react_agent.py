from __future__ import annotations

from typing import Optional

from brain.core.agents.base_agent  import AgentState, BaseAgent
from llm.types import LLMMessage


class ReActAgent(BaseAgent):
    """Implements the think-act loop shared by planning and executor agents."""

    async def think(self) -> Optional[LLMMessage]:
        raise NotImplementedError

    async def act(self, thought: Optional[LLMMessage]) -> Optional[str]:
        raise NotImplementedError

    async def step(self) -> Optional[str]:
        thought = await self.think()
        if thought is None:
            self.set_state(AgentState.FINISHED)
            return None
        return await self.act(thought)
