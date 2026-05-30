from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

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
                trace_id="create-task",
                user_id="user-1",
                agent_id="task-pilot-agent",
                conversation_id="conversation-1",
                outputStyle="markdown",
                mode="react",
                run_environment="sandbox",
                messages=[app.AgentMessage(role="user", content="run in background")],
            )
        )
    )

    assert payload["taskId"] == "create-task"
    assert payload["status"] == tasks.AgentTaskStatus.QUEUED
    assert payload["metadata"]["source"] == "api"
    assert payload["metadata"]["runEnvironment"] == "sandbox"
    assert created_background
    created_background[0].close()

    store = tasks.TaskStore()
    events = store.list_events("create-task")
    assert events[-1].event_type == "task_queued"
    assert tasks.serialize_event(events[-1])["payload"]["runEnvironment"] == "sandbox"


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
    assert created_background

    store = tasks.TaskStore()
    events = store.list_events(payload["taskId"])
    assert events[-1].event_type == "task_queued"
    assert tasks.serialize_event(events[-1])["payload"]["parentAgentId"] == "parent-agent"

    with pytest.raises(ValueError, match="cannot hand off"):
        asyncio.run(app._start_handoff_task(parent_ctx, "blocked-agent", "blocked", {}))


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
