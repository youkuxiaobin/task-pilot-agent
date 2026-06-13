from __future__ import annotations

import asyncio
from types import SimpleNamespace

from brain.core.context import AgentContext
from brain.core.task_memory_context import (
    agent_memory_read_limits,
    load_task_memory_context,
    memory_context_status_text,
    summarize_context_result,
)


class FakeMemoryManager:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = []

    def get_search_config(self):
        return {"memory_enabled": True, "rag_enabled": True}

    def unified_search(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("memory unavailable")
        return {
            "memory_results": [
                {
                    "id": "memory-1",
                    "content": "remember public context",
                    "metadata": {"api_key": "hidden", "source_file": "notes.md"},
                    "score": 0.91234,
                }
            ]
            if kwargs["memory_limit"]
            else [],
            "rag_results": [
                {
                    "id": "doc-1",
                    "content": "knowledge context",
                    "metadata": {"title": "demo"},
                    "score": 0.81234,
                }
            ]
            if kwargs["rag_limit"]
            else [],
            "warnings": [],
        }


def make_context(agent_memory=None) -> AgentContext:
    return AgentContext(
        requestId="memory-context-request",
        sessionId="memory-context-session",
        user_id="user-1",
        agent_id="agent-1",
        run_id="run-1",
        query="hello",
        task=None,
        printer=None,
        toolCollection=None,
        dateInfo="2026-06-13",
        agent_memory=agent_memory or {},
    )


def test_task_memory_context_respects_agent_read_scopes():
    assert agent_memory_read_limits(make_context({})) == (5, 5)
    assert agent_memory_read_limits(make_context({"read": []})) == (0, 0)
    assert agent_memory_read_limits(make_context({"read": ["task_history"]})) == (5, 0)
    assert agent_memory_read_limits(make_context({"read": ["knowledge_base"]})) == (0, 5)
    assert agent_memory_read_limits(make_context({"read": ["all"]})) == (5, 5)


def test_task_memory_context_loads_and_sanitizes_results():
    memory_manager = FakeMemoryManager()
    ctx = make_context({"read": ["task_history"]})

    payload = asyncio.run(load_task_memory_context(ctx, "hello", memory_manager=memory_manager))

    assert memory_manager.calls[-1]["memory_limit"] == 5
    assert memory_manager.calls[-1]["rag_limit"] == 0
    assert payload["memoryEnabled"] is True
    assert payload["ragEnabled"] is False
    assert payload["memoryCount"] == 1
    assert payload["ragCount"] == 0
    assert payload["memoryResults"][0]["snippet"] == "remember public context"
    assert payload["memoryResults"][0]["score"] == 0.9123
    assert payload["memoryResults"][0]["metadata"]["api_key"] == "***"
    assert ctx.memory_context == payload


def test_task_memory_context_handles_disabled_empty_and_degraded_states():
    disabled_manager = FakeMemoryManager()
    disabled_ctx = make_context({"read": []})
    disabled_payload = asyncio.run(load_task_memory_context(disabled_ctx, "hello", memory_manager=disabled_manager))
    assert disabled_manager.calls == []
    assert disabled_payload["memoryEnabled"] is False
    assert disabled_payload["ragEnabled"] is False
    assert memory_context_status_text(disabled_payload) == "上下文检索已按 Agent 配置关闭"

    empty_payload = asyncio.run(load_task_memory_context(make_context(), "   ", memory_manager=FakeMemoryManager()))
    assert empty_payload["warningCount"] == 1
    assert empty_payload["warnings"][0]["reason"] == "empty_query"

    logger = SimpleNamespace(warnings=[])
    logger.warning = lambda *args: logger.warnings.append(args)
    degraded_payload = asyncio.run(
        load_task_memory_context(make_context(), "hello", memory_manager=FakeMemoryManager(fail=True), logger=logger)
    )
    assert degraded_payload["warningCount"] == 1
    assert degraded_payload["warnings"][0]["reason"] == "RuntimeError"
    assert logger.warnings


def test_task_memory_context_summarizes_non_dict_results():
    payload = summarize_context_result("plain result", "memory")

    assert payload == {
        "id": "",
        "source": "memory",
        "score": None,
        "metadata": {},
        "snippet": "plain result",
    }
