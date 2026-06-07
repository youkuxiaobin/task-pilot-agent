from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
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
        self.called = False
        self.input_schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }

    async def execute(self, input_obj):
        self.called = True
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


def test_react_handler_auto_creates_plan_for_financial_report_question(monkeypatch):
    import brain.core.handlers.react as react_module

    react_module = importlib.reload(react_module)
    FakeReactAgent.instances = []
    monkeypatch.setattr(react_module, "ReActAgentImp", FakeReactAgent)
    monkeypatch.setattr(react_module, "SummaryAgent", FakeSummaryAgent)

    query = "2025年阿里财报如何"
    ctx, printer = _ctx(query)

    asyncio.run(react_module.ReactHandler().handle(ctx, _request(query)))

    message_types = [event["message_type"] for event in printer.events]
    assert "plan_created" in message_types
    assert message_types.index("plan_created") < message_types.index("result")
    plan_created = next(event for event in printer.events if event["message_type"] == "plan_created")
    assert "财报" in plan_created["message"]["steps"][0]
    assert FakeReactAgent.instances[0].history[0]["action"] == "builtin:plan_tool"


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


def test_react_handler_advances_final_plan_step_before_summary():
    import brain.core.handlers.react as react_module

    ctx, printer = _ctx("整理奇安信财报")
    plan_tool = ctx.toolCollection.get_tool("builtin:plan_tool")
    asyncio.run(
        plan_tool.execute(
            {
                "command": "create",
                "title": "财报信息收集",
                "steps": ["搜索公开信息", "读取详细内容", "整理关键数据"],
            }
        )
    )
    asyncio.run(
        plan_tool.execute(
            {
                "command": "mark_step",
                "step_index": 1,
                "status": "completed",
                "note": "搜索完成",
            }
        )
    )
    asyncio.run(
        plan_tool.execute(
            {
                "command": "mark_step",
                "step_index": 2,
                "status": "running",
                "note": "正在读取",
            }
        )
    )

    summary_step_index = asyncio.run(react_module.ReactHandler()._mark_summary_step_running(ctx))

    assert summary_step_index == 3
    assert plan_tool.plan_dict()["step_status"] == ["completed", "completed", "running"]

    asyncio.run(
        react_module.ReactHandler()._mark_summary_step_terminal(
            ctx,
            summary_step_index,
            "completed",
            "总结输出完成",
        )
    )

    assert plan_tool.plan_dict()["step_status"] == ["completed", "completed", "completed"]
    message_types = [event["message_type"] for event in printer.events]
    assert message_types.count("plan_step_completed") >= 2
    assert message_types.count("plan_step_started") >= 2


def test_react_agent_stops_before_repeating_same_search_call():
    from brain.core.agents.ReActAgentImp import ReActAgentImp
    from brain.core.agents.base_agent import AgentState
    from llm.types import LLMMessage, RoleType

    ctx, printer = _ctx("请调研 TaskPilot")
    search_tool = FakeSearchTool()
    ctx.toolCollection.add_tool(search_tool)
    agent = ReActAgentImp(ctx, "{remaining_iterations}\n{dialogue_history}\n{tool_history}\n{available_tools}", 3)
    agent.current_step = 2
    agent.history.append(
        {
            "step": 1,
            "thought": "需要搜索",
            "action": "mcp_local:web_search",
            "input": {"query": "TaskPilot"},
            "observation": '{"query":"TaskPilot","results":[{"title":"TaskPilot"}]}',
        }
    )
    agent._last_decision = {
        "thought": "再次搜索同一个关键词",
        "action": "mcp_local-web_search",
        "input": {"query": "TaskPilot"},
        "answer": "",
    }

    result = asyncio.run(agent.act(LLMMessage(role=RoleType.ASSISTANT.value, content="")))

    assert result is None
    assert agent.state == AgentState.FINISHED
    assert search_tool.called is False
    assert all(event["message_type"] != "tool_call" for event in printer.events)
    assert "重复工具调用" in agent.history[-1]["observation"]


def test_summary_agent_streams_each_chunk_and_caps_tokens():
    from brain.core.agents.summary_agent import SummaryAgent

    class FakeSummaryLLM:
        def __init__(self) -> None:
            self.kwargs = {}

        async def stream_generate_async(self, _messages, chunk_callback, **kwargs):
            self.kwargs = kwargs
            for _ in range(250):
                chunk_callback("字")
            return SimpleNamespace(text="字" * 250)

    ctx, printer = _ctx("生成一个简要报告")
    ctx.agent_memory = {"write": []}
    agent = SummaryAgent(ctx)
    fake_llm = FakeSummaryLLM()
    agent.llm = fake_llm

    result = asyncio.run(agent.summarize(ctx.query, [], ["证据"]))

    result_events = [event for event in printer.events if event["message_type"] == "result"]
    assert result == "字" * 250
    assert fake_llm.kwargs["max_tokens"] == 2200
    assert len(result_events) == 250
    assert "".join(event["message"] for event in result_events) == result
