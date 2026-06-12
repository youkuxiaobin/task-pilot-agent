from __future__ import annotations

import asyncio
from types import SimpleNamespace

from brain.core.agent_registry import AgentConfig, AgentToolSpec
from brain.core.context import AgentContext
from brain.core.tools.gateway import ToolGateway


class FakeRegistry:
    def __init__(self, agent: AgentConfig | None) -> None:
        self.agent = agent

    def get(self, agent_id: str):
        return self.agent if self.agent and self.agent.id == agent_id else None


class FakeFetcher:
    def __init__(self, _ctx, _url: str) -> None:
        pass

    async def fetch_tools(self):
        return [
            SimpleNamespace(name="mcp_local:deepsearch", description="Search"),
            SimpleNamespace(name="mcp_local:code_interpreter", description="Code"),
        ]


async def fake_handoff_starter(*_args, **_kwargs):
    return {"taskId": "child-task"}


async def fake_should_use_plan(ctx, query=None, **_kwargs):
    return bool(getattr(ctx, "needs_plan_for_test", False))


def make_context(**overrides) -> AgentContext:
    values = {
        "requestId": "request-1",
        "sessionId": "session-1",
        "user_id": "user-1",
        "agent_id": "gateway-agent",
        "run_id": "run-1",
        "query": "",
        "task": None,
        "printer": None,
        "toolCollection": None,
        "dateInfo": "2026-05-30",
        "isStream": False,
    }
    values.update(overrides)
    return AgentContext(**values)


def test_tool_gateway_builds_policy_filtered_collection(monkeypatch):
    import brain.core.tools.gateway as gateway_module

    monkeypatch.delenv("APP_ALLOW_HIGH_RISK_TOOLS", raising=False)
    monkeypatch.delenv("ALLOW_HIGH_RISK_TOOLS", raising=False)
    monkeypatch.setattr(gateway_module, "should_use_plan", fake_should_use_plan)
    agent = AgentConfig(
        id="gateway-agent",
        name="Gateway Agent",
        tools=[
            AgentToolSpec(name="builtin:plan_tool"),
            AgentToolSpec(name="builtin:set_todo_list"),
            AgentToolSpec(name="builtin:handoff"),
            AgentToolSpec(name="mcp_local:deepsearch"),
            AgentToolSpec(name="mcp_local:code_interpreter", policy={"risk": "high"}),
        ],
    )
    gateway = ToolGateway(
        FakeRegistry(agent),
        mcp_market_url="http://mcp.example.test",
        handoff_starter=fake_handoff_starter,
        mcp_fetcher_cls=FakeFetcher,
    )

    simple_ctx = make_context(query="simple request")
    simple_ctx.needs_plan_for_test = False
    simple_collection = asyncio.run(gateway.build_collection(simple_ctx))

    assert "builtin:plan_tool" not in simple_collection.tool_map
    assert "builtin:set_todo_list" in simple_collection.tool_map

    complex_ctx = make_context(query="complex request")
    complex_ctx.needs_plan_for_test = True
    blocked_collection = asyncio.run(gateway.build_collection(complex_ctx))

    assert "builtin:plan_tool" in blocked_collection.tool_map
    assert "builtin:set_todo_list" in blocked_collection.tool_map
    assert "builtin:handoff" in blocked_collection.tool_map
    assert "mcp_local:deepsearch" in blocked_collection.tool_map
    assert "mcp_local:code_interpreter" not in blocked_collection.tool_map
    assert blocked_collection.blocked_tools == ["mcp_local:code_interpreter"]

    approved_collection = asyncio.run(
        gateway.build_collection(make_context(approved_tools=["mcp_local:code_interpreter"]))
    )

    assert "mcp_local:code_interpreter" in approved_collection.tool_map
    assert approved_collection.blocked_tools == []

    selected_collection = asyncio.run(
        gateway.build_collection(make_context(query="你好", selected_tools=["builtin:plan_tool"]))
    )

    assert "builtin:plan_tool" in selected_collection.tool_map


def test_tool_gateway_honors_per_request_selected_tools(monkeypatch):
    monkeypatch.delenv("APP_ALLOW_HIGH_RISK_TOOLS", raising=False)
    monkeypatch.delenv("ALLOW_HIGH_RISK_TOOLS", raising=False)
    agent = AgentConfig(
        id="gateway-agent",
        name="Gateway Agent",
        tools=[
            AgentToolSpec(name="mcp_local:deepsearch"),
            AgentToolSpec(name="mcp_local:code_interpreter", policy={"risk": "high"}),
        ],
    )
    gateway = ToolGateway(
        FakeRegistry(agent),
        mcp_market_url="http://mcp.example.test",
        mcp_fetcher_cls=FakeFetcher,
    )

    collection = asyncio.run(gateway.build_collection(make_context(selected_tools=["mcp_local:deepsearch"])))

    assert "mcp_local:deepsearch" in collection.tool_map
    assert "mcp_local:code_interpreter" not in collection.tool_map
    assert collection.blocked_tools == ["mcp_local:code_interpreter"]


def test_tool_gateway_requires_per_task_approval_even_when_high_risk_enabled(monkeypatch):
    monkeypatch.setenv("ALLOW_HIGH_RISK_TOOLS", "true")
    agent = AgentConfig(
        id="gateway-agent",
        name="Gateway Agent",
        permissions={"require_approval_for": ["high_risk_tools"]},
        tools=[
            AgentToolSpec(name="mcp_local:deepsearch"),
            AgentToolSpec(name="mcp_local:code_interpreter", policy={"risk": "high"}),
        ],
    )
    gateway = ToolGateway(
        FakeRegistry(agent),
        mcp_market_url="http://mcp.example.test",
        mcp_fetcher_cls=FakeFetcher,
    )

    blocked_collection = asyncio.run(gateway.build_collection(make_context()))
    approved_collection = asyncio.run(
        gateway.build_collection(make_context(approved_tools=["mcp_local:code_interpreter"]))
    )

    assert "mcp_local:deepsearch" in blocked_collection.tool_map
    assert "mcp_local:code_interpreter" not in blocked_collection.tool_map
    assert blocked_collection.blocked_tools == ["mcp_local:code_interpreter"]
    assert "mcp_local:code_interpreter" in approved_collection.tool_map
