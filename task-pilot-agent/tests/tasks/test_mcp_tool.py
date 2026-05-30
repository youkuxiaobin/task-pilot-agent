from __future__ import annotations

from types import SimpleNamespace

from brain.core.tools.mcp_tool import MCPTool


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
