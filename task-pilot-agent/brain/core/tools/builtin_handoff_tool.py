from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Optional

from brain.core.tools.base import BaseTool

if TYPE_CHECKING:
    from brain.core.context import AgentContext


HandoffStarter = Callable[["AgentContext", str, str, Dict[str, Any]], Awaitable[Dict[str, Any]]]


class BuiltinHandoffTool(BaseTool):
    """Create a child task for another registered agent."""

    def __init__(self, context: Optional["AgentContext"], starter: HandoffStarter) -> None:
        super().__init__(
            name="builtin:handoff",
            description=(
                "Delegate a task to another allowed agent. Use this when the current task "
                "needs a specialist agent configured in this agent's handoffs.allowed list."
            ),
        )
        self.full_name = self.name
        self.context = context
        self._starter = starter
        self.input_schema = {
            "type": "object",
            "properties": {
                "target_agent_id": {
                    "type": "string",
                    "description": "Target agent id from the current agent's allowed handoffs.",
                },
                "task": {
                    "type": "string",
                    "description": "Task text to send to the target agent.",
                },
                "mode": {
                    "type": "string",
                    "description": "Optional target run mode.",
                },
                "outputStyle": {
                    "type": "string",
                    "description": "Optional target output style.",
                },
            },
            "required": ["target_agent_id", "task"],
        }

    def to_params(self) -> Dict[str, Any]:
        return self.input_schema

    async def execute(self, input_obj: Dict[str, Any]) -> str:
        if self.context is None:
            raise ValueError("handoff context is missing")
        target_agent_id = str(input_obj.get("target_agent_id") or input_obj.get("agent_id") or "").strip()
        task = str(input_obj.get("task") or input_obj.get("query") or "").strip()
        if not target_agent_id:
            raise ValueError("target_agent_id is required")
        if not task:
            raise ValueError("task is required")
        payload = await self._starter(self.context, target_agent_id, task, input_obj)
        return json.dumps(payload, ensure_ascii=False)
