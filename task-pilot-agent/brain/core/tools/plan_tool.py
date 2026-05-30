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
            "Supported commands: create / continue / update / finish. "
            "'create' starts a new plan - provide a title that explains the goal plus steps that describe the workflow. "
            "'update' adjusts the existing plan when it no longer matches the latest user question - explain why and supply the revised title and steps. "
            "'continue' keeps executing the current plan without structural changes. "
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
                    "enum": ["create", "continue", "update", "finish"],
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
                "current_step": {
                    "type": "string",
                    "description": "The next step id or description to execute (e.g., 'S1' or 'Step 3 ...')."
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
                "title": "UPDATE",
                "properties": { "command": { "const": "update" } },
                "required": ["title", "steps", "current_step"]
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
        if command not in {"create", "continue", "update", "finish"}:
            raise ValueError("command 必须是 create/continue/update/finish 之一")
        if command == "create":
            return self._create(params)
        if command == "continue":
            return self._continue(params)
        if command == "update":
            return self._update(params)
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
