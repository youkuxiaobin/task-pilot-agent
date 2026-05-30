from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from brain.core.tools.base import BaseTool
from brain.core.tools.collection import ToolCollection


class DummyTool(BaseTool):
    def __init__(self, name: str) -> None:
        super().__init__(name=name, description=f"{name} description")
        self.full_name = name
        self.input_schema = {"type": "object", "properties": {}, "required": []}
        self.called = False

    async def execute(self, input_obj: Dict[str, Any]) -> str:
        self.called = True
        return f"ok:{self.name}:{input_obj.get('value')}"


class FailingTool(DummyTool):
    async def execute(self, input_obj: Dict[str, Any]) -> str:
        self.called = True
        raise RuntimeError("boom")


class SlowTool(DummyTool):
    async def execute(self, input_obj: Dict[str, Any]) -> str:
        self.called = True
        await asyncio.sleep(float(input_obj.get("sleep", 0.05)))
        return "slow-ok"


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
                "message_id": message_id,
                "message_type": message_type,
                "message": message,
                "digital_employee": digital_employee,
                "is_final": is_final,
            }
        )


def test_tool_collection_blocks_tools_outside_allowed_patterns():
    collection = ToolCollection()
    collection.set_allowed_tool_patterns(["mcp_local:deepsearch", "mcp_world:*"])

    assert collection.add_tool(DummyTool("mcp_local:deepsearch")) is True
    assert collection.add_tool(DummyTool("mcp_world:browser")) is True
    assert collection.add_tool(DummyTool("mcp_local:code_interpreter")) is False

    assert sorted(collection.tool_map) == ["mcp_local:deepsearch", "mcp_world:browser"]
    assert collection.blocked_tools == ["mcp_local:code_interpreter"]
    assert collection.get_tool("mcp_local:code_interpreter") is None
    assert "mcp_local:deepsearch" in collection.to_str()
    assert "mcp_world:browser" in collection.to_str()
    assert "mcp_local:code_interpreter" not in collection.to_str()

    openai_tool_names = [
        item["function"]["name"]
        for item in collection.to_openai_tools()
    ]
    assert openai_tool_names == ["mcp_local:deepsearch", "mcp_world:browser"]


def test_tool_collection_checker_overrides_patterns_and_blocks_add():
    collection = ToolCollection()
    collection.set_allowed_tool_patterns(["*"])
    collection.set_tool_allowed_checker(lambda name: name != "mcp_local:code_interpreter")

    assert collection.add_tool(DummyTool("mcp_local:deepsearch")) is True
    assert collection.add_tool(DummyTool("mcp_local:code_interpreter")) is False
    assert sorted(collection.tool_map) == ["mcp_local:deepsearch"]
    assert collection.blocked_tools == ["mcp_local:code_interpreter"]


def test_tool_collection_empty_pattern_list_blocks_all_tools():
    collection = ToolCollection()
    collection.set_allowed_tool_patterns([])

    assert collection.add_tool(DummyTool("mcp_local:deepsearch")) is False
    assert collection.tool_map == {}
    assert collection.blocked_tools == ["mcp_local:deepsearch"]


def test_tool_collection_refuses_manual_bypass_at_execution_time():
    collection = ToolCollection()
    collection.set_allowed_tool_patterns(["mcp_local:deepsearch"])
    blocked_tool = DummyTool("mcp_local:code_interpreter")
    collection.tool_map[blocked_tool.name] = blocked_tool
    printer = FakePrinter()
    collection.agentContext = SimpleNamespace(printer=printer)

    result = asyncio.run(collection.execute("mcp_local:code_interpreter", {"value": "secret"}))

    assert result == "tool `mcp_local:code_interpreter` is not allowed for this agent"
    assert blocked_tool.called is False
    assert printer.events[-1]["message_type"] == "notifications"
    assert printer.events[-1]["message"]["tool"] == "mcp_local:code_interpreter"


