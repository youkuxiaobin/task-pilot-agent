from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture()
def task_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_DB_URL", f"sqlite:///{tmp_path / 'tasks.db'}")
    monkeypatch.setenv("TASK_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv(
        "APP_CONFIG_FILE",
        str(Path(__file__).resolve().parents[3] / "config" / "config.yaml"),
    )

    import file.db_engine as db_engine

    db_engine.get_engine.cache_clear()

    import brain.core.tasks as tasks

    tasks = importlib.reload(tasks)
    yield tasks

    db_engine.get_engine.cache_clear()


def test_recover_background_tasks_rebuilds_request_and_records_event(task_modules):
    from brain.core.task_recovery import recover_background_tasks

    store = task_modules.TaskStore()
    store.create_task(
        task_id="recover-me",
        trace_id="recover-me",
        conversation_id="session-1",
        user_id="user-1",
        agent_id="agent-1",
        mode="react",
        output_style="markdown",
        input_text="continue this",
        metadata={
            "selectedTools": ["mcp_local:deepsearch"],
            "approvedTools": ["mcp_local:code_interpreter"],
            "runEnvironment": "sandbox",
            "language": "en",
            "sessionMessageId": "message-1",
            "inputFiles": [
                {
                    "fileName": "source.csv",
                    "ossUrl": "https://files.example.test/source.csv",
                    "fileSize": 10,
                }
            ],
        },
    )

    started = []

    def deserialize_file_items(items):
        return list(items or [])

    async def fake_run_autoagent(req, enqueue):
        return None

    def fake_start_background_run(run_id, coro):
        started.append((run_id, coro))
        return object()

    payload = recover_background_tasks(
        store=store,
        owner="worker-recovery",
        start_background_run=fake_start_background_run,
        run_autoagent=fake_run_autoagent,
        deserialize_file_items=deserialize_file_items,
        lease_ms=60_000,
    )

    assert payload["count"] == 1
    assert payload["items"][0]["taskId"] == "recover-me"
    assert len(started) == 1
    run_id, coro = started[0]
    try:
        req = coro.cr_frame.f_locals["req"]
        assert run_id == "recover-me"
        assert req.trace_id == "recover-me"
        assert req.conversation_id == "session-1"
        assert req.session_message_id == "message-1"
        assert req.selected_tools == ["mcp_local:deepsearch"]
        assert req.approved_tools == ["mcp_local:code_interpreter"]
        assert req.run_environment == "sandbox"
        assert req.language == "en"
        assert req.messages[0].content == "continue this"
        assert req.messages[0].uploadFile[0].fileName == "source.csv"
    finally:
        coro.close()

    events = store.list_events("recover-me")
    assert events[-1].event_type == "task_recovery_requested"
    event_payload = task_modules.serialize_event(events[-1])["payload"]
    assert event_payload["owner"] == "worker-recovery"


def test_recover_background_tasks_fails_exhausted_recovery_attempts(task_modules):
    from brain.core.task_recovery import recover_background_tasks

    store = task_modules.TaskStore()
    store.create_task(
        task_id="recover-exhausted",
        trace_id="recover-exhausted",
        conversation_id="session-1",
        user_id="user-1",
        agent_id="agent-1",
        mode="react",
        output_style="markdown",
        input_text="continue this",
    )
    store.mark_background_dispatch_started("recover-exhausted", owner="worker-a", lease_ms=1)
    store.update_status("recover-exhausted", task_modules.AgentTaskStatus.RUNNING)

    import time

    time.sleep(0.01)
    claimed = store.claim_recoverable_background_tasks(
        owner="worker-b",
        lease_ms=1,
        max_attempts=2,
    )
    assert [item.task_id for item in claimed] == ["recover-exhausted"]
    time.sleep(0.01)

    started = []

    def deserialize_file_items(items):
        return list(items or [])

    async def fake_run_autoagent(req, enqueue):
        return None

    def fake_start_background_run(run_id, coro):
        started.append((run_id, coro))
        return object()

    payload = recover_background_tasks(
        store=store,
        owner="worker-recovery",
        start_background_run=fake_start_background_run,
        run_autoagent=fake_run_autoagent,
        deserialize_file_items=deserialize_file_items,
        lease_ms=60_000,
        max_attempts=2,
    )

    assert payload["count"] == 0
    assert payload["items"] == []
    assert payload["failedCount"] == 1
    assert payload["failedItems"][0]["taskId"] == "recover-exhausted"
    assert started == []

    task_payload = task_modules.serialize_task(store.get_task("recover-exhausted"))
    assert task_payload["status"] == task_modules.AgentTaskStatus.FAILED
    assert task_payload["error"] == task_modules.BACKGROUND_DISPATCH_EXHAUSTED_ERROR

    events = store.list_events("recover-exhausted")
    assert events[-1].event_type == "task_recovery_failed"
    event_payload = task_modules.serialize_event(events[-1])["payload"]
    assert event_payload["owner"] == "worker-recovery"
    assert event_payload["attempt"] == 2
    assert event_payload["maxAttempts"] == 2
