from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, Optional

from brain.core.tools.base import BaseTool
from brain.core.tools.plan_tool import PlanFunctionTool

if TYPE_CHECKING:
    from brain.core.context import AgentContext


class BuiltinPlanTool(BaseTool):
    """Expose the deterministic plan state machine as a normal agent tool."""

    def __init__(self, context: Optional["AgentContext"] = None) -> None:
        super().__init__(
            name="builtin:plan_tool",
            description=(
                "Create, update, continue, and finish a task plan. Use this when the task "
                "needs explicit steps or when previous steps need to be adjusted."
            ),
        )
        self.full_name = self.name
        self.context = context
        self._plan_tool = PlanFunctionTool()
        self.input_schema = self._plan_tool.toParams()

    def to_params(self) -> Dict[str, Any]:
        return self.input_schema

    async def execute(self, input_obj: Dict[str, Any]) -> str:
        message = self._plan_tool.execute(input_obj)
        plan = self._plan_tool.plan_dict() or {
            "title": "",
            "steps": [],
            "step_status": [],
            "notes": [],
            "command": self._plan_tool.current_command,
        }
        plan["tool_result"] = message
        self._emit_plan(plan)
        return json.dumps({"message": message, "plan": plan}, ensure_ascii=False)

    def _emit_plan(self, plan: Dict[str, Any]) -> None:
        printer = getattr(self.context, "printer", None) if self.context else None
        if printer is None:
            return
        printer.send(None, "plan", plan, None, True)

    def plan_dict(self) -> Optional[Dict[str, Any]]:
        return self._plan_tool.plan_dict()
