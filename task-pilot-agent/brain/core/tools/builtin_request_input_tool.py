from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, Optional

from brain.core.tools.base import BaseTool

if TYPE_CHECKING:
    from brain.core.context import AgentContext


class BuiltinRequestInputTool(BaseTool):
    """Pause the current task and request more information from the user."""

    def __init__(self, context: Optional["AgentContext"]) -> None:
        super().__init__(
            name="builtin:request_input",
            description=(
                "Ask the user for missing information and pause the current task in waiting_input "
                "status. Use this when continuing would require guessing."
            ),
        )
        self.full_name = self.name
        self.context = context
        self.input_schema = {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Clear question or instruction shown to the user.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why the task cannot safely continue without this input.",
                },
            },
            "required": ["prompt"],
        }

    def to_params(self) -> Dict[str, Any]:
        return self.input_schema

    async def execute(self, input_obj: Dict[str, Any]) -> str:
        if self.context is None:
            raise ValueError("request input context is missing")
        task_id = getattr(self.context, "task_id", None)
        if not task_id:
            raise ValueError("task_id is required to request user input")
        prompt = str(input_obj.get("prompt") or input_obj.get("question") or "").strip()
        if not prompt:
            raise ValueError("prompt is required")

        reason = str(input_obj.get("reason") or "").strip()
        metadata = {
            "agentId": getattr(self.context, "agent_id", ""),
            "requestId": getattr(self.context, "requestId", ""),
        }
        if reason:
            metadata["reason"] = reason

        from brain.core.tasks import TaskStore

        event = TaskStore().request_user_input(
            task_id,
            prompt,
            trace_id=getattr(self.context, "requestId", None),
            source="agent",
            metadata=metadata,
        )
        self.context.waiting_for_input = True
        self.context.waiting_input_prompt = prompt
        self._emit_waiting_task(prompt)
        return json.dumps(
            {
                "status": "waiting_input",
                "taskId": task_id,
                "prompt": prompt,
                "eventId": getattr(event, "id", None),
            },
            ensure_ascii=False,
        )

    def _emit_waiting_task(self, prompt: str) -> None:
        printer = getattr(self.context, "printer", None) if self.context else None
        if printer is None:
            return
        printer.send(
            None,
            "task",
            {
                "task": f"等待补充输入：{prompt}",
                "status": "waiting_input",
                "prompt": prompt,
                "taskId": getattr(self.context, "task_id", None),
            },
            None,
            False,
        )
