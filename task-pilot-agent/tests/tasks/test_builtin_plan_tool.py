from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from brain.core.tools.builtin_plan_tool import BuiltinPlanTool
from brain.core.tools.builtin_handoff_tool import BuiltinHandoffTool
from brain.core.tools.builtin_request_input_tool import BuiltinRequestInputTool
from brain.core.tools.collection import ToolCollection


class FakePrinter:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def send(
        self,
        message_id: Optional[str],
        message_type: str,
        message: Any,
        digital_employee: Optional[str],
        is_final: bool,
    ) -> None:
        self.events.append(
            {
                "message_type": message_type,
                "message": message,
                "digital_employee": digital_employee,
                "is_final": is_final,
            }
        )


def test_builtin_plan_tool_creates_plan_and_emits_plan_event():
    printer = FakePrinter()
    tool = BuiltinPlanTool(SimpleNamespace(printer=printer))

    result = asyncio.run(
        tool.execute(
            {
                "summary": "make a plan",
                "command": "create",
                "rationale": {"decision": "task needs steps"},
                "title": "Demo Plan",
                "steps": ["Search", "Write"],
                "current_step": "Search",
            }
        )
    )

    payload = json.loads(result)
    assert payload["message"] == "计划已创建"
    assert payload["plan"]["title"] == "Demo Plan"
    assert payload["plan"]["steps"] == ["Search", "Write"]
    assert printer.events[-1]["message_type"] == "plan"
    assert printer.events[-1]["message"]["tool_result"] == "计划已创建"


def test_builtin_plan_tool_marks_step_status_and_emits_plan_event():
    printer = FakePrinter()
    tool = BuiltinPlanTool(SimpleNamespace(printer=printer))

    asyncio.run(
        tool.execute(
            {
                "summary": "make a plan",
                "command": "create",
                "rationale": {"decision": "task needs steps"},
                "title": "Demo Plan",
                "steps": ["Search", "Write"],
                "current_step": "Search",
            }
        )
    )
    result = asyncio.run(
        tool.execute(
            {
                "summary": "search done",
                "command": "mark_step",
                "rationale": {"decision": "step finished"},
                "step_index": 1,
                "status": "completed",
                "note": "found sources",
            }
        )
    )

    payload = json.loads(result)
    assert payload["message"] == "计划步骤已更新"
    assert payload["plan"]["step_status"] == ["completed", "not_started"]
    assert payload["plan"]["notes"] == ["found sources", ""]
    assert printer.events[-1]["message_type"] == "plan"
    assert printer.events[-1]["message"]["command"] == "mark_step"


def test_builtin_plan_tool_is_visible_as_openai_tool_when_allowed():
    collection = ToolCollection()
    collection.set_allowed_tool_patterns(["builtin:plan_tool"])
    collection.add_tool(BuiltinPlanTool())

    specs = collection.to_openai_tools()

    assert len(specs) == 1
    assert specs[0]["function"]["name"] == "builtin:plan_tool"
    assert specs[0]["function"]["parameters"]["properties"]["command"]["enum"] == [
        "create",
        "continue",
        "update",
        "mark_step",
        "finish",
    ]
    assert specs[0]["function"]["parameters"]["properties"]["status"]["enum"] == [
        "running",
        "completed",
        "failed",
    ]


def test_builtin_handoff_tool_is_visible_as_openai_tool_when_allowed():
    async def fake_starter(_ctx, _target_agent_id, _task, _options):
        return {"taskId": "child-task"}

    collection = ToolCollection()
    collection.set_allowed_tool_patterns(["builtin:handoff"])
    collection.add_tool(BuiltinHandoffTool(SimpleNamespace(), fake_starter))

    specs = collection.to_openai_tools()

    assert len(specs) == 1
    assert specs[0]["function"]["name"] == "builtin:handoff"
    assert "target_agent_id" in specs[0]["function"]["parameters"]["properties"]
    assert "task" in specs[0]["function"]["parameters"]["required"]


def test_builtin_request_input_tool_marks_task_waiting(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_DB_URL", f"sqlite:///{tmp_path / 'tasks.db'}")
    monkeypatch.setenv("TASK_WORKSPACE_ROOT", str(tmp_path / "workspaces"))

    import file.db_engine as db_engine
    from brain.core.tasks import AgentTaskStatus, TaskStore, serialize_event

    db_engine.get_engine.cache_clear()
    store = TaskStore()
    store.create_task(task_id="needs-input-task", trace_id="trace-input", agent_id="agent-1")
    printer = FakePrinter()
    ctx = SimpleNamespace(
        printer=printer,
        task_id="needs-input-task",
        requestId="trace-input",
        agent_id="agent-1",
    )
    tool = BuiltinRequestInputTool(ctx)

    result = asyncio.run(tool.execute({"prompt": "请补充账号 ID", "reason": "缺少账号"}))

    payload = json.loads(result)
    updated_task = store.get_task("needs-input-task")
    events = store.list_events("needs-input-task")
    waiting_payload = serialize_event(events[-1])["payload"]
    assert payload["status"] == "waiting_input"
    assert updated_task.status == AgentTaskStatus.WAITING_INPUT
    assert events[-1].event_type == "waiting_input"
    assert waiting_payload["prompt"] == "请补充账号 ID"
    assert waiting_payload["metadata"]["reason"] == "缺少账号"
    assert ctx.waiting_for_input is True
    assert ctx.waiting_input_prompt == "请补充账号 ID"
    assert printer.events[-1]["message_type"] == "task"


def test_builtin_request_input_tool_is_visible_as_openai_tool_when_allowed():
    collection = ToolCollection()
    collection.set_allowed_tool_patterns(["builtin:request_input"])
    collection.add_tool(BuiltinRequestInputTool(SimpleNamespace()))

    specs = collection.to_openai_tools()

    assert len(specs) == 1
    assert specs[0]["function"]["name"] == "builtin:request_input"
    assert specs[0]["function"]["parameters"]["required"] == ["prompt"]


def test_default_agent_config_does_not_allow_builtin_plan_tool(tmp_path):
    from brain.core.agent_registry import AgentRegistry

    agent_dir = tmp_path / "agents" / "default_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "system_prompt.md").write_text("prompt", encoding="utf-8")
    (agent_dir / "agent.yaml").write_text(
        "\n".join(
            [
                "id: default_agent",
                "name: Default",
                "system_prompt_file: system_prompt.md",
                "tools:",
                '  - name: "mcp_*:*"',
            ]
        ),
        encoding="utf-8",
    )

    registry = AgentRegistry(tmp_path / "agents")
    agent = registry.get("default_agent")

    assert agent is not None
    assert agent.allows_tool("mcp_local:deepsearch")
    assert not agent.allows_tool("builtin:plan_tool")
