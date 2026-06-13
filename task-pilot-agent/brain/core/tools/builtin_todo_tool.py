from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from brain.core.run_events import RunEventType
from brain.core.tools.base import BaseTool

if TYPE_CHECKING:
    from brain.core.context import AgentContext


TODO_STATUS_MAP = {
    "": "not_started",
    "todo": "not_started",
    "pending": "not_started",
    "not_started": "not_started",
    "running": "running",
    "in_progress": "running",
    "doing": "running",
    "waiting": "waiting",
    "waiting_input": "waiting",
    "blocked": "blocked",
    "completed": "completed",
    "complete": "completed",
    "done": "completed",
    "failed": "failed",
    "error": "failed",
}


class BuiltinTodoTool(BaseTool):
    """Publish a lightweight todo list for UI progress display."""

    def __init__(self, context: Optional["AgentContext"] = None) -> None:
        super().__init__(
            name="builtin:set_todo_list",
            description=(
                "Update the short todo list shown to the user. Use this to project the current "
                "plan or immediate next steps into a concise progress checklist."
            ),
        )
        self.full_name = self.name
        self.context = context
        self.input_schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "Todo items in user-visible order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["not_started", "running", "waiting", "blocked", "completed", "failed"],
                            },
                            "detail": {"type": "string"},
                        },
                        "required": ["title"],
                    },
                },
                "summary": {"type": "string", "description": "Short status summary."},
                "current_index": {
                    "type": "integer",
                    "description": "Zero-based index of the current todo item.",
                },
            },
            "required": ["items"],
        }

    def to_params(self) -> Dict[str, Any]:
        return self.input_schema

    async def execute(self, input_obj: Dict[str, Any]) -> str:
        raw_items = input_obj.get("items") or input_obj.get("todos") or input_obj.get("todo_list")
        items = self._normalize_items(raw_items)
        if not items:
            raise ValueError("items must contain at least one todo")

        current_index = self._normalize_current_index(input_obj, items)
        payload = {
            "eventType": RunEventType.TODO_LIST_UPDATED,
            "summary": str(input_obj.get("summary") or "").strip(),
            "items": items,
            "todos": items,
            "currentIndex": current_index,
            "count": len(items),
        }
        self._emit_todo_event(payload)
        return json.dumps(
            {
                "message": "TODO list updated",
                "todoList": payload,
            },
            ensure_ascii=False,
        )

    def _normalize_items(self, raw_items: Any) -> List[Dict[str, Any]]:
        if not isinstance(raw_items, list):
            return []
        items: List[Dict[str, Any]] = []
        for index, raw_item in enumerate(raw_items):
            if isinstance(raw_item, str):
                title = raw_item.strip()
                raw_status = ""
                detail = ""
                item_id = f"todo-{index + 1}"
            elif isinstance(raw_item, dict):
                title = str(
                    raw_item.get("title")
                    or raw_item.get("task")
                    or raw_item.get("content")
                    or raw_item.get("text")
                    or ""
                ).strip()
                raw_status = str(raw_item.get("status") or "").strip().lower()
                detail = str(raw_item.get("detail") or raw_item.get("description") or raw_item.get("note") or "").strip()
                item_id = str(raw_item.get("id") or f"todo-{index + 1}").strip()
            else:
                continue
            if not title:
                continue
            status = TODO_STATUS_MAP.get(raw_status, raw_status or "not_started")
            if status not in {"not_started", "running", "waiting", "blocked", "completed", "failed"}:
                status = "not_started"
            items.append(
                {
                    "id": item_id or f"todo-{index + 1}",
                    "title": title,
                    "status": status,
                    "detail": detail,
                }
            )
        return items

    def _normalize_current_index(self, input_obj: Dict[str, Any], items: List[Dict[str, Any]]) -> int:
        raw_index = input_obj.get("current_index", input_obj.get("currentIndex"))
        try:
            current_index = int(raw_index)
        except (TypeError, ValueError):
            current_index = -1
        if 0 <= current_index < len(items):
            return current_index
        for index, item in enumerate(items):
            if item.get("status") in {"running", "waiting", "blocked"}:
                return index
        return 0

    def _emit_todo_event(self, payload: Dict[str, Any]) -> None:
        printer = getattr(self.context, "printer", None) if self.context else None
        if printer is None:
            return
        printer.send(None, RunEventType.TODO_LIST_UPDATED, payload, None, True)