def test_tool_collection_allows_matching_tool_execution_and_emits_call():
    collection = ToolCollection()
    collection.set_allowed_tool_patterns(["mcp_local:deepsearch"])
    allowed_tool = DummyTool("mcp_local:deepsearch")
    collection.add_tool(allowed_tool)
    printer = FakePrinter()
    collection.agentContext = SimpleNamespace(printer=printer)

    result = asyncio.run(collection.execute("mcp_local:deepsearch", {"value": "query"}))

    assert result == "ok:mcp_local:deepsearch:query"
    assert allowed_tool.called is True
    assert printer.events[-1]["message_type"] == "tool_call"
    assert printer.events[-1]["message"]["tool"] == "mcp_local:deepsearch"


def test_tool_collection_records_execution_metadata_for_success_and_failure():
    collection = ToolCollection()
    allowed_tool = DummyTool("mcp_local:deepsearch")
    collection.add_tool(allowed_tool)

    result = asyncio.run(collection.execute("mcp_local:deepsearch", {"value": "query"}))

    assert result == "ok:mcp_local:deepsearch:query"
    assert collection.last_execution is not None
    assert collection.last_execution["tool"] == "mcp_local:deepsearch"
    assert collection.last_execution["failed"] is False
    assert collection.last_execution["durationMs"] >= 0
    assert collection.last_execution["argumentsSummary"] == '{"value": "query"}'
    assert collection.last_execution["resultSummary"] == "ok:mcp_local:deepsearch:query"

    failing_tool = FailingTool("mcp_local:broken")
    collection.add_tool(failing_tool)
    printer = FakePrinter()
    collection.agentContext = SimpleNamespace(printer=printer)

    with pytest.raises(RuntimeError):
        asyncio.run(collection.execute("mcp_local:broken", {"value": "query"}))

    assert collection.last_execution is not None
    assert collection.last_execution["tool"] == "mcp_local:broken"
    assert collection.last_execution["failed"] is True
    assert collection.last_execution["durationMs"] >= 0
    assert collection.last_execution["argumentsSummary"] == '{"value": "query"}'
    assert collection.last_execution["error"] == "boom"
    assert printer.events[-1]["message_type"] == "tool_result"
    assert printer.events[-1]["message"]["tool"] == "mcp_local:broken"
    assert printer.events[-1]["message"]["failed"] is True
    assert printer.events[-1]["message"]["error"] == "boom"


def test_tool_collection_records_audit_context_in_events_and_metadata():
    collection = ToolCollection()
    collection.set_allowed_tool_patterns(["mcp_local:deepsearch"])
    allowed_tool = DummyTool("mcp_local:deepsearch")
    collection.add_tool(allowed_tool)
    printer = FakePrinter()
    collection.agentContext = SimpleNamespace(
        printer=printer,
        user_id="user-1",
        agent_id="agent-1",
        task_id="task-1",
        requestId="request-1",
        run_id="run-1",
        sessionId="session-1",
        run_environment="sandbox",
        work_dir="/tmp/task-work",
    )

    result = asyncio.run(collection.execute("mcp_local:deepsearch", {"value": "query"}))

    assert result == "ok:mcp_local:deepsearch:query"
    assert collection.last_execution is not None
    assert printer.events[-1]["message_type"] == "tool_call"
    assert printer.events[-1]["message"]["argumentsSummary"] == '{"value": "query"}'
    for key, expected in {
        "userId": "user-1",
        "agentId": "agent-1",
        "taskId": "task-1",
        "requestId": "request-1",
        "runId": "run-1",
        "sessionId": "session-1",
        "runEnvironment": "sandbox",
        "workDir": "/tmp/task-work",
    }.items():
        assert collection.last_execution[key] == expected
        assert printer.events[-1]["message"][key] == expected
    assert collection.last_execution["argumentsSummary"] == '{"value": "query"}'
    assert collection.last_execution["startedAt"]
    assert collection.last_execution["completedAt"]


