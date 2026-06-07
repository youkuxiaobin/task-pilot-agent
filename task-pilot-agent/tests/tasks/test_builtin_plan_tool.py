from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from brain.core.tools.builtin_plan_tool import BuiltinPlanTool
from brain.core.tools.builtin_handoff_tool import BuiltinHandoffTool
from brain.core.tools.builtin_request_input_tool import BuiltinRequestInputTool
from brain.core.tools.builtin_todo_tool import BuiltinTodoTool
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
    assert [event["message_type"] for event in printer.events] == ["plan", "plan_created"]
    assert printer.events[-1]["message"]["eventType"] == "plan_created"
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
                "evidence": [
                    {
                        "tool": "mcp_local:web_search",
                        "summary": "Found two source pages",
                        "url": "https://example.test/source",
                    },
                    "artifact:report-1",
                ],
            }
        )
    )

    payload = json.loads(result)
    assert payload["message"] == "计划步骤已更新"
    assert payload["plan"]["step_status"] == ["completed", "not_started"]
    assert payload["plan"]["notes"] == ["found sources", ""]
    assert payload["plan"]["evidence"][0] == [
        {
            "tool": "mcp_local:web_search",
            "summary": "Found two source pages",
            "url": "https://example.test/source",
        },
        {"summary": "artifact:report-1"},
    ]
    assert printer.events[-1]["message_type"] == "plan_step_completed"
    assert printer.events[-1]["message"]["command"] == "mark_step"
    assert printer.events[-1]["message"]["stepIndex"] == 1
    assert printer.events[-1]["message"]["stepStatus"] == "completed"
    assert printer.events[-1]["message"]["step"] == "Search"
    assert printer.events[-1]["message"]["evidence"] == payload["plan"]["evidence"]
    assert printer.events[-1]["message"]["stepEvidence"] == payload["plan"]["evidence"][0]


def test_builtin_plan_tool_can_read_add_and_skip_steps():
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
    read_result = asyncio.run(
        tool.execute(
            {
                "summary": "read current plan",
                "command": "get_plan",
                "rationale": {"decision": "check current plan"},
            }
        )
    )
    add_result = asyncio.run(
        tool.execute(
            {
                "summary": "add review step",
                "command": "add_step",
                "rationale": {"decision": "review is needed"},
                "step": "Review sources",
                "position": 2,
                "note": "added after search",
            }
        )
    )
    skip_result = asyncio.run(
        tool.execute(
            {
                "summary": "skip write",
                "command": "skip_step",
                "rationale": {"decision": "user only needs sources"},
                "step_index": 3,
                "note": "not needed for this run",
                "evidence": ["user requested sources only"],
            }
        )
    )

    read_payload = json.loads(read_result)
    add_payload = json.loads(add_result)
    skip_payload = json.loads(skip_result)
    assert read_payload["message"] == "当前计划已读取"
    assert add_payload["message"] == "计划步骤已新增"
    assert add_payload["plan"]["steps"] == ["Search", "Review sources", "Write"]
    assert add_payload["plan"]["notes"] == ["", "added after search", ""]
    assert skip_payload["message"] == "计划步骤已跳过"
    assert skip_payload["plan"]["step_status"] == ["not_started", "not_started", "skipped"]
    assert skip_payload["plan"]["notes"][2] == "not needed for this run"
    assert skip_payload["plan"]["evidence"][2] == [{"summary": "user requested sources only"}]
    assert [event["message_type"] for event in printer.events] == [
        "plan",
        "plan_created",
        "plan",
        "plan_updated",
        "plan",
        "plan_updated",
        "plan",
        "plan_step_updated",
    ]
    assert printer.events[-1]["message_type"] == "plan_step_updated"
    assert printer.events[-1]["message"]["stepIndex"] == 3
    assert printer.events[-1]["message"]["stepStatus"] == "skipped"


def test_builtin_plan_tool_is_visible_as_openai_tool_when_allowed():
    collection = ToolCollection()
    collection.set_allowed_tool_patterns(["builtin:plan_tool"])
    collection.add_tool(BuiltinPlanTool())

    specs = collection.to_openai_tools()

    assert len(specs) == 1
    assert specs[0]["function"]["name"] == "builtin-plan_tool"
    assert specs[0]["function"]["parameters"]["properties"]["command"]["enum"] == [
        "create",
        "continue",
        "get_plan",
        "update",
        "add_step",
        "mark_step",
        "skip_step",
        "finish",
    ]
    assert specs[0]["function"]["parameters"]["properties"]["status"]["enum"] == [
        "running",
        "completed",
        "failed",
        "waiting_input",
        "skipped",
    ]


