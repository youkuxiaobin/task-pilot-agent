from __future__ import annotations

from brain.core.agents.base_agent import BaseAgent
from brain.core.context import AgentContext
from llm.types import LLMMessage


class FakeMemory:
    def __init__(self):
        self.messages = []

    def add_message(self, **kwargs):
        self.messages.append(kwargs)
        return kwargs.get("trace_id") or "trace"


class FakeAgent(BaseAgent):
    async def step(self):
        return None


def make_context(agent_memory):
    return AgentContext(
        requestId="memory-policy-trace",
        sessionId="memory-policy-session",
        user_id="user-1",
        agent_id="agent-1",
        run_id="conversation-1",
        query="hello",
        task=None,
        printer=None,
        toolCollection=None,
        dateInfo="2026-05-30",
        agent_memory=agent_memory,
    )


def test_base_agent_respects_explicit_memory_write_scope():
    denied_memory = FakeMemory()
    denied_agent = FakeAgent(
        name="fake",
        description="fake",
        config=None,
        context=make_context({"write": []}),
        systemPrompt="",
        maxSteps=1,
    )
    denied_agent.memory = denied_memory

    denied_agent.add_message(LLMMessage(role="user", content="do not persist"))

    assert denied_memory.messages == []

    allowed_memory = FakeMemory()
    allowed_agent = FakeAgent(
        name="fake",
        description="fake",
        config=None,
        context=make_context({"write": ["task_history"]}),
        systemPrompt="",
        maxSteps=1,
    )
    allowed_agent.memory = allowed_memory

    allowed_agent.add_message(LLMMessage(role="user", content="persist"))

    assert allowed_memory.messages[0]["content"] == "persist"
    assert allowed_memory.messages[0]["trace_id"] == "memory-policy-trace"