def test_tool_collection_blocks_sandbox_paths_outside_task_workspace(tmp_path):
    work_dir = tmp_path / "task-work"
    work_dir.mkdir()
    outside_path = tmp_path / "outside.txt"
    collection = ToolCollection()
    tool = DummyTool("mcp_local:file_writer")
    collection.add_tool(tool)
    printer = FakePrinter()
    collection.agentContext = SimpleNamespace(
        printer=printer,
        run_environment="sandbox",
        work_dir=str(work_dir),
        task_id="task-1",
    )

    result = asyncio.run(collection.execute("mcp_local:file_writer", {"output_path": str(outside_path)}))

    assert "must stay inside task workspace" in result
    assert tool.called is False
    assert collection.last_execution is not None
    assert collection.last_execution["failed"] is True
    assert collection.last_execution["error"] == result
    assert printer.events[-1]["message_type"] == "tool_call"


def test_tool_collection_allows_sandbox_paths_inside_task_workspace(tmp_path):
    work_dir = tmp_path / "task-work"
    work_dir.mkdir()
    collection = ToolCollection()
    tool = DummyTool("mcp_local:file_writer")
    collection.add_tool(tool)
    collection.agentContext = SimpleNamespace(
        printer=FakePrinter(),
        run_environment="sandbox",
        work_dir=str(work_dir),
        task_id="task-1",
    )

    result = asyncio.run(collection.execute("mcp_local:file_writer", {"output_path": "nested/result.txt"}))

    assert result == "ok:mcp_local:file_writer:None"
    assert tool.called is True
    assert collection.last_execution is not None
    assert collection.last_execution["failed"] is False


def test_agent_tool_result_metadata_includes_runtime_boundary():
    from brain.core.agents.ReActAgentImp import ReActAgentImp
    from brain.core.agents.executor_agent import ExecutorAgent

    tool_collection = SimpleNamespace(
        last_execution={
            "tool": "mcp_local:deepsearch",
            "durationMs": 12,
            "failed": False,
            "argumentsSummary": '{"value": "query"}',
            "resultSummary": "ok",
            "startedAt": "2026-05-30T00:00:00+00:00",
            "completedAt": "2026-05-30T00:00:01+00:00",
            "userId": "user-1",
            "agentId": "agent-1",
            "taskId": "task-1",
            "requestId": "request-1",
            "runId": "run-1",
            "sessionId": "session-1",
            "runEnvironment": "sandbox",
            "workDir": "/tmp/task-work",
        }
    )
    context = SimpleNamespace(toolCollection=tool_collection)

    for cls in (ReActAgentImp, ExecutorAgent):
        agent = object.__new__(cls)
        agent.context = context
        metadata = agent._tool_execution_metadata("mcp_local:deepsearch")

        assert metadata["runEnvironment"] == "sandbox"
        assert metadata["workDir"] == "/tmp/task-work"
        assert metadata["argumentsSummary"] == '{"value": "query"}'


def test_tool_collection_enforces_configured_tool_timeout():
    collection = ToolCollection()
    slow_tool = SlowTool("mcp_local:slow")
    collection.add_tool(slow_tool)
    collection.set_tool_timeout_patterns({"mcp_local:slow": 0.01})
    printer = FakePrinter()
    collection.agentContext = SimpleNamespace(printer=printer)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(collection.execute("mcp_local:slow", {"sleep": 0.2}))

    assert slow_tool.called is True
    assert collection.last_execution is not None
    assert collection.last_execution["tool"] == "mcp_local:slow"
    assert collection.last_execution["failed"] is True
    assert collection.last_execution["error"] == "tool `mcp_local:slow` timed out"
    assert printer.events[-1]["message_type"] == "tool_result"
    assert printer.events[-1]["message"]["failed"] is True
    assert printer.events[-1]["message"]["error"] == "tool `mcp_local:slow` timed out"
