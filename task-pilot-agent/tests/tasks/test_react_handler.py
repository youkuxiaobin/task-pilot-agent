from __future__ import annotations

import asyncio
import importlib
from typing import Any, List, Optional

from brain.core.tools.base import BaseTool


class FakePrinter:
    def __init__(self) -> None:
        self.events: List[dict[str, Any]] = []

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


class FakeReactAgent:
    instances: List["FakeReactAgent"] = []

    def __init__(self, ctx, _prompt, max_steps):
        self.ctx = ctx
        self.maxSteps = max_steps
        self.current_step = 0
        self.history: List[dict[str, Any]] = []
        self.evidence: List[str] = []
        self.final_answer = "react answer"
        FakeReactAgent.instances.append(self)

    async def run(self, query: str) -> str:
        self.current_step = 1
        self.evidence.append(f"handled: {query}")
        return self.final_answer


class FakeSummaryAgent:
    def __init__(self, ctx) -> None:
        self.ctx = ctx

    async def summarize(self, _query, _plan_steps, _evidence):
        self.ctx.printer.send("summary-1", "result", "summary answer", None, True)
        return "summary answer"


class FakeSearchTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(name="mcp_local-web_search", description="Search pages")
        self.full_name = self.name
        self.input_schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }

    async def execute(self, input_obj):
        return {
            "query": input_obj.get("query"),
            "results": [
                {
                    "title": "TaskPilot",
                    "url": "https://example.test/taskpilot",
                    "content": "rendered page body",
                }
            ],
        }


def _ctx(query: str):
    from brain.core.context import AgentContext
    from brain.core.tools.builtin_plan_tool import BuiltinPlanTool
    from brain.core.tools.collection import ToolCollection

    printer = FakePrinter()
    ctx = AgentContext(
        requestId="react-request",
        sessionId="react-session",
        user_id="user-1",
        agent_id="react-agent",
        run_id="react-run",
        query=query,
        task=None,
        printer=printer,
        toolCollection=None,
        dateInfo="2026-06-06",
        mode="react",
        task_id="react-run",
        language="ch",
    )
    tool_collection = ToolCollection(agentContext=ctx)
    tool_collection.add_tool(BuiltinPlanTool(ctx))
    ctx.toolCollection = tool_collection
    return ctx, printer


def _request(query: str):
    from brain.models.requests import AgentMessage, GptQueryReq

    return GptQueryReq(
        trace_id="react-run",
        user_id="user-1",
        agent_id="react-agent",
        conversation_id="react-session",
        mode="react",
        messages=[AgentMessage(role="user", content=query)],
    )


def test_react_handler_auto_creates_plan_for_complex_request(monkeypatch):
    import brain.core.handlers.react as react_module

    react_module = importlib.reload(react_module)
    FakeReactAgent.instances = []
    monkeypatch.setattr(react_module, "ReActAgentImp", FakeReactAgent)
    monkeypatch.setattr(react_module, "SummaryAgent", FakeSummaryAgent)

    query = "请完整调研 web_search 的设计，分析如何拆开 deepsearch，并给出实现和测试方案"
    ctx, printer = _ctx(query)

    asyncio.run(react_module.ReactHandler().handle(ctx, _request(query)))

    message_types = [event["message_type"] for event in printer.events]
    assert "plan_created" in message_types
    assert "plan_step_started" in message_types
    assert message_types.index("plan_created") < message_types.index("plan_step_started")
    assert message_types.index("plan_step_started") < message_types.index("result")

    agent = FakeReactAgent.instances[0]
    assert agent.history[0]["action"] == "builtin:plan_tool"
    assert agent.history[0]["input"]["command"] == "create"
    assert "step_status" in agent.history[0]["observation"]


def test_react_handler_does_not_force_plan_for_simple_request(monkeypatch):
    import brain.core.handlers.react as react_module

    react_module = importlib.reload(react_module)
    FakeReactAgent.instances = []
    monkeypatch.setattr(react_module, "ReActAgentImp", FakeReactAgent)
    monkeypatch.setattr(react_module, "SummaryAgent", FakeSummaryAgent)

    query = "你好"
    ctx, printer = _ctx(query)

    asyncio.run(react_module.ReactHandler().handle(ctx, _request(query)))

    message_types = [event["message_type"] for event in printer.events]
    assert "plan_created" not in message_types
    assert "plan_step_started" not in message_types
    assert FakeReactAgent.instances[0].history == []


def test_react_agent_syncs_tool_result_into_running_plan_step():
    from brain.core.agents.ReActAgentImp import ReActAgentImp

    ctx, printer = _ctx("请调研 TaskPilot")
    ctx.toolCollection.add_tool(FakeSearchTool())
    plan_tool = ctx.toolCollection.get_tool("builtin:plan_tool")
    asyncio.run(
        plan_tool.execute(
            {
                "command": "create",
                "title": "Search Plan",
                "steps": ["Search sources", "Write answer"],
            }
        )
    )
    asyncio.run(
        plan_tool.execute(
            {
                "command": "mark_step",
                "step_index": 1,
                "status": "running",
                "note": "searching",
            }
        )
    )
    agent = ReActAgentImp(ctx, "{remaining_iterations}\n{dialogue_history}\n{tool_history}\n{available_tools}", 3)

    assert agent._has_tool("mcp_local:web_search") is True
    observation = asyncio.run(agent._invoke_tool("mcp_local:web_search", {"query": "TaskPilot"}))

    assert "TaskPilot" in observation
    message_types = [event["message_type"] for event in printer.events]
    assert "plan_step_completed" in message_types
    assert "plan_step_started" in message_types
    completed_event = next(
        event
        for event in printer.events
        if event["message_type"] == "plan_step_completed"
        and event["message"].get("stepIndex") == 1
    )
    next_started_event = next(
        event
        for event in printer.events
        if event["message_type"] == "plan_step_started"
        and event["message"].get("stepIndex") == 2
    )

    assert completed_event["message"]["stepStatus"] == "completed"
    assert completed_event["message"]["stepEvidence"][0]["tool"] == "mcp_local-web_search"
    assert completed_event["message"]["stepEvidence"][0]["failed"] is False
    assert "TaskPilot" in completed_event["message"]["stepEvidence"][0]["summary"]
    assert next_started_event["message"]["stepStatus"] == "running"
