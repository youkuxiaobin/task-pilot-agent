from __future__ import annotations

from shlex import join
from typing import Any, Dict, List, Optional

from .plan_state import PlanState


class PlanFunctionTool:
    """Internal tool exposed via function-calls for planning."""

    def __init__(self) -> None:
        self._plan: Optional[PlanState] = None
        self.current_command: Optional[str] = None

    # 工具接口
    def getName(self) -> str:
        return "planning"

    def getDescription(self) -> str:
        return (
            "Create/maintain an executable plan via a deterministic state machine. "
            "Supported commands: create / continue / get_plan / update / add_step / mark_step / skip_step / finish. "
            "'create' starts a new plan - provide a title that explains the goal plus steps that describe the workflow. "
            "'update' adjusts the existing plan when it no longer matches the latest user question - explain why and supply the revised title and steps. "
            "'get_plan' reads the current plan without changing it. "
            "'add_step' inserts a new step when new work is discovered. "
            "'continue' keeps executing the current plan without structural changes. "
            "'mark_step' records one step as running, completed, failed, waiting_input, or skipped. "
            "'skip_step' skips one step while preserving why it was skipped. "
            "'finish' ends planning when the query is solved or no further tool work is needed; gather tool notes if available."
        )

    def toParams(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Concise reasoning summary (<=200 chars). No internal prompts or tool names.",
                    "maxLength": 200
                },
                "command": {
                    "type": "string",
                    "enum": ["create", "continue", "get_plan", "update", "add_step", "mark_step", "skip_step", "finish"],
                    "description": "Command to execute."
                },
                "rationale": {
                "type": "object",
                "description": "Why this command was chosen and how complex the task is.",
                "properties": {
                    "decision": {
                        "type": "string",
                        "description": "e.g., 'C1~C4 true -> continue' or 'U1 triggered -> update'."
                    },
                    "complexity_score": { "type": "integer", "minimum": 0, "maximum": 6 },
                    "evidence": { "type": "array", "items": { "type": "string" }, "maxItems": 5 }
                },
                "required": ["decision"]
                },
                "title": {
                    "type": "string",
                    "description": "Plan title (create or update)."
                },
                "steps": {
                    "type": "array",
                    "items": { "type": "string" },
                    "description": "List of steps as strings. Required for create/update; omit for continue/finish. Example: ['Step 1 ...', 'Step 2 ...']"
                },
                "step": {
                    "type": "string",
                    "description": "New step text for add_step."
                },
                "position": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional 1-based insert position for add_step. Defaults to appending after the last step."
                },
                "current_step": {
                    "type": "string",
                    "description": "The next step id or description to execute (e.g., 'S1' or 'Step 3 ...')."
                },
                "step_index": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "1-based step index to update when command is mark_step."
                },
                "status": {
                    "type": "string",
                    "enum": ["running", "completed", "failed", "waiting_input", "skipped"],
                    "description": "Step status for mark_step."
                },
                "note": {
                    "type": "string",
                    "description": "Optional result, error, or progress note for mark_step."
                },
                "evidence": {
                    "type": "array",
                    "description": (
                        "Optional evidence anchors for mark_step. Use tool results, source URLs, "
                        "artifact ids, or event ids that prove this step's status."
                    ),
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "summary": {"type": "string"},
                                    "tool": {"type": "string"},
                                    "url": {"type": "string"},
                                    "artifactId": {"type": "string"},
                                    "eventId": {"type": "string"},
                                },
                            },
                        ]
                    },
                    "maxItems": 10
                },
                "finish_reason": {
                    "type": "string",
                    "description": "Why planning can stop; cite concrete evidence anchors."
                }
            },
            "required": ["summary", "command", "rationale"],
            "oneOf": [
                {
                "title": "CREATE",
                "properties": { "command": { "const": "create" } },
                "required": ["title", "steps", "current_step"]
                },
                {
                "title": "CONTINUE",
                "properties": { "command": { "const": "continue" } },
                "required": ["current_step"]
                },
                {
                "title": "GET_PLAN",
                "properties": { "command": { "const": "get_plan" } }
                },
                {
                "title": "UPDATE",
                "properties": { "command": { "const": "update" } },
                "required": ["title", "steps", "current_step"]
                },
                {
                "title": "ADD_STEP",
                "properties": { "command": { "const": "add_step" } },
                "required": ["step"]
                },
                {
                "title": "MARK_STEP",
                "properties": { "command": { "const": "mark_step" } },
                "required": ["step_index", "status"]
                },
                {
                "title": "SKIP_STEP",
                "properties": { "command": { "const": "skip_step" } },
                "required": ["step_index"]
                },
                {
                "title": "FINISH",
                "properties": { "command": { "const": "finish" } },
                "required": ["finish_reason"]
                }
            ]
        }


    def to_openai_tool(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.getName(),
                "description": self.getDescription(),
                "parameters": self.toParams(),
            },
        }
    def to_str(self) -> str:
        steps = ";".join(self._plan.steps) if self._plan else ""
        try:
            from llm.manager import store as prompt_store
            template = prompt_store.get_prompt("plan_to_str")
        except Exception:
            template = "command={command}\nsteps={steps}"
        return template.format(
            command=self.current_command,
            steps=steps,
        )
    def execute(self, params: Dict[str, Any]) -> str:
        if not isinstance(params, dict):
            raise ValueError("Tool parameters must be dict")
        command = params.get("command")
        if command not in {"create", "continue", "get_plan", "update", "add_step", "mark_step", "skip_step", "finish"}:
            raise ValueError("command 必须是 create/continue/get_plan/update/add_step/mark_step/skip_step/finish 之一")
        if command == "create":
            return self._create(params)
        if command == "continue":
            return self._continue(params)
        if command == "get_plan":
            return self._get_plan()
        if command == "update":
            return self._update(params)
        if command == "add_step":
            return self._add_step(params)
        if command == "mark_step":
            return self._mark_step(params)
        if command == "skip_step":
            return self._skip_step(params)
        return self._finish()

    # --- command handlers -------------------------------------------------
    def _create(self, params: Dict[str, Any]) -> str:
        self.current_command = "create"

        title = params.get("title")
        steps = params.get("steps")
        if not title:
            raise ValueError("create 命令需要 title")
        if not steps or not isinstance(steps, list):
            raise ValueError("create 命令需要 steps 列表")
        if self._plan is not None:
            raise ValueError("当前已存在 plan，无法重复创建")
        self._plan = PlanState(title=title, steps=[str(step) for step in steps])
        return "计划已创建"

    def _update(self, params: Dict[str, Any]) -> str:
        self.current_command = "update"
        
        if self._plan is None:
            raise ValueError("尚未创建 plan，无法更新")
        title = params.get("title")
        steps = params.get("steps")
        list_steps: Optional[List[str]] = None
        if steps is not None:
            if not isinstance(steps, list):
                raise ValueError("steps 必须是字符串列表")
            list_steps = [str(step) for step in steps]
        self._plan.update(title, list_steps)
        return "计划已更新"

    def _continue(self, params: Dict[str, Any]) -> str:
        self.current_command = "continue"
        return "计划已继续执行"

    def _get_plan(self) -> str:
        self.current_command = "get_plan"
        if self._plan is None:
            return "目前还没有计划"
        return "当前计划已读取"

    def _add_step(self, params: Dict[str, Any]) -> str:
        self.current_command = "add_step"
        if self._plan is None:
            raise ValueError("尚未创建 plan，无法新增步骤")
        step = params.get("step")
        position = params.get("position")
        if position is not None and not isinstance(position, int):
            raise ValueError("position 必须是整数")
        note = params.get("note")
        evidence = self._normalize_evidence(params.get("evidence"))
        inserted_index = self._plan.add_step(
            str(step or ""),
            position=position,
            note=str(note) if note is not None else None,
            evidence=evidence,
        )
        params["step_index"] = inserted_index
        params["status"] = "not_started"
        return "计划步骤已新增"

    def _mark_step(self, params: Dict[str, Any]) -> str:
        self.current_command = "mark_step"
        if self._plan is None:
            raise ValueError("尚未创建 plan，无法标记步骤")
        step_index = params.get("step_index")
        if not isinstance(step_index, int):
            raise ValueError("mark_step 命令需要 step_index")
        status = str(params.get("status") or "")
        note = params.get("note")
        evidence = self._normalize_evidence(params.get("evidence"))
        self._plan.mark_step(
            step_index,
            status,
            str(note) if note is not None else None,
            evidence=evidence,
        )
        return "计划步骤已更新"

    def _skip_step(self, params: Dict[str, Any]) -> str:
        self.current_command = "skip_step"
        if self._plan is None:
            raise ValueError("尚未创建 plan，无法跳过步骤")
        step_index = params.get("step_index")
        if not isinstance(step_index, int):
            raise ValueError("skip_step 命令需要 step_index")
        note = params.get("note")
        evidence = self._normalize_evidence(params.get("evidence"))
        self._plan.skip_step(
            step_index,
            note=str(note) if note is not None else None,
            evidence=evidence,
        )
        params["status"] = "skipped"
        return "计划步骤已跳过"

    def _finish(self) -> str:
        self.current_command = "finish"
        
        if self._plan is None:
            self._plan = PlanState(title="临时计划", steps=[])
        self._plan.mark_finished()
        return "计划已标记为完成"

    # 提供外部访问
    def get_plan(self) -> Optional[PlanState]:
        return self._plan

    def format_plan(self) -> str:
        if self._plan is None:
            return "目前还没有Plan"
        return self._plan.format_plan()

    def plan_dict(self) -> Optional[Dict[str, Any]]:
        if self._plan is None:
            return None
        payload = self._plan.to_dict()
        payload["command"] = self.current_command
        return payload

    def _normalize_evidence(self, raw_evidence: Any) -> Optional[List[Dict[str, Any]]]:
        if raw_evidence is None:
            return None
        if not isinstance(raw_evidence, list):
            raise ValueError("evidence 必须是字符串或对象列表")
        normalized: List[Dict[str, Any]] = []
        for item in raw_evidence[:10]:
            if isinstance(item, dict):
                normalized.append({str(key): value for key, value in item.items() if value not in (None, "")})
            else:
                text = str(item).strip()
                if text:
                    normalized.append({"summary": text})
        return normalized
