from __future__ import annotations
from dataclasses import dataclass
from brain.core.context import AgentContext
from brain.models.requests import AgentRequest
from typing import Dict, List


@dataclass
class AgentHandlerService:
    def support(self, ctx: AgentContext, req: AgentRequest) -> bool:
        raise NotImplementedError

    async def handle(self, ctx: AgentContext, req: AgentRequest) -> None:
        raise NotImplementedError
    
    def _serialize_history(self, ctx: AgentContext) -> List[Dict[str, str]]:
        history: List[Dict[str, str]] = []
        for message in getattr(ctx, "messages", []) or []:
            content = getattr(message, "content", "") or ""
            if not content:
                continue
            role = getattr(message, "role", "") or ""
            history.append({"role": role, "content": content})
        return history

