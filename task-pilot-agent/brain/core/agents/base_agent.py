from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Iterable, List, Optional
from langfuse import observe

from llm.manager  import mgr as llm_mgr
from config.config  import AgentSettings
from memory.memory_mgr import MemoryManager,  memory_manager
from brain.core.context import AgentContext
from llm.types import LLMMessage, RoleType
from memory.message_manager import Message

class AgentState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    FINISHED = "finished"
    ERROR = "error"


class BaseAgent:
    """Shared building blocks used by every agent variant."""

    def __init__(
        self,
        name: str,
        description: str,
        config: AgentSettings,
        context: AgentContext,
        systemPrompt: str,
        maxSteps: int,
        llm_manager=None,
    ) -> None:
        self.name = name
        self.description = description
        self.llm = llm_manager or llm_mgr
        self.config = config
        self.state = AgentState.IDLE
        self.memory = memory_manager
        self.logger = logging.getLogger(name)
        self.context = context
        self.system_prompt = systemPrompt
        self.maxSteps = maxSteps
        self.current_step = 0
        #self.messages = []
        self.current_msg = None

    @observe(name="agent_run")
    async def run(self, initial_user_input: str) -> Optional[str]:
        self.state = AgentState.RUNNING
        if initial_user_input:
            self.add_message(LLMMessage(role=RoleType.USER.value, content=initial_user_input))
        
        self.current_msg = initial_user_input
        self.current_step = 0
        result: Optional[str] = None
        try:
            while self.state == AgentState.RUNNING and self.current_step < self.maxSteps:
                self.current_step += 1
                result = await self.step()  # type: ignore[assignment]
            if self.state == AgentState.RUNNING:
                self.state = AgentState.FINISHED
        except Exception:  # pragma: no cover - defensive guard
            self.state = AgentState.ERROR
            self.logger.exception("%s encountered an error during step %s", self.name, self.current_step)
            raise
        return result

    async def step(self) -> Optional[str]:
        raise NotImplementedError

    def add_message(self, message: LLMMessage) -> None:
        #self.memory.add_memory([message], self.context.user_id, self.context.agent_id, self.context.run_id)
        self.memory.add_message(user_id=self.context.user_id, 
                                conversation_id=self.context.run_id, 
                                agent_id=self.context.agent_id, 
                                role=message.role, 
                                content=message.content, 
                                type_name=self.name,
                                trace_id=self.context.requestId)

    #def last_message(self) -> Optional[LLMMessage]:    
        #return self.memory.get_memory(self.context.user_id, self.context.agent_id, self.context.run_id, limit=1)
    #    return self.context.messages[-1]

    def get_messages(self, type_name: Optional[str] = None) -> List[LLMMessage]:
        messages = self.memory.get_messages(user_id=self.context.user_id, 
                                    agent_id=self.context.agent_id, 
                                    conversation_id=self.context.run_id, 
                                    trace_id=self.context.requestId, type_name=type_name)
        return [LLMMessage(role=message.role, content=message.content) for message in messages]
    def set_state(self, state: AgentState) -> None:
        self.state = state

    def _format_files(self) -> str:
        if not self.context.productFiles:
            return ""
        parts = []
        for item in self.context.productFiles:
            file_path = item.ossUrl
            description = item.description
            parts.append(f"- {file_path}: {description}")
        return "\n".join(parts)


    def _format_conversation_history(self, history: Optional[Iterable[Any]] = None) -> str:
        source = history if history is not None else self.context.serialize_messages()
        blocks: List[str] = []
        for item in (source or []):
            if isinstance(item, dict):
                role = str(item.get("role") or "")
                content = str(item.get("content") or "")
            else:
                role = str(getattr(item, "role", "") or "")
                content = str(getattr(item, "content", "") or "")
            content = content.strip()
            if not content:
                continue
            role = role.strip() or "unknown"
            blocks.append(f"<message><role>{role}</role><content>{content}</content></message>")
        return "\n".join(blocks)

