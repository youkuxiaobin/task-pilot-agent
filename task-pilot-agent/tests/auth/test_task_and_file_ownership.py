from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


@pytest.fixture()
def ownership_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_DB_URL", f"sqlite:///{tmp_path / 'ownership.db'}")
    monkeypatch.setenv("TASK_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("APP_CORE__UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv(
        "APP_CONFIG_FILE",
        str(Path(__file__).resolve().parents[3] / "config" / "config.yaml"),
    )

    import file.db_engine as db_engine

    db_engine.get_engine.cache_clear()
    db_engine = importlib.reload(db_engine)

    import brain.core.tasks as tasks
    import brain.app as app
    import file.file_table_op as file_table_op
    import file.file_op as file_op

    tasks = importlib.reload(tasks)
    app = importlib.reload(app)
    file_table_op = importlib.reload(file_table_op)
    file_op = importlib.reload(file_op)
    yield app, tasks, file_table_op, file_op

    db_engine.get_engine.cache_clear()


def user(user_id: str):
    return SimpleNamespace(user_id=user_id)


def test_task_list_uses_authenticated_user_over_query_user(ownership_modules):
    app, tasks, _file_table_op, _file_op = ownership_modules
    store = tasks.TaskStore()
    store.create_task(task_id="owner-task", trace_id="trace-owner", user_id="owner", input_text="alpha")
    store.create_task(task_id="other-task", trace_id="trace-other", user_id="other", input_text="beta")

    payload = asyncio.run(
        app.list_agent_tasks(
            user_id="other",
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
            current_user=user("owner"),
        )
    )

    assert [item["taskId"] for item in payload["items"]] == ["owner-task"]


def test_task_detail_rejects_another_users_task(ownership_modules):
    app, tasks, _file_table_op, _file_op = ownership_modules
    store = tasks.TaskStore()
    store.create_task(task_id="other-task", trace_id="trace-other", user_id="other", input_text="beta")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(app.get_agent_task("other-task", current_user=user("owner")))

    assert exc_info.value.status_code == 404


def test_create_task_binds_authenticated_user(ownership_modules, monkeypatch):
    app, tasks, _file_table_op, _file_op = ownership_modules
    created_background = []

    def fake_create_task(coro):
        created_background.append(coro)
        coro.close()

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
                trace_id="owned-create",
                user_id="malicious-user",
                agent_id="task-pilot-agent",
                conversation_id="conversation-1",
                messages=[app.AgentMessage(role="user", content="hello")],
            ),
            current_user=user("owner"),
        )
    )

    assert payload["userId"] == "owner"
    assert tasks.TaskStore().get_task("owned-create").user_id == "owner"
    assert created_background


def test_file_preview_rejects_another_users_file(ownership_modules):
    _app, _tasks, file_table_op, file_op = ownership_modules
    asyncio.run(
        file_table_op.FileInfoOp.add_by_content(
            filename="secret.txt",
            content="secret",
            file_id=file_op.get_file_id("request-1", "secret.txt"),
            request_id="request-1",
            user_id="owner",
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(file_op.preview_file("request-1", "secret.txt", current_user=user("other")))

    assert exc_info.value.status_code == 404


def test_websocket_autoagent_binds_current_user(ownership_modules, monkeypatch):
    app, _tasks, _file_table_op, _file_op = ownership_modules
    captured = {}

    class FakeWebSocket:
        cookies = {}

        async def accept(self):
            return None

        async def receive_json(self):
            return {
                "trace_id": "ws-owned",
                "user_id": "malicious-user",
                "agent_id": "task-pilot-agent",
                "conversation_id": "conversation-1",
                "messages": [{"role": "user", "content": "hello"}],
            }

        async def send_json(self, _payload):
            return None

        async def send_text(self, _payload):
            return None

        async def close(self, code=None):
            return None

    async def fake_run_autoagent(req, enqueue):
        captured["user_id"] = req.user_id
        enqueue("data: [DONE]\n\n")

    monkeypatch.setattr(app, "_run_autoagent", fake_run_autoagent)

    asyncio.run(app.autoagent_ws(FakeWebSocket()))

    assert captured["user_id"] == app.agentSettings.auth.dev_user_id