def test_builtin_todo_tool_emits_todo_list_event():
    printer = FakePrinter()
    tool = BuiltinTodoTool(SimpleNamespace(printer=printer))

    result = asyncio.run(
        tool.execute(
            {
                "summary": "working through a plan",
                "items": [
                    {"title": "Search sources", "status": "completed"},
                    {"title": "Write answer", "status": "running", "detail": "Draft concise response"},
                ],
            }
        )
    )

    payload = json.loads(result)
    assert payload["message"] == "TODO list updated"
    assert payload["todoList"]["count"] == 2
    assert payload["todoList"]["currentIndex"] == 1
    assert payload["todoList"]["items"][0]["status"] == "completed"
    assert payload["todoList"]["items"][1]["detail"] == "Draft concise response"
    assert printer.events[-1]["message_type"] == "todo_list_updated"
    assert printer.events[-1]["message"]["eventType"] == "todo_list_updated"
    assert printer.events[-1]["message"]["todos"] == payload["todoList"]["items"]


def test_builtin_todo_tool_is_visible_as_openai_tool_when_allowed():
    collection = ToolCollection()
    collection.set_allowed_tool_patterns(["builtin:set_todo_list"])
    collection.add_tool(BuiltinTodoTool())

    specs = collection.to_openai_tools()

    assert len(specs) == 1
    assert specs[0]["function"]["name"] == "builtin-set_todo_list"
    assert specs[0]["function"]["parameters"]["required"] == ["items"]
    assert "current_index" in specs[0]["function"]["parameters"]["properties"]


def test_builtin_handoff_tool_is_visible_as_openai_tool_when_allowed():
    async def fake_starter(_ctx, _target_agent_id, _task, _options):
        return {"taskId": "child-task"}

    collection = ToolCollection()
    collection.set_allowed_tool_patterns(["builtin:handoff"])
    collection.add_tool(BuiltinHandoffTool(SimpleNamespace(), fake_starter))

    specs = collection.to_openai_tools()

    assert len(specs) == 1
    assert specs[0]["function"]["name"] == "builtin-handoff"
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
    tool_collection = ToolCollection(agentContext=ctx)
    plan_tool = BuiltinPlanTool(ctx)
    tool_collection.add_tool(plan_tool)
    ctx.toolCollection = tool_collection
    asyncio.run(
        plan_tool.execute(
            {
                "command": "create",
                "title": "Need Input Plan",
                "steps": ["Collect account id", "Continue work"],
            }
        )
    )
    asyncio.run(
        plan_tool.execute(
            {
                "command": "mark_step",
                "step_index": 1,
                "status": "running",
                "note": "checking input",
            }
        )
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
    plan_waiting_event = next(
        event
        for event in printer.events
        if event["message_type"] == "plan_step_updated"
        and event["message"].get("stepStatus") == "waiting_input"
    )
    assert plan_waiting_event["message"]["stepIndex"] == 1
    assert plan_waiting_event["message"]["step_status"] == ["waiting_input", "not_started"]
    assert plan_waiting_event["message"]["stepEvidence"][0]["tool"] == "builtin:request_input"
    assert plan_waiting_event["message"]["stepEvidence"][0]["reason"] == "缺少账号"


def test_builtin_request_input_tool_is_visible_as_openai_tool_when_allowed():
    collection = ToolCollection()
    collection.set_allowed_tool_patterns(["builtin:request_input"])
    collection.add_tool(BuiltinRequestInputTool(SimpleNamespace()))

    specs = collection.to_openai_tools()

    assert len(specs) == 1
    assert specs[0]["function"]["name"] == "builtin-request_input"
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


def test_project_default_agent_config_allows_builtin_plan_tool():
    from brain.core.agent_registry import AgentRegistry

    agents_root = Path(__file__).resolve().parents[3] / "config" / "agents"
    registry = AgentRegistry(agents_root)
    agent = registry.get("task-pilot-agent")
    supervisor = registry.get("supervisor_agent")

    assert agent is not None
    assert agent.allows_tool("builtin:plan_tool")
    assert supervisor is not None
    assert supervisor.allows_tool("builtin:plan_tool")
    for configured_agent in (agent, supervisor):
        plan_tool = next(tool for tool in configured_agent.tools if tool.name == "builtin:plan_tool")
        assert plan_tool.input_schema["properties"]["status"]["enum"] == [
            "running",
            "completed",
            "failed",
            "waiting_input",
            "skipped",
        ]
        assert plan_tool.input_schema["properties"]["command"]["enum"] == [
            "create",
            "continue",
            "get_plan",
            "update",
            "add_step",
            "mark_step",
            "skip_step",
            "finish",
        ]
        assert "step" in plan_tool.input_schema["properties"]
        assert "position" in plan_tool.input_schema["properties"]
        assert "evidence" in plan_tool.input_schema["properties"]
