from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture()
def session_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_DB_URL", f"sqlite:///{tmp_path / 'sessions.db'}")
    monkeypatch.setenv(
        "APP_CONFIG_FILE",
        str(Path(__file__).resolve().parents[3] / "config" / "config.yaml"),
    )

    import file.db_engine as db_engine

    db_engine.get_engine.cache_clear()

    import brain.core.sessions as sessions

    sessions = importlib.reload(sessions)
    yield sessions

    db_engine.get_engine.cache_clear()


def test_session_store_creates_session_messages_and_redacts_metadata(session_modules):
    store = session_modules.SessionStore()

    created = store.create_session(
        session_id="session-1",
        user_id="user-1",
        agent_id="agent-1",
        metadata={"token": "raw-secret", "keep": "visible"},
    )
    payload = session_modules.serialize_session(created)

    assert payload["sessionId"] == "session-1"
    assert payload["status"] == session_modules.AgentSessionStatus.IDLE
    assert payload["metadata"]["token"] == "***"
    assert payload["metadata"]["keep"] == "visible"

    message = store.add_message(
        "session-1",
        run_id="run-1",
        user_id="user-1",
        role=session_modules.AgentMessageRole.USER,
        content="hello session store",
        metadata={"api_key": "secret-key", "keep": "ok"},
    )
    message_payload = session_modules.serialize_message(message)

    assert message_payload["messageId"]
    assert message_payload["runId"] == "run-1"
    assert message_payload["metadata"]["api_key"] == "***"
    assert message_payload["metadata"]["keep"] == "ok"

    session_payload = session_modules.serialize_session(store.get_session("session-1"))
    assert session_payload["title"] == "hello session store"
    assert session_payload["lastMessageId"] == message.message_id
    assert session_payload["lastMessagePreview"] == "hello session store"
    assert [item.message_id for item in store.list_messages("session-1")] == [message.message_id]

    second = store.add_message(
        "session-1",
        message_id="message-2",
        run_id="run-1",
        user_id="user-1",
        role=session_modules.AgentMessageRole.ASSISTANT,
        content="second reply",
    )
    third = store.add_message(
        "session-1",
        message_id="message-3",
        run_id="run-1",
        user_id="user-1",
        role=session_modules.AgentMessageRole.USER,
        content="third request",
    )
    fourth = store.add_message(
        "session-1",
        message_id="message-4",
        run_id="run-1",
        user_id="user-1",
        role=session_modules.AgentMessageRole.ASSISTANT,
        content="fourth reply",
    )
    fifth = store.add_message(
        "session-1",
        message_id="message-5",
        run_id="run-1",
        user_id="user-1",
        role=session_modules.AgentMessageRole.USER,
        content="fifth request",
    )
    assert [item.message_id for item in store.list_messages("session-1")] == [
        message.message_id,
        second.message_id,
        third.message_id,
        fourth.message_id,
        fifth.message_id,
    ]
    assert store.count_messages("session-1") == 5
    before_third = store.list_messages("session-1", before_message_id=third.message_id)
    assert [item.message_id for item in before_third] == [message.message_id, second.message_id]
    assert store.count_messages("session-1", before_message_id=third.message_id) == 2
    before_fifth = store.list_messages("session-1", limit=2, before_message_id=fifth.message_id)
    assert [item.message_id for item in before_fifth] == [third.message_id, fourth.message_id]
    assert store.count_messages("session-1", before_message_id=fifth.message_id) == 4
    older_before_fifth = store.list_messages("session-1", limit=2, offset=2, before_message_id=fifth.message_id)
    assert [item.message_id for item in older_before_fifth] == [message.message_id, second.message_id]
    assert store.list_messages("session-1", before_message_id="missing-message") == []
    assert store.count_messages("session-1", before_message_id="missing-message") == 0


