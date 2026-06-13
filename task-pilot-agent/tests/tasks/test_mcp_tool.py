from __future__ import annotations

from types import SimpleNamespace

import pytest

from brain.core.tools.mcp_tool import MCPTool, MCPToolFetcher
from tools.aggre_mcp_market.models import Protocol, ToolInfo
from tools.aggre_mcp_market.service import runtime as registry_runtime


def make_mcp_tool(input_schema):
    return MCPTool(
        full_name="mcp_local:code_interpreter",
        name="code_interpreter",
        description="Code interpreter",
        input_schema=input_schema,
        output_schema={},
        server_url="http://mcp.example.test",
        protocol="streamable-http",
        tool_prefix="mcp_local",
        context=SimpleNamespace(requestId="request-1", task_id="task-1", work_dir="/tmp/task-work"),
        mcp_market_url="http://market.example.test",
    )


def test_mcp_tool_injects_declared_runtime_arguments_without_mutating_input():
    tool = make_mcp_tool(
        {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "request_id": {"type": "string"},
                "task_id": {"type": "string"},
                "work_dir": {"type": "string"},
            },
            "required": ["task", "request_id"],
        }
    )
    input_obj = {"task": "analyze data"}

    arguments = tool._with_runtime_arguments(input_obj)

    assert arguments == {
        "task": "analyze data",
        "request_id": "request-1",
        "task_id": "task-1",
        "work_dir": "/tmp/task-work",
    }
    assert input_obj == {"task": "analyze data"}


def test_mcp_tool_does_not_inject_undeclared_or_existing_runtime_arguments():
    tool = make_mcp_tool(
        {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "request_id": {"type": "string"},
            },
        }
    )

    arguments = tool._with_runtime_arguments({"task": "analyze data", "request_id": "provided"})

    assert arguments == {"task": "analyze data", "request_id": "provided"}
    assert "task_id" not in arguments
    assert "work_dir" not in arguments


@pytest.mark.asyncio
async def test_mcp_tool_fetcher_uses_in_process_registry_and_executes_without_http(monkeypatch):
    class FakeRegistry:
        def __init__(self):
            self.list_called = False
            self.called_with = None

        def list_tools(self):
            self.list_called = True
            return [
                ToolInfo(
                    full_name="mcp_local-shell_exec",
                    name="shell_exec",
                    description="Shell",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "command": {"type": "string"},
                            "request_id": {"type": "string"},
                        },
                    },
                    output_schema={},
                    server_url="http://mcp.example.test/mcp",
                    protocol=Protocol.STREAMABLE_HTTP,
                    tool_prefix="mcp_local",
                    server_id="mcp_local",
                    risk_level="high",
                    requires_approval=True,
                    metadata={"serverId": "mcp_local"},
                )
            ]

        async def call_tool_stream(self, full_name, arguments):
            self.called_with = (full_name, arguments)

            async def events():
                yield {"method": "notifications/message", "params": {"data": "running"}}
                yield {"result": {"ok": True, "arguments": arguments}}

            return events()

    class FailingSession:
        def __init__(self, *args, **kwargs):
            raise AssertionError("HTTP should not be used when registry is available")

    registry = FakeRegistry()
    registry_runtime.set_registry(registry)
    monkeypatch.setattr("aiohttp.ClientSession", FailingSession)
    try:
        fetcher = MCPToolFetcher(
            SimpleNamespace(requestId="request-1"),
            mcp_market_url="http://market.example.test",
        )
        tools = await fetcher.fetch_tools()
        assert registry.list_called is True
        assert len(tools) == 1
        tool = tools[0]
        assert tool.registry is registry
        assert tool.risk_level == "high"
        assert tool.requires_approval is True
        assert tool.metadata == {"serverId": "mcp_local"}

        result = await tool.execute({"command": "pwd"})

        assert registry.called_with == (
            "mcp_local-shell_exec",
            {"command": "pwd", "request_id": "request-1"},
        )
        assert '"ok": true' in result
        assert '"request_id": "request-1"' in result
    finally:
        registry_runtime.set_registry(None)
