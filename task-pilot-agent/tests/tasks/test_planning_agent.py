from __future__ import annotations

import json

from brain.core.agents.planning_agent import PlanningAgent
from brain.core.context import AgentContext
from brain.core.tools.collection import ToolCollection


class FakePrinter:
    def send(self, *_args, **_kwargs) -> None:
        return None


def test_planning_agent_renders_query_in_system_prompt():
    query = "只回复一个字：好"
    ctx = AgentContext(
        requestId="planning-render-test",
        sessionId="planning-render-session",
        user_id="user-planning",
        agent_id="agent-planning",
        run_id="conversation-planning",
        query=query,
        task=None,
        printer=FakePrinter(),
        toolCollection=ToolCollection(),
        dateInfo="2026-05-30",
    )
    agent = PlanningAgent(ctx)
    agent.current_msg = json.dumps({"query": query}, ensure_ascii=False)

    prompt = agent._render_system_prompt()

    assert "<query>" in prompt
    assert query in prompt
