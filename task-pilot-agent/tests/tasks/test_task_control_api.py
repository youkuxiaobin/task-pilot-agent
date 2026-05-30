from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture()
def app_modules(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    monkeypatch.setenv("FILE_DB_URL", f"sqlite:///{tmp_path / 'tasks.db'}")
    monkeypatch.setenv(
        "APP_CONFIG_FILE",
        str(Path(__file__).resolve().parents[3] / "config" / "config.yaml"),
    )

    import file.db_engine as db_engine

    db_engine.get_engine.cache_clear()

    import brain.core.tasks as tasks
    import brain.app as app

    tasks = importlib.reload(tasks)
    app = importlib.reload(app)
    yield app, tasks

    db_engine.get_engine.cache_clear()


def test_cancel_task_marks_running_task_cancelled(app_modules):
    app, tasks = app_modules
    store = tasks.TaskStore()
    store.create_task(
        task_id="cancel-me",
        trace_id="trace-cancel",
        user_id="user-1",
        agent_id="agent-1",
        input_text="hello",
    )
    store.update_status("cancel-me", tasks.AgentTaskStatus.RUNNING)

    payload = asyncio.run(app.cancel_agent_task("cancel-me"))

    assert payload["status"] == tasks.AgentTaskStatus.CANCELLED
    events = store.list_events("cancel-me")
    assert events[-1].event_type == "task_cancel_requested"


def test_retry_task_creates_new_task_from_saved_input(app_modules, monkeypatch):
    app, tasks = app_modules
    store = tasks.TaskStore()
    store.create_task(
        task_id="retry-me",
        trace_id="trace-retry",
        conversation_id="conversation-1",
        user_id="user-1",
        agent_id="agent-1",
        mode="react",
        output_style="markdown",
        input_text="retry this",
        metadata={
            "inputFiles": [
                {
                    "fileName": "source.csv",
                    "description": "input data",
                    "ossUrl": "https://files.example.test/source.csv",
                    "fileSize": 10,
                }
            ],
            "runEnvironment": "sandbox",
            "approvedTools": ["mcp_local:code_interpreter"],
        },
    )

    created_background = []

    def fake_create_task(coro):
        created_background.append(coro)

        class DoneTask:
            def done(self):
                return True

            def cancel(self):
                return None

        return DoneTask()

    monkeypatch.setattr(app.asyncio, "create_task", fake_create_task)
    payload = asyncio.run(app.retry_agent_task("retry-me"))

    assert payload["status"] == tasks.AgentTaskStatus.QUEUED
    assert payload["input"] == "retry this"
    assert payload["metadata"]["source"] == "retry"
    assert payload["metadata"]["parentTaskId"] == "retry-me"
    assert payload["metadata"]["runEnvironment"] == "sandbox"
    assert payload["metadata"]["approvedTools"] == ["mcp_local:code_interpreter"]
    assert payload["metadata"]["inputFiles"][0]["fileName"] == "source.csv"
    assert app._deserialize_file_items(payload["metadata"]["inputFiles"])[0].fileName == "source.csv"
    assert created_background
    created_background[0].close()

    parent_events = store.list_events("retry-me")
    assert parent_events[-1].event_type == "task_retry_requested"


def test_create_task_api_persists_task_and_starts_background_run(app_modules, monkeypatch):
    app, tasks = app_modules
    created_background = []

    def fake_create_task(coro):
        created_background.append(coro)

        class DoneTask:
            def done(self):
                return True

            def cancel(self):
                return None

        return DoneTask()

    monkeypatch.setattr(app.asyncio, "create_task", fake_create_task)
    payload = asyncio.run(
        app.create_agent_task(
            app.GptQueryReq(
                user_id="user-1",
                agent_id="task-pilot-agent",
                conversation_id="conversation-1",
                outputStyle="markdown",
                mode="react",
                run_environment="sandbox",
                approved_tools=["mcp_local:code_interpreter"],
                messages=[app.AgentMessage(role="user", content="run in background")],
            )
        )
    )

    assert payload["taskId"]
    assert payload["status"] == tasks.AgentTaskStatus.QUEUED
    assert payload["metadata"]["source"] == "api"
    assert payload["metadata"]["runEnvironment"] == "sandbox"
    assert payload["metadata"]["approvedTools"] == ["mcp_local:code_interpreter"]
    assert created_background
    background_req = created_background[0].cr_frame.f_locals["req"]
    assert background_req.trace_id == payload["taskId"]
    created_background[0].close()

    store = tasks.TaskStore()
    events = store.list_events(payload["taskId"])
    assert events[-1].event_type == "task_queued"
    assert tasks.serialize_event(events[-1])["payload"]["runEnvironment"] == "sandbox"
    assert tasks.serialize_event(events[-1])["payload"]["approvedTools"] == ["mcp_local:code_interpreter"]


def test_list_tasks_api_supports_time_duration_and_error_filters(app_modules, monkeypatch):
    app, tasks = app_modules
    timestamps = iter([1_000, 1_100, 1_800, 5_000, 5_100, 8_000])
    monkeypatch.setattr(tasks, "now_ms", lambda: next(timestamps))
    store = tasks.TaskStore()

    store.create_task(task_id="fast", trace_id="trace-fast", input_text="fast task")
    store.update_status("fast", tasks.AgentTaskStatus.RUNNING)
    store.update_status("fast", tasks.AgentTaskStatus.COMPLETED)
    store.create_task(task_id="broken", trace_id="trace-broken", input_text="broken task")
    store.update_status("broken", tasks.AgentTaskStatus.RUNNING)
    store.update_status("broken", tasks.AgentTaskStatus.FAILED, error_message="failed")

    payload = asyncio.run(
        app.list_agent_tasks(
            user_id=None,
            status=None,
            agent_id=None,
            agent_type=None,
            keyword=None,
            created_from=None,
            created_to=None,
            min_duration_ms=1_000,
            max_duration_ms=None,
            has_error=True,
            limit=50,
            offset=0,
        )
    )

    assert [item["taskId"] for item in payload["items"]] == ["broken"]
    assert payload["items"][0]["durationMs"] == 2_900
    assert payload["items"][0]["hasError"] is True


def test_list_tasks_api_filters_by_user(app_modules):
    app, tasks = app_modules
    store = tasks.TaskStore()
    store.create_task(task_id="user-task-a", trace_id="trace-a", user_id="user-a", input_text="alpha")
    store.create_task(task_id="user-task-b", trace_id="trace-b", user_id="user-b", input_text="beta")

    payload = asyncio.run(
        app.list_agent_tasks(
            user_id="user-a",
            status=None,
            agent_id=None,
            agent_type=None,
            keyword=None,
            created_from=None,
            created_to=None,
            min_duration_ms=None,
            max_duration_ms=None,
            has_error=None,
            limit=50,
            offset=0,
        )
    )

    assert [item["taskId"] for item in payload["items"]] == ["user-task-a"]


def test_list_tasks_api_filters_by_agent_type(app_modules, monkeypatch):
    app, tasks = app_modules
    store = tasks.TaskStore()
    store.create_task(task_id="task-supervisor", trace_id="trace-supervisor", agent_id="supervisor-agent", input_text="alpha")
    store.create_task(task_id="task-worker", trace_id="trace-worker", agent_id="worker-agent", input_text="beta")

    configs = {
        "supervisor-agent": app.AgentConfig(id="supervisor-agent", name="Supervisor", type="supervisor"),
        "worker-agent": app.AgentConfig(id="worker-agent", name="Worker", type="react_worker"),
    }

    monkeypatch.setattr(app.agentRegistry, "reload", lambda: None)
    monkeypatch.setattr(app.agentRegistry, "get", lambda agent_id: configs.get(agent_id))

    payload = asyncio.run(
        app.list_agent_tasks(
            user_id=None,
            status=None,
            agent_id=None,
            agent_type="supervisor",
            keyword=None,
            created_from=None,
            created_to=None,
            min_duration_ms=None,
            max_duration_ms=None,
            has_error=None,
            limit=50,
            offset=0,
        )
    )

    assert [item["taskId"] for item in payload["items"]] == ["task-supervisor"]


def test_blocked_tool_reasons_include_policy_and_selection(app_modules, monkeypatch):
    app, _tasks = app_modules
    from brain.core.agent_registry import AgentToolSpec

    monkeypatch.delenv("APP_ALLOW_HIGH_RISK_TOOLS", raising=False)
    monkeypatch.delenv("ALLOW_HIGH_RISK_TOOLS", raising=False)
    agent = app.AgentConfig(
        id="policy-agent",
        name="Policy Agent",
        tools=[
            AgentToolSpec(name="mcp_local:code_interpreter", policy={"risk": "high"}),
            AgentToolSpec(name="mcp_local:*"),
        ],
    )

    assert app._blocked_tool_reasons(["mcp_local:code_interpreter"], agent, None) == {
        "mcp_local:code_interpreter": "high_risk_requires_enable"
    }
    assert app._blocked_tool_reasons(
        ["mcp_local:code_interpreter"],
        agent,
        None,
        ["mcp_local:code_interpreter"],
    ) == {"mcp_local:code_interpreter": "blocked_by_policy"}
    assert app._blocked_tool_reasons(["mcp_local:deepsearch"], agent, ["mcp_local:weather"]) == {
        "mcp_local:deepsearch": "not_selected"
    }


def test_list_agent_tools_returns_builtin_and_mcp_tools(app_modules, monkeypatch):
    app, _tasks = app_modules
    from brain.core.agent_registry import AgentToolSpec

    agent = app.AgentConfig(
        id="tool-agent",
        name="Tool Agent",
        tools=[
            AgentToolSpec(name="builtin:plan_tool"),
            AgentToolSpec(name="mcp_local:deepsearch"),
            AgentToolSpec(
                name="mcp_local:code_interpreter",
                description="Configured code runner",
                input_schema={"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
                output_schema={"type": "object", "properties": {"result": {"type": "string"}}},
                policy={"risk": "high"},
            ),
        ],
    )

    async def fake_fetch_tools(_self):
        return [
            SimpleNamespace(
                name="mcp_local:deepsearch",
                description="Search",
                input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
                output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
            ),
            SimpleNamespace(
                name="mcp_local:code_interpreter",
                description="Code",
                input_schema={"type": "object"},
                output_schema={},
            ),
        ]

    monkeypatch.delenv("APP_ALLOW_HIGH_RISK_TOOLS", raising=False)
    monkeypatch.delenv("ALLOW_HIGH_RISK_TOOLS", raising=False)
    monkeypatch.setattr(app.agentRegistry, "reload", lambda: None)
    monkeypatch.setattr(app.agentRegistry, "get", lambda agent_id: agent if agent_id == "tool-agent" else None)
    monkeypatch.setattr(app.MCPToolFetcher, "fetch_tools", fake_fetch_tools)

    payload = asyncio.run(app.list_agent_tools(agent_id="tool-agent"))

    names = [item["name"] for item in payload["items"]]
    assert "builtin:plan_tool" in names
    assert "mcp_local:deepsearch" in names
    assert payload["items"][names.index("mcp_local:deepsearch")]["inputSchema"]["properties"]["query"]["type"] == "string"
    assert payload["blockedTools"] == [
        {
            "name": "mcp_local:code_interpreter",
            "description": "Configured code runner",
            "allowed": False,
            "blockReason": "high_risk_requires_enable",
            "inputSchema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
            "outputSchema": {"type": "object", "properties": {"result": {"type": "string"}}},
            "alias": "",
            "purpose": "",
            "whenToUse": "",
            "required": False,
            "timeoutSeconds": None,
            "policy": {"risk": "high"},
        }
    ]


def test_build_tool_collection_allows_approved_high_risk_tools(app_modules, monkeypatch):
    app, _tasks = app_modules
    from brain.core.agent_registry import AgentToolSpec

    agent = app.AgentConfig(
        id="approved-tool-agent",
        name="Approved Tool Agent",
        tools=[AgentToolSpec(name="mcp_local:code_interpreter", policy={"risk": "high"})],
    )

    async def fake_fetch_tools(_self):
        return [
            SimpleNamespace(
                name="mcp_local:code_interpreter",
                description="Code",
                input_schema={"type": "object"},
                output_schema={},
            )
        ]

    monkeypatch.delenv("APP_ALLOW_HIGH_RISK_TOOLS", raising=False)
    monkeypatch.delenv("ALLOW_HIGH_RISK_TOOLS", raising=False)
    monkeypatch.setattr(app.agentRegistry, "get", lambda agent_id: agent if agent_id == "approved-tool-agent" else None)
    monkeypatch.setattr(app.MCPToolFetcher, "fetch_tools", fake_fetch_tools)

    base_ctx = {
        "requestId": "request-approved",
        "sessionId": "session-approved",
        "user_id": "user-1",
        "agent_id": "approved-tool-agent",
        "run_id": "run-approved",
        "query": "",
        "task": None,
        "printer": None,
        "toolCollection": None,
        "dateInfo": "2026-05-30",
        "isStream": False,
    }

    blocked_ctx = app.AgentContext(**base_ctx)
    blocked_tc = asyncio.run(app.build_tool_collection(blocked_ctx))
    assert "mcp_local:code_interpreter" not in blocked_tc.tool_map
    assert blocked_tc.blocked_tools == ["mcp_local:code_interpreter"]

    approved_ctx = app.AgentContext(**base_ctx, approved_tools=["mcp_local:code_interpreter"])
    approved_tc = asyncio.run(app.build_tool_collection(approved_ctx))
    assert "mcp_local:code_interpreter" in approved_tc.tool_map
    assert approved_tc.blocked_tools == []


def test_remote_artifact_download_redirects_to_recorded_url(app_modules):
    app, tasks = app_modules
    store = tasks.TaskStore()
    store.create_task(task_id="remote-download", trace_id="trace-remote-download")
    artifact = store.add_remote_artifact(
        "remote-download",
        "https://files.example.test/output.csv",
        filename="output.csv",
    )

    response = asyncio.run(app.download_agent_task_artifact("remote-download", artifact.artifact_id))

    assert response.status_code in {302, 307}
    assert response.headers["location"] == "https://files.example.test/output.csv"


def test_handoff_task_creates_allowed_child_task(app_modules, monkeypatch):
    app, tasks = app_modules
    created_background = []

    configs = {
        "parent-agent": app.AgentConfig(
            id="parent-agent",
            name="Parent Agent",
            handoffs={"allowed": ["child-agent"]},
        ),
        "child-agent": app.AgentConfig(
            id="child-agent",
            name="Child Agent",
            mode="react",
        ),
    }

    def fake_resolve(agent_id):
        return configs.get(agent_id)

    def fake_create_task(coro):
        created_background.append(coro)
        coro.close()

        class DoneTask:
            def done(self):
                return True

            def cancel(self):
                return None

        return DoneTask()

    monkeypatch.setattr(app, "_resolve_agent_config", fake_resolve)
    monkeypatch.setattr(app.asyncio, "create_task", fake_create_task)
    parent_ctx = app.AgentContext(
        requestId="request-parent",
        sessionId="session-parent",
        user_id="user-1",
        agent_id="parent-agent",
        run_id="conversation-1",
        query="parent task",
        task=None,
        printer=None,
        toolCollection=None,
        dateInfo="2026-05-30",
        task_id="parent-task",
        outputStyle="markdown",
        approved_tools=["mcp_local:code_interpreter"],
        run_environment="sandbox",
    )

    payload = asyncio.run(
        app._start_handoff_task(
            parent_ctx,
            "child-agent",
            "child task",
            {"outputStyle": "markdown"},
        )
    )

    assert payload["agentId"] == "child-agent"
    assert payload["metadata"]["source"] == "handoff"
    assert payload["metadata"]["parentTaskId"] == "parent-task"
    assert payload["metadata"]["runEnvironment"] == "sandbox"
    assert payload["metadata"]["approvedTools"] == ["mcp_local:code_interpreter"]
    assert created_background

    store = tasks.TaskStore()
    events = store.list_events(payload["taskId"])
    assert events[-1].event_type == "task_queued"
    assert tasks.serialize_event(events[-1])["payload"]["parentAgentId"] == "parent-agent"
    assert tasks.serialize_event(events[-1])["payload"]["approvedTools"] == ["mcp_local:code_interpreter"]
    parent_events = store.list_events("parent-task")
    assert parent_events[-1].event_type == "task_handoff_requested"
    assert tasks.serialize_event(parent_events[-1])["payload"]["targetAgentId"] == "child-agent"
    assert tasks.serialize_event(parent_events[-1])["payload"]["childTaskId"] == payload["taskId"]
    assert tasks.serialize_event(parent_events[-1])["payload"]["approvedTools"] == ["mcp_local:code_interpreter"]

    with pytest.raises(ValueError, match="cannot hand off"):
        asyncio.run(app._start_handoff_task(parent_ctx, "blocked-agent", "blocked", {}))


def test_run_agent_evals_creates_task_for_each_case(app_modules, monkeypatch):
    app, tasks = app_modules
    from brain.core.agent_registry import AgentEvalCase

    created_background = []
    agent = app.AgentConfig(
        id="eval-agent",
        name="Eval Agent",
        mode="react",
        evals=[
            AgentEvalCase(id="case-a", name="Case A", input="first task", expected="first"),
            AgentEvalCase(id="case-b", name="Case B", input="second task", expected="second"),
        ],
    )

    def fake_create_task(coro):
        created_background.append(coro)
        coro.close()

        class DoneTask:
            def done(self):
                return True

            def cancel(self):
                return None

        return DoneTask()

    monkeypatch.setattr(app.agentRegistry, "reload", lambda: None)
    monkeypatch.setattr(app.agentRegistry, "get", lambda agent_id: agent if agent_id == "eval-agent" else None)
    monkeypatch.setattr(app.asyncio, "create_task", fake_create_task)

    payload = asyncio.run(app.run_agent_evals("eval-agent", user_id="tester", output_style="gaia"))

    assert payload["count"] == 2
    assert [item["eval"]["caseId"] for item in payload["items"]] == ["case-a", "case-b"]
    assert [item["task"]["status"] for item in payload["items"]] == [
        tasks.AgentTaskStatus.QUEUED,
        tasks.AgentTaskStatus.QUEUED,
    ]
    assert len(created_background) == 2

    store = tasks.TaskStore()
    first_task_id = payload["items"][0]["task"]["taskId"]
    first_events = store.list_events(first_task_id)
    assert first_events[-1].event_type == "eval_run_created"
    assert tasks.serialize_event(first_events[-1])["payload"]["caseId"] == "case-a"


def test_websocket_disconnect_does_not_cancel_background_task(app_modules, monkeypatch):
    app, _tasks = app_modules
    cancelled = False

    class FakeWebSocket:
        async def accept(self):
            return None

        async def receive_json(self):
            return {
                "trace_id": "ws-trace",
                "user_id": "user-1",
                "agent_id": "task-pilot-agent",
                "conversation_id": "conversation-1",
                "messages": [{"role": "user", "content": "hello"}],
            }

        async def send_json(self, _payload):
            raise app.WebSocketDisconnect()

        async def send_text(self, _payload):
            raise app.WebSocketDisconnect()

        async def close(self, code=None):
            return None

    async def run_scenario():
        nonlocal cancelled
        created_tasks = []
        stop_worker = asyncio.Event()
        real_create_task = asyncio.create_task

        async def fake_run_autoagent(_req, enqueue):
            nonlocal cancelled
            enqueue('data: {"messageType":"stream","result":"hello"}\n\n')
            try:
                await stop_worker.wait()
            except asyncio.CancelledError:
                cancelled = True
                raise

        def tracking_create_task(coro):
            task = real_create_task(coro)
            created_tasks.append(task)
            return task

        monkeypatch.setattr(app, "_run_autoagent", fake_run_autoagent)
        monkeypatch.setattr(app.asyncio, "create_task", tracking_create_task)

        await asyncio.wait_for(app.autoagent_ws(FakeWebSocket()), timeout=1)

        assert created_tasks
        assert created_tasks[0].done() is False
        assert cancelled is False
        stop_worker.set()
        await created_tasks[0]

    asyncio.run(run_scenario())
