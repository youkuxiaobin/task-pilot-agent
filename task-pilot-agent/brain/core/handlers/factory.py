from __future__ import annotations
from typing import List

from .base import AgentHandlerService
from brain.core.context import AgentContext
from brain.models.requests import AgentRequest


class AgentHandlerFactory:
    def __init__(self, handlers: List[AgentHandlerService]):
        self.handlers = handlers

    def get_handler(self, ctx: AgentContext, req: AgentRequest) -> AgentHandlerService | None:
        for h in self.handlers:
            if h.support(ctx, req):
                return h
        return None