def test_session_store_creates_updates_and_lists_runs(session_modules):
    store = session_modules.SessionStore()
    store.create_session(session_id="session-run", user_id="user-a", agent_id="agent-a")

    created = store.create_run(
        run_id="run-1",
        session_id="session-run",
        user_id="user-a",
        user_message_id="msg-user",
        trace_id="trace-1",
        agent_id="agent-a",
        mode="react",
        output_style="markdown",
        input_text="do work",
        metadata={"api_key": "secret-key", "keep": "ok"},
    )

    payload = session_modules.serialize_run(created)
    assert payload["runId"] == "run-1"
    assert payload["sessionId"] == "session-run"
    assert payload["status"] == "queued"
    assert payload["metadata"]["api_key"] == "***"
    assert payload["metadata"]["keep"] == "ok"

    running = store.update_run("run-1", status="running", work_dir="/tmp/work")
    running_payload = session_modules.serialize_run(running)
    assert running_payload["status"] == "running"
    assert running_payload["startedAt"]
    assert running_payload["workDir"] == "/tmp/work"

    completed = store.update_run(
        "run-1",
        status="completed",
        assistant_message_id="msg-assistant",
        output_text="done",
        metadata={"usage": {"messageCount": 12}},
    )
    completed_payload = session_modules.serialize_run(completed)
    assert completed_payload["status"] == "completed"
    assert completed_payload["assistantMessageId"] == "msg-assistant"
    assert completed_payload["output"] == "done"
    assert completed_payload["endedAt"]
    assert completed_payload["metadata"]["keep"] == "ok"
    assert completed_payload["metadata"]["usage"]["messageCount"] == 12

    store.create_run(run_id="run-2", session_id="session-run", status="failed", error_message="bad")
    assert [item.run_id for item in store.list_runs("session-run")] == ["run-2", "run-1"]
    assert [item.run_id for item in store.list_runs("session-run", status="completed")] == ["run-1"]
    assert store.count_runs("session-run") == 2
    assert store.count_runs("session-run", status="failed") == 1
    assert session_modules.serialize_run(store.get_run("run-2"))["errorMessage"] == "bad"


def test_session_store_creates_filters_and_redacts_run_events(session_modules):
    store = session_modules.SessionStore()
    store.create_session(session_id="session-events", user_id="user-a")
    store.create_run(run_id="run-1", session_id="session-events", user_id="user-a")

    first = store.add_run_event(
        session_id="session-events",
        run_id="run-1",
        user_id="user-a",
        event_id="evt-custom-1",
        event_type="tool_call",
        source="sse",
        message_id="message-1",
        payload={"tool": "web_search", "args": {"query": "public", "authorization": "Bearer secret"}},
    )
    second = store.add_run_event(
        session_id="session-events",
        run_id="run-1",
        event_type="tool_result",
        source="sse",
        payload={"tool": "web_search", "result": "done"},
    )
    duplicate = store.add_run_event(
        session_id="session-events",
        run_id="run-1",
        event_id="evt-custom-1",
        event_type="tool_call",
        payload={"ignored": True},
    )

    assert duplicate.id == first.id
    assert second.seq == first.seq + 1
    payload = session_modules.serialize_run_event(first)
    assert payload["eventId"] == "evt-custom-1"
    assert payload["sessionId"] == "session-events"
    assert payload["runId"] == "run-1"
    assert payload["userId"] == "user-a"
    assert payload["messageId"] == "message-1"
    assert payload["eventSchemaVersion"] == 1
    assert payload["eventCategory"] == "tool"
    assert payload["eventAlias"] == "tool_call_started"
    assert payload["payload"]["args"]["query"] == "public"
    assert payload["payload"]["args"]["authorization"] == "***"
    assert [item.event_id for item in store.list_run_events("session-events", event_type="tool_call")] == [
        "evt-custom-1"
    ]
    assert [item.event_id for item in store.list_run_events("session-events", after_seq=first.seq)] == [
        second.event_id
    ]
    assert store.count_run_events("session-events") == 2
    assert store.count_run_events("session-events", source="sse") == 2
    assert store.count_run_events("session-events", event_type="tool_result") == 1


