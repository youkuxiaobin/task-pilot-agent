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
                "Create, read, update, extend, skip, and finish a task plan. Use this when "
                "the task needs explicit steps or previous steps need adjustment."
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
        plan.update(self._plan_event_fields(input_obj))
        self._emit_plan(plan)
        self._emit_plan_event(self._plan_event_type(input_obj), plan)
        return json.dumps({"message": message, "plan": plan}, ensure_ascii=False)

    def _emit_plan(self, plan: Dict[str, Any]) -> None:
        printer = getattr(self.context, "printer", None) if self.context else None
        if printer is None:
            return
        printer.send(None, "plan", plan, None, True)

    def _emit_plan_event(self, event_type: str, plan: Dict[str, Any]) -> None:
        printer = getattr(self.context, "printer", None) if self.context else None
        if printer is None:
            return
        printer.send(None, event_type, plan, None, True)

    def _plan_event_type(self, input_obj: Dict[str, Any]) -> str:
        command = str(input_obj.get("command") or self._plan_tool.current_command or "")
        if command == "create":
            return "plan_created"
        if command == "update":
            return "plan_updated"
        if command == "get_plan":
            return "plan_updated"
        if command == "add_step":
            return "plan_updated"
        if command == "finish":
            return "plan_completed"
        if command == "skip_step":
            return "plan_step_updated"
        if command == "mark_step":
            status = str(input_obj.get("status") or "")
            if status == "running":
                return "plan_step_started"
            if status == "completed":
                return "plan_step_completed"
            if status == "failed":
                return "plan_step_failed"
            return "plan_step_updated"
        return "plan_updated"

    def _plan_event_fields(self, input_obj: Dict[str, Any]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "eventType": self._plan_event_type(input_obj),
        }
        if "summary" in input_obj:
            payload["summary"] = input_obj.get("summary")
        if "rationale" in input_obj:
            payload["rationale"] = input_obj.get("rationale")
        if "current_step" in input_obj:
            payload["currentStep"] = input_obj.get("current_step")
        if "finish_reason" in input_obj:
            payload["finishReason"] = input_obj.get("finish_reason")
        step_index = input_obj.get("step_index")
        if isinstance(step_index, int):
            payload["stepIndex"] = step_index
            plan = self._plan_tool.plan_dict() or {}
            plan_steps = plan.get("steps") if isinstance(plan, dict) else []
            if isinstance(plan_steps, list) and 1 <= step_index <= len(plan_steps):
                payload["step"] = plan_steps[step_index - 1]
            plan_evidence = plan.get("evidence") if isinstance(plan, dict) else []
            if isinstance(plan_evidence, list) and 1 <= step_index <= len(plan_evidence):
                payload["stepEvidence"] = plan_evidence[step_index - 1]
        if "status" in input_obj:
            payload["stepStatus"] = input_obj.get("status")
        if "note" in input_obj:
            payload["note"] = input_obj.get("note")
        return payload

    def plan_dict(self) -> Optional[Dict[str, Any]]:
        return self._plan_tool.plan_dict()
