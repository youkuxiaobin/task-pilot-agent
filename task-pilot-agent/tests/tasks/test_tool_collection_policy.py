from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

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
