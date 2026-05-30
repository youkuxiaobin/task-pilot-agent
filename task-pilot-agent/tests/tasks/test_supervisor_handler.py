from __future__ import annotations

import asyncio
import importlib
import textwrap
from pathlib import Path
from typing import Any, List, Optional

import pytest


@pytest.fixture()
def supervisor_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_DB_URL", f"sqlite:///{tmp_path / 'tasks.db'}")
    monkeypatch.setenv("TASK_WORKSPACE_ROOT", str(tmp_path / "workspaces"))

    import file.db_engine as db_engine

    db_engine.get_engine.cache_clear()

    import brain.core.tasks as tasks
    import brain.core.handlers.supervisor as supervisor

    tasks = importlib.reload(tasks)
    supervisor = importlib.reload(supervisor)
    yield tasks, supervisor

    db_engine.get_engine.cache_clear()


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
                "message_type": message_type,
                "message": message,
                "digital_employee": digital_employee,
                "is_final": is_final,
            }
        )


class FakeWorkerHandler:
    def __init__(self) -> None:
        self.calls: List[tuple[str, str, str]] = []

    def support(self, ctx, req) -> bool:
        return ctx.mode == "react" and req.mode == "react"

    async def handle(self, ctx, req) -> None:
        self.calls.append((ctx.agent_id, ctx.mode, ctx.agent_system_prompt))
        ctx.printer.send("result-1", "result", "worker done", None, True)


def test_supervisor_handler_selects_agent_rebuilds_tools_and_records_events(
    supervisor_modules,
    tmp_path,
):
    tasks, supervisor_module = supervisor_modules
    from brain.core.agent_registry import AgentRegistry
    from brain.core.context import AgentContext
    from brain.core.tools.collection import ToolCollection
    from brain.models.requests import AgentMessage, GptQueryReq

    agents_root = tmp_path / "agents"
    supervisor_dir = agents_root / "supervisor"
    worker_dir = agents_root / "research_agent"
    supervisor_dir.mkdir(parents=True)
    worker_dir.mkdir()
    (supervisor_dir / "agent.yaml").write_text(
        textwrap.dedent(
            """
            id: supervisor
            name: Supervisor
            type: supervisor
            mode: react
            handoffs:
              allowed:
                - research_agent
            """
        ).strip(),
        encoding="utf-8",
    )
    (worker_dir / "agent.yaml").write_text(
        textwrap.dedent(
            """
            id: research_agent
            name: Research Agent
            type: react_worker
            mode: react
            system_prompt: worker prompt
            description: Search and research public information.
            tools:
              - name: builtin:plan_tool
            """
        ).strip(),
        encoding="utf-8",
    )

    registry = AgentRegistry(agents_root)
    store = tasks.TaskStore()
    store.create_task(task_id="supervisor-task", trace_id="trace-supervisor", agent_id="supervisor")
    built_for: List[str] = []

    async def fake_builder(ctx):
        built_for.append(ctx.agent_id)
        return ToolCollection(agentContext=ctx)

    worker = FakeWorkerHandler()
    handler = supervisor_module.SupervisorHandler(registry, fake_builder, worker_handlers=[worker])
    printer = FakePrinter()
    ctx = AgentContext(
        requestId="trace-supervisor",
        sessionId="session-1",
        user_id="user-1",
        agent_id="supervisor",
        run_id="conversation-1",
        query="Search web sources for release notes.",
        task=None,
        printer=printer,
        toolCollection=ToolCollection(),
        dateInfo="2026-05-30",
        mode="react",
        task_id="supervisor-task",
        agent_system_prompt="supervisor prompt",
    )
    req = GptQueryReq(
        trace_id="trace-supervisor",
        user_id="user-1",
        agent_id="supervisor",
        conversation_id="conversation-1",
        mode="react",
        messages=[AgentMessage(role="user", content=ctx.query)],
    )

    assert handler.support(ctx, req)
    asyncio.run(handler.handle(ctx, req))

    assert built_for == ["research_agent"]
    assert worker.calls == [("research_agent", "react", "worker prompt")]
    assert ctx.agent_id == "supervisor"
    assert ctx.agent_system_prompt == "supervisor prompt"
    assert any("Supervisor 已选择 Agent" in event["message"] for event in printer.events)

    event_types = [event.event_type for event in store.list_events("supervisor-task")]
    assert "agent_selected" in event_types
    assert "agent_started" in event_types
    assert "agent_completed" in event_types
    selected_payload = tasks.serialize_event(
        next(event for event in store.list_events("supervisor-task") if event.event_type == "agent_selected")
    )["payload"]
    assert selected_payload["agentId"] == "research_agent"