def test_session_store_creates_lists_and_redacts_artifacts(session_modules, tmp_path):
    store = session_modules.SessionStore()
    store.create_session(session_id="session-artifacts", user_id="user-a")
    store.create_run(run_id="run-artifact-1", session_id="session-artifacts", user_id="user-a")
    store.create_run(run_id="run-artifact-2", session_id="session-artifacts", user_id="user-a")

    local_file = tmp_path / "result.txt"
    local_file.write_text("artifact", encoding="utf-8")
    local = store.add_artifact(
        session_id="session-artifacts",
        run_id="run-artifact-1",
        user_id="user-a",
        message_id="message-1",
        file_path=str(local_file),
        artifact_id="artifact-local",
        metadata={"api_key": "raw-secret", "keep": "ok"},
    )
    remote = store.add_artifact(
        session_id="session-artifacts",
        run_id="run-artifact-2",
        file_path="https://files.example.test/report.csv?token=raw-secret",
        artifact_id="artifact-remote",
        filename="report.csv",
        file_size=12,
    )
    duplicate = store.add_artifact(
        session_id="session-artifacts",
        run_id="run-artifact-1",
        file_path=str(local_file),
        artifact_id="artifact-local",
    )

    assert duplicate.id == local.id
    local_payload = session_modules.serialize_agent_artifact(local)
    assert local_payload["artifactId"] == "artifact-local"
    assert local_payload["sessionId"] == "session-artifacts"
    assert local_payload["runId"] == "run-artifact-1"
    assert local_payload["taskId"] == "run-artifact-1"
    assert local_payload["messageId"] == "message-1"
    assert local_payload["filename"] == "result.txt"
    assert local_payload["fileSize"] == len("artifact")
    assert local_payload["metadata"]["api_key"] == "***"
    assert local_payload["metadata"]["keep"] == "ok"

    remote_payload = session_modules.serialize_agent_artifact(remote)
    assert remote_payload["isRemote"] is True
    assert remote_payload["remoteUrl"] == "https://files.example.test/report.csv?token=***"
    assert remote_payload["fileSize"] == 12

    assert [item.artifact_id for item in store.list_artifacts("session-artifacts")] == [
        "artifact-local",
        "artifact-remote",
    ]
    assert [item.artifact_id for item in store.list_artifacts("session-artifacts", run_id="run-artifact-2")] == [
        "artifact-remote"
    ]
    assert store.count_artifacts("session-artifacts") == 2
    assert store.count_artifacts("session-artifacts", run_id="run-artifact-1") == 1
    assert store.get_artifact("session-artifacts", "artifact-local").artifact_id == "artifact-local"
    assert store.delete_artifacts("session-artifacts", run_id="run-artifact-1") == 1
    assert [item.artifact_id for item in store.list_artifacts("session-artifacts")] == ["artifact-remote"]


def test_session_store_lists_updates_and_archives_by_owner(session_modules):
    store = session_modules.SessionStore()
    store.create_session(session_id="session-a", user_id="user-a", title="alpha work")
    store.create_session(session_id="session-b", user_id="user-a", title="beta work")
    store.create_session(session_id="session-c", user_id="user-b", title="alpha other")
    store.update_session("session-b", archived=True)

    user_a_sessions = store.list_sessions(user_id="user-a")
    assert [item.session_id for item in user_a_sessions] == ["session-a"]
    assert store.count_sessions(user_id="user-a") == 1
    active_user_a_sessions = store.list_sessions(user_id="user-a", status="active")
    assert [item.session_id for item in active_user_a_sessions] == ["session-a"]
    assert store.count_sessions(user_id="user-a", status="active") == 1
    active_with_archived = store.list_sessions(user_id="user-a", status="active", include_archived=True)
    assert [item.session_id for item in active_with_archived] == ["session-a"]

    archived = store.list_sessions(user_id="user-a", include_archived=True)
    assert {item.session_id for item in archived} == {"session-a", "session-b"}
    assert store.count_sessions(user_id="user-a", include_archived=True) == 2

    keyword_matches = store.list_sessions(user_id="user-b", keyword="alpha")
    assert [item.session_id for item in keyword_matches] == ["session-c"]
    assert store.count_sessions(user_id="user-b", keyword="alpha") == 1

    updated = store.update_session(
        "session-a",
        title="renamed",
        pinned=True,
        status=session_modules.AgentSessionStatus.RUNNING,
        current_run_id="run-a",
    )
    updated_payload = session_modules.serialize_session(updated)
    assert updated_payload["title"] == "renamed"
    assert updated_payload["pinned"] is True
    assert updated_payload["status"] == session_modules.AgentSessionStatus.RUNNING
    assert updated_payload["currentRunId"] == "run-a"

    cleared = store.update_session(
        "session-a",
        status=session_modules.AgentSessionStatus.IDLE,
        current_run_id="",
    )
    assert session_modules.serialize_session(cleared)["currentRunId"] is None

    deleted = store.delete_session("session-a")
    deleted_payload = session_modules.serialize_session(deleted)
    assert deleted_payload["status"] == session_modules.AgentSessionStatus.ARCHIVED
    assert deleted_payload["archivedAt"]
    assert store.list_sessions(user_id="user-a") == []
    archived_after_delete = store.list_sessions(user_id="user-a", include_archived=True)
    assert {item.session_id for item in archived_after_delete} == {"session-a", "session-b"}


def test_create_existing_session_does_not_change_owner(session_modules):
    store = session_modules.SessionStore()
    created = store.create_session(session_id="shared-session", user_id="user-a")
    fetched = store.create_session(session_id="shared-session", user_id="user-b", title="new title")

    assert created.user_id == "user-a"
    assert fetched.user_id == "user-a"
