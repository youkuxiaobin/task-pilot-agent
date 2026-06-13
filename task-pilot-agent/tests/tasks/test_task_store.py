from __future__ import annotations

import importlib
import asyncio
import json
import time
from pathlib import Path
from typing import Any, List

import pytest
from sqlalchemy import create_engine, inspect, text


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


def test_task_store_uses_magent_table_names(task_modules):
    import file.db_engine as db_engine

    store = task_modules.TaskStore()
    store.create_task(task_id="table-name-task", trace_id="trace-table-name")

    table_names = set(inspect(db_engine.get_engine()).get_table_names())
    assert {"magent_task", "magent_task_event", "magent_task_artifact"}.issubset(table_names)
    assert "meta_agent_task" not in table_names
    assert "meta_agent_task_event" not in table_names
    assert "meta_agent_task_artifact" not in table_names


def test_task_store_renames_legacy_meta_agent_tables(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy-tasks.db"
    legacy_engine = create_engine(f"sqlite:///{db_path}", future=True)
    with legacy_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE meta_agent_task (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id VARCHAR(128) NOT NULL,
                    trace_id VARCHAR(128) NOT NULL,
                    conversation_id VARCHAR(128) NOT NULL DEFAULT '',
                    user_id VARCHAR(128) NOT NULL DEFAULT '',
                    agent_id VARCHAR(128) NOT NULL DEFAULT '',
                    mode VARCHAR(64) NOT NULL DEFAULT '',
                    output_style VARCHAR(64) NOT NULL DEFAULT '',
                    status VARCHAR(32) NOT NULL DEFAULT 'queued',
                    input_text TEXT,
                    output_text TEXT,
                    error_message TEXT,
                    metadata TEXT,
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    started_at BIGINT,
                    ended_at BIGINT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE meta_agent_task_event (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id VARCHAR(128) NOT NULL,
                    trace_id VARCHAR(128) NOT NULL DEFAULT '',
                    event_type VARCHAR(64) NOT NULL,
                    source VARCHAR(64) NOT NULL DEFAULT '',
                    message_id VARCHAR(128),
                    payload TEXT NOT NULL,
                    created_at BIGINT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE meta_agent_task_artifact (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artifact_id VARCHAR(128) NOT NULL,
                    task_id VARCHAR(128) NOT NULL,
                    filename VARCHAR(512) NOT NULL,
                    file_path VARCHAR(2048) NOT NULL,
                    mime_type VARCHAR(128),
                    file_size BIGINT NOT NULL DEFAULT 0,
                    description TEXT,
                    metadata TEXT,
                    created_at BIGINT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO meta_agent_task (
                    task_id,
                    trace_id,
                    conversation_id,
                    user_id,
                    agent_id,
                    mode,
                    output_style,
                    status,
                    input_text,
                    metadata,
                    created_at,
                    updated_at
                )
                VALUES (
                    'legacy-task',
                    'legacy-trace',
                    'legacy-session',
                    'legacy-user',
                    'legacy-agent',
                    'react',
                    'markdown',
                    'queued',
                    'legacy input',
                    '{}',
                    1000,
                    1000
                )
                """
            )
        )
    legacy_engine.dispose()

    monkeypatch.setenv("FILE_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("TASK_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv(
        "APP_CONFIG_FILE",
        str(Path(__file__).resolve().parents[3] / "config" / "config.yaml"),
    )

    import file.db_engine as db_engine

    db_engine.get_engine.cache_clear()
    import brain.core.tasks as tasks

    tasks = importlib.reload(tasks)
    try:
        store = tasks.TaskStore()

        table_names = set(inspect(db_engine.get_engine()).get_table_names())
        assert {"magent_task", "magent_task_event", "magent_task_artifact"}.issubset(table_names)
        assert "meta_agent_task" not in table_names
        assert "meta_agent_task_event" not in table_names
        assert "meta_agent_task_artifact" not in table_names

        migrated_task = store.get_task("legacy-task")
        assert migrated_task is not None
        assert migrated_task.trace_id == "legacy-trace"
        assert migrated_task.conversation_id == "legacy-session"
        assert migrated_task.user_id == "legacy-user"
    finally:
        db_engine.get_engine.cache_clear()


def test_task_store_records_lifecycle_events_and_redacts_sensitive_payload(task_modules, tmp_path):
    store = task_modules.TaskStore()

    created = store.create_task(
        task_id="task-1",
        trace_id="trace-1",
        conversation_id="conversation-1",
        user_id="user-1",
        agent_id="agent-1",
        mode="react",
        output_style="markdown",
        input_text="search something",
        metadata={
            "source": "test",
            "api_key": "sk-secret",
            "nested": {"token": "hidden", "keep": "visible"},
        },
    )

    created_payload = task_modules.serialize_task(created)
    assert created_payload["status"] == task_modules.AgentTaskStatus.QUEUED
    assert created_payload["workDir"].endswith("task-1")
    assert (tmp_path / "workspaces" / "task-1").is_dir()
    assert created_payload["metadata"]["api_key"] == "***"
    assert created_payload["metadata"]["nested"]["token"] == "***"
    assert created_payload["metadata"]["nested"]["keep"] == "visible"

    running = store.update_status("task-1", task_modules.AgentTaskStatus.RUNNING)
    assert running is not None
    assert running.started_at is not None

    store.add_event(
        "task-1",
        "tool_call",
        {
            "tool": "deepsearch",
            "args": {"query": "public info", "authorization": "Bearer secret"},
        },
        trace_id="trace-1",
        source="sse",
        message_id="message-1",
    )

    completed = store.update_status(
        "task-1",
        task_modules.AgentTaskStatus.COMPLETED,
        output_text="done",
    )
    assert completed is not None
    assert completed.ended_at is not None

    fetched = store.get_task("task-1")
    assert fetched is not None
    fetched_payload = task_modules.serialize_task(fetched)
    assert fetched_payload["status"] == task_modules.AgentTaskStatus.COMPLETED
    assert fetched_payload["output"] == "done"

    store.increment_usage_metrics("task-1", {"events": 2, "toolCalls": 1})
    store.increment_usage_metrics("task-1", {"events": 1, "toolDurationMs": 25})
    usage_payload = task_modules.serialize_task(store.get_task("task-1"))["usage"]
    assert usage_payload["events"] == 3
    assert usage_payload["toolCalls"] == 1
    assert usage_payload["toolDurationMs"] == 25

    events = store.list_events("task-1")
    assert [event.event_type for event in events] == ["tool_call"]
    serialized_event = task_modules.serialize_event(events[0])
    assert serialized_event["eventId"] == f"evt_{events[0].id}"
    assert serialized_event["event_id"] == serialized_event["eventId"]
    assert serialized_event["eventSchemaVersion"] == 1
    assert serialized_event["eventCategory"] == "tool"
    assert serialized_event["eventAlias"] == "tool_call_started"
    event_payload = serialized_event["payload"]
    assert event_payload["args"]["query"] == "public info"
    assert event_payload["args"]["authorization"] == "***"


def test_task_store_start_task_does_not_reopen_final_task(task_modules):
    store = task_modules.TaskStore()
    store.create_task(task_id="start-queued", trace_id="trace-start-queued")

    started = store.start_task("start-queued")
    assert started is not None
    assert started.status == task_modules.AgentTaskStatus.RUNNING
    assert started.started_at is not None

    store.create_task(task_id="start-cancelled", trace_id="trace-start-cancelled")
    store.update_status("start-cancelled", task_modules.AgentTaskStatus.CANCELLED)

    skipped = store.start_task("start-cancelled")
    assert skipped is not None
    assert skipped.status == task_modules.AgentTaskStatus.CANCELLED
    assert store.get_task("start-cancelled").status == task_modules.AgentTaskStatus.CANCELLED


def test_task_store_background_dispatch_lease_and_recovery_claim(task_modules):
    store = task_modules.TaskStore()
    store.create_task(task_id="dispatch-active", trace_id="trace-dispatch-active")
    store.create_task(task_id="dispatch-stale", trace_id="trace-dispatch-stale")
    store.create_task(task_id="dispatch-final", trace_id="trace-dispatch-final")

    store.mark_background_dispatch_started("dispatch-active", owner="worker-a", lease_ms=60_000)
    stale = store.mark_background_dispatch_started("dispatch-stale", owner="worker-a", lease_ms=1)
    assert stale is not None
    store.update_status("dispatch-stale", task_modules.AgentTaskStatus.RUNNING)
    store.update_status("dispatch-final", task_modules.AgentTaskStatus.COMPLETED)
    time.sleep(0.01)

    claimed = store.claim_recoverable_background_tasks(owner="worker-b", limit=10, lease_ms=60_000)
    claimed_ids = [item.task_id for item in claimed]

    assert claimed_ids == ["dispatch-stale"]
    claimed_payload = task_modules.serialize_task(store.get_task("dispatch-stale"))
    dispatch = claimed_payload["metadata"]["backgroundDispatch"]
    assert claimed_payload["status"] == task_modules.AgentTaskStatus.QUEUED
    assert dispatch["status"] == "recovering"
    assert dispatch["owner"] == "worker-b"
    assert dispatch["previousStatus"] == task_modules.AgentTaskStatus.RUNNING

    active_payload = task_modules.serialize_task(store.get_task("dispatch-active"))
    assert active_payload["metadata"]["backgroundDispatch"]["owner"] == "worker-a"

    recovering_claimed = store.claim_recoverable_background_tasks(
        owner="worker-c",
        limit=10,
        lease_ms=60_000,
    )
    assert recovering_claimed == []


def test_task_store_background_dispatch_finish_clears_active_lease(task_modules):
    store = task_modules.TaskStore()
    store.create_task(task_id="dispatch-finish", trace_id="trace-dispatch-finish")
    store.mark_background_dispatch_started("dispatch-finish", owner="worker-a", lease_ms=60_000)
    store.update_status("dispatch-finish", task_modules.AgentTaskStatus.COMPLETED, output_text="done")

    finished = store.mark_background_dispatch_finished("dispatch-finish")

    payload = task_modules.serialize_task(finished)
    dispatch = payload["metadata"]["backgroundDispatch"]
    assert dispatch["status"] == task_modules.AgentTaskStatus.COMPLETED
    assert dispatch["leaseExpiresAt"] == 0
    assert dispatch["finishedAt"]


def test_task_store_background_dispatch_renew_requires_same_owner(task_modules):
    store = task_modules.TaskStore()
    store.create_task(task_id="dispatch-renew", trace_id="trace-dispatch-renew")
    started = store.mark_background_dispatch_started("dispatch-renew", owner="worker-a", lease_ms=1)
    assert started is not None
    original_payload = task_modules.serialize_task(started)
    original_expires_at = original_payload["metadata"]["backgroundDispatch"]["leaseExpiresAt"]
    time.sleep(0.01)

    wrong_owner = store.renew_background_dispatch("dispatch-renew", owner="worker-b", lease_ms=60_000)
    wrong_owner_payload = task_modules.serialize_task(wrong_owner)
    assert wrong_owner_payload["metadata"]["backgroundDispatch"]["leaseExpiresAt"] == original_expires_at

    renewed = store.renew_background_dispatch("dispatch-renew", owner="worker-a", lease_ms=60_000)
    renewed_payload = task_modules.serialize_task(renewed)
    dispatch = renewed_payload["metadata"]["backgroundDispatch"]
    assert dispatch["status"] == "running"
    assert dispatch["owner"] == "worker-a"
    assert dispatch["renewedAt"]
    assert dispatch["leaseExpiresAt"] > original_expires_at


def test_task_store_fails_exhausted_background_recoveries(task_modules):
    store = task_modules.TaskStore()
    store.create_task(task_id="dispatch-exhausted", trace_id="trace-dispatch-exhausted")
    store.mark_background_dispatch_started("dispatch-exhausted", owner="worker-a", lease_ms=1)
    store.update_status("dispatch-exhausted", task_modules.AgentTaskStatus.RUNNING)
    time.sleep(0.01)

    first_recovery = store.claim_recoverable_background_tasks(
        owner="worker-b",
        lease_ms=1,
        max_attempts=3,
    )
    assert [item.task_id for item in first_recovery] == ["dispatch-exhausted"]
    time.sleep(0.01)

    second_recovery = store.claim_recoverable_background_tasks(
        owner="worker-c",
        lease_ms=1,
        max_attempts=3,
    )
    assert [item.task_id for item in second_recovery] == ["dispatch-exhausted"]
    time.sleep(0.01)

    failed = store.fail_exhausted_background_recoveries(owner="worker-d", max_attempts=3)

    assert [item.task_id for item in failed] == ["dispatch-exhausted"]
    payload = task_modules.serialize_task(store.get_task("dispatch-exhausted"))
    dispatch = payload["metadata"]["backgroundDispatch"]
    assert payload["status"] == task_modules.AgentTaskStatus.FAILED
    assert payload["error"] == task_modules.BACKGROUND_DISPATCH_EXHAUSTED_ERROR
    assert dispatch["status"] == "failed"
    assert dispatch["owner"] == "worker-d"
    assert dispatch["attempt"] == 3
    assert dispatch["maxAttempts"] == 3
    assert dispatch["leaseExpiresAt"] == 0

    reclaimed = store.claim_recoverable_background_tasks(
        owner="worker-e",
        lease_ms=60_000,
        max_attempts=3,
    )
    assert reclaimed == []


def test_task_store_links_child_tasks_for_replay(task_modules):
    store = task_modules.TaskStore()
    store.create_task(
        task_id="parent-task",
        trace_id="trace-parent-task",
        user_id="user-1",
        agent_id="parent-agent",
        input_text="parent",
    )
    store.create_task(
        task_id="child-task",
        trace_id="trace-child-task",
        user_id="user-1",
        agent_id="child-agent",
        input_text="child",
        metadata={"parentTaskId": "parent-task", "parentAgentId": "parent-agent"},
    )

    linked = store.link_child_task("parent-task", "child-task", relationship="handoff", source="handoff")

    parent_payload = task_modules.serialize_task(linked)
    child_payload = task_modules.serialize_task(store.get_task("child-task"))
    assert parent_payload["childTasks"] == [
        {
            "taskId": "child-task",
            "runId": "child-task",
            "traceId": "trace-child-task",
            "agentId": "child-agent",
            "status": task_modules.AgentTaskStatus.QUEUED,
            "relationship": "handoff",
            "source": "handoff",
            "createdAt": child_payload["createdAt"],
            "updatedAt": child_payload["updatedAt"],
        }
    ]
    assert child_payload["parentTaskId"] == "parent-task"
    assert child_payload["parentAgentId"] == "parent-agent"


def test_task_store_persists_latest_plan_snapshot_from_plan_events(task_modules):
    store = task_modules.TaskStore()
    store.create_task(task_id="plan-snapshot", trace_id="trace-plan-snapshot")

    store.add_event(
        "plan-snapshot",
        "plan_created",
        {
            "messageType": "plan_created",
            "plan": {
                "title": "Research Plan",
                "steps": ["Search", "Summarize"],
                "step_status": ["not_started", "not_started"],
                "notes": ["", ""],
            },
        },
        trace_id="trace-plan-snapshot",
        source="sse",
    )
    created_payload = task_modules.serialize_task(store.get_task("plan-snapshot"))
    assert created_payload["latestPlan"]["title"] == "Research Plan"
    assert created_payload["latestPlanEventType"] == "plan_created"

    store.add_event(
        "plan-snapshot",
        "plan_step_completed",
        {
            "messageType": "plan_step_completed",
            "plan": {
                "title": "Research Plan",
                "steps": ["Search", "Summarize"],
                "step_status": ["completed", "not_started"],
                "notes": ["found sources", ""],
                "evidence": [[{"url": "https://example.test/source", "api_key": "hidden"}], []],
            },
        },
        trace_id="trace-plan-snapshot",
        source="sse",
    )
    completed_payload = task_modules.serialize_task(store.get_task("plan-snapshot"))
    assert completed_payload["latestPlan"]["step_status"] == ["completed", "not_started"]
    assert completed_payload["latestPlan"]["evidence"][0][0]["url"] == "https://example.test/source"
    assert completed_payload["latestPlan"]["evidence"][0][0]["api_key"] == "***"
    assert completed_payload["metadata"]["latestPlan"]["evidence"][0][0]["api_key"] == "***"
    assert completed_payload["latestPlanEventType"] == "plan_step_completed"
    assert isinstance(completed_payload["latestPlanUpdatedAt"], int)


def test_task_store_mirrors_session_run_events(task_modules):
    import brain.core.sessions as sessions

    sessions = importlib.reload(sessions)
    session_store = sessions.SessionStore()
    session_store.create_session(session_id="mirror-session", user_id="user-1")
    store = task_modules.TaskStore()
    store.create_task(
        task_id="mirror-run",
        trace_id="mirror-run",
        conversation_id="mirror-session",
        user_id="user-1",
    )

    task_event = store.add_event(
        "mirror-run",
        "tool_call",
        {"seq": 99, "tool": "web_search", "args": {"query": "public", "token": "secret"}},
        trace_id="mirror-run",
        source="sse",
        message_id="message-1",
    )
    second_task_event = store.add_event(
        "mirror-run",
        "result",
        {"seq": 99, "result": "partial"},
        trace_id="mirror-run",
        source="sse",
        message_id="message-2",
    )

    run_events = session_store.list_run_events("mirror-session")
    assert len(run_events) == 2
    assert [event.seq for event in run_events] == [1, 2]
    mirrored = sessions.serialize_run_event(run_events[0])
    assert mirrored["eventId"] == f"evt_{task_event.id}"
    assert mirrored["sessionId"] == "mirror-session"
    assert mirrored["runId"] == "mirror-run"
    assert mirrored["userId"] == "user-1"
    assert mirrored["seq"] == 1
    assert mirrored["eventType"] == "tool_call"
    assert mirrored["source"] == "sse"
    assert mirrored["messageId"] == "message-1"
    assert mirrored["payload"]["seq"] == 99
    assert mirrored["payload"]["args"]["query"] == "public"
    assert mirrored["payload"]["args"]["token"] == "***"
    second_mirrored = sessions.serialize_run_event(run_events[1])
    assert second_mirrored["eventId"] == f"evt_{second_task_event.id}"
    assert second_mirrored["seq"] == 2
    assert second_mirrored["payload"]["seq"] == 99


def test_task_store_mirrors_session_run_artifacts(task_modules):
    import brain.core.sessions as sessions

    sessions = importlib.reload(sessions)
    session_store = sessions.SessionStore()
    session_store.create_session(session_id="mirror-artifact-session", user_id="user-1")
    store = task_modules.TaskStore()
    task = store.create_task(
        task_id="mirror-artifact-run",
        trace_id="mirror-artifact-run",
        conversation_id="mirror-artifact-session",
        user_id="user-1",
    )
    work_dir = Path(task_modules.serialize_task(task)["workDir"])
    local_file = work_dir / "result.txt"
    local_file.write_text("artifact", encoding="utf-8")

    local_artifact = store.add_artifact(
        "mirror-artifact-run",
        str(local_file),
        artifact_id="mirror-local",
        metadata={"secret": "raw-secret", "keep": "ok"},
    )
    remote_artifact = store.add_remote_artifact(
        "mirror-artifact-run",
        "https://files.example.test/report.md?token=raw-secret",
        artifact_id="mirror-remote",
        filename="report.md",
    )

    session_artifacts = session_store.list_artifacts(
        "mirror-artifact-session",
        run_id="mirror-artifact-run",
    )
    assert {item.artifact_id for item in session_artifacts} == {
        local_artifact.artifact_id,
        remote_artifact.artifact_id,
    }
    payloads = {
        item["artifactId"]: item
        for item in [sessions.serialize_agent_artifact(item) for item in session_artifacts]
    }
    assert payloads["mirror-local"]["sessionId"] == "mirror-artifact-session"
    assert payloads["mirror-local"]["runId"] == "mirror-artifact-run"
    assert payloads["mirror-local"]["metadata"]["secret"] == "***"
    assert payloads["mirror-local"]["metadata"]["keep"] == "ok"
    assert payloads["mirror-remote"]["remoteUrl"] == "https://files.example.test/report.md?token=***"

    assert store.delete_task("mirror-artifact-run") is True
    assert session_store.list_artifacts("mirror-artifact-session", run_id="mirror-artifact-run") == []


def test_task_store_filters_events_by_type_and_source(task_modules):
    store = task_modules.TaskStore()
    store.create_task(task_id="event-filter", trace_id="trace-event-filter")
    store.add_event("event-filter", "tool_call", {"tool": "deepsearch"}, trace_id="trace-event-filter", source="sse")
    store.add_event("event-filter", "tool_result", {"tool": "deepsearch"}, trace_id="trace-event-filter", source="sse")
    store.add_event("event-filter", "task_failed", {"error": "boom"}, trace_id="trace-event-filter", source="autoagent")

    tool_events = store.list_events("event-filter", event_type="tool_call,tool_result")
    assert [event.event_type for event in tool_events] == ["tool_call", "tool_result"]

    autoagent_events = store.list_events("event-filter", source="autoagent")
    assert [event.event_type for event in autoagent_events] == ["task_failed"]

    combined = store.list_events("event-filter", event_type="tool_call", source="sse")
    assert [event.event_type for event in combined] == ["tool_call"]


def test_task_store_deletes_task_events_artifacts_and_workspace(task_modules):
    store = task_modules.TaskStore()
    created = store.create_task(task_id="delete-me", trace_id="trace-delete")
    work_dir = Path(task_modules.serialize_task(created)["workDir"])
    artifact_file = work_dir / "artifact.txt"
    artifact_file.write_text("artifact", encoding="utf-8")
    store.add_event("delete-me", "tool_call", {"tool": "demo"}, trace_id="trace-delete", source="sse")
    store.add_artifact("delete-me", str(artifact_file), filename="artifact.txt")

    assert store.delete_task("delete-me") is True

    assert store.get_task("delete-me") is None
    assert store.list_events("delete-me") == []
    assert store.list_artifacts("delete-me") == []
    assert not work_dir.exists()
    assert store.delete_task("delete-me") is False


def test_task_store_lists_tasks_by_owner_status_and_agent(task_modules):
    store = task_modules.TaskStore()

    store.create_task(
        task_id="task-a",
        trace_id="trace-a",
        conversation_id="conversation-a",
        user_id="user-a",
        agent_id="agent-a",
        input_text="weather lookup",
    )
    store.create_task(
        task_id="task-b",
        trace_id="trace-b",
        conversation_id="conversation-b",
        user_id="user-a",
        agent_id="agent-b",
        input_text="report draft",
    )
    store.create_task(
        task_id="task-c",
        trace_id="trace-c",
        user_id="user-b",
        agent_id="agent-a",
        input_text="browser task",
    )
    store.update_status("task-a", task_modules.AgentTaskStatus.COMPLETED)
    store.update_status("task-b", task_modules.AgentTaskStatus.FAILED, error_message="missing source")

    user_a_tasks = store.list_tasks(user_id="user-a")
    assert {task.task_id for task in user_a_tasks} == {"task-a", "task-b"}
    assert store.count_tasks(user_id="user-a") == 2

    completed_tasks = store.list_tasks(status=task_modules.AgentTaskStatus.COMPLETED)
    assert [task.task_id for task in completed_tasks] == ["task-a"]
    assert store.count_tasks(status=task_modules.AgentTaskStatus.COMPLETED) == 1

    agent_a_tasks = store.list_tasks(agent_id="agent-a")
    assert {task.task_id for task in agent_a_tasks} == {"task-a", "task-c"}
    assert store.count_tasks(agent_id="agent-a") == 2

    conversation_tasks = store.list_tasks(conversation_id="conversation-a")
    assert [task.task_id for task in conversation_tasks] == ["task-a"]
    assert store.count_tasks(conversation_id="conversation-a") == 1

    weather_tasks = store.list_tasks(keyword="weather")
    assert [task.task_id for task in weather_tasks] == ["task-a"]
    assert store.count_tasks(keyword="weather") == 1

    error_tasks = store.list_tasks(keyword="missing source")
    assert [task.task_id for task in error_tasks] == ["task-b"]
    assert store.count_tasks(keyword="missing source") == 1


def test_task_store_lists_tasks_by_time_duration_and_error(task_modules, monkeypatch):
    timestamps = iter([1_000, 1_100, 1_600, 2_000, 2_100, 7_500, 10_000])
    monkeypatch.setattr(task_modules, "now_ms", lambda: next(timestamps))
    store = task_modules.TaskStore()

    store.create_task(task_id="short-task", trace_id="trace-short", input_text="quick")
    store.update_status("short-task", task_modules.AgentTaskStatus.RUNNING)
    store.update_status("short-task", task_modules.AgentTaskStatus.COMPLETED)

    store.create_task(task_id="error-task", trace_id="trace-error", input_text="slow")
    store.update_status("error-task", task_modules.AgentTaskStatus.RUNNING)
    store.update_status(
        "error-task",
        task_modules.AgentTaskStatus.FAILED,
        error_message="tool failed",
    )

    store.create_task(task_id="late-task", trace_id="trace-late", input_text="new")

    assert [task.task_id for task in store.list_tasks(created_from_ms=5_000)] == ["late-task"]
    assert [task.task_id for task in store.list_tasks(created_to_ms=1_500)] == ["short-task"]
    assert [task.task_id for task in store.list_tasks(max_duration_ms=700)] == ["short-task"]
    assert [task.task_id for task in store.list_tasks(min_duration_ms=1_000)] == ["error-task"]
    assert [task.task_id for task in store.list_tasks(has_error=True)] == ["error-task"]
    assert {task.task_id for task in store.list_tasks(has_error=False)} == {"short-task", "late-task"}
    assert store.count_tasks(created_from_ms=5_000) == 1
    assert store.count_tasks(created_to_ms=1_500) == 1
    assert store.count_tasks(max_duration_ms=700) == 1
    assert store.count_tasks(min_duration_ms=1_000) == 1
    assert store.count_tasks(has_error=True) == 1
    assert store.count_tasks(has_error=False) == 2

    short_payload = task_modules.serialize_task(store.get_task("short-task"))
    error_payload = task_modules.serialize_task(store.get_task("error-task"))
    assert short_payload["durationMs"] == 500
    assert short_payload["hasError"] is False
    assert error_payload["durationMs"] == 5_400
    assert error_payload["hasError"] is True


def test_task_store_records_waiting_input_and_user_reply(task_modules):
    store = task_modules.TaskStore()
    store.create_task(task_id="needs-input", trace_id="trace-input", user_id="user-1")

    wait_event = store.request_user_input("needs-input", "Need account id")
    waiting_task = store.get_task("needs-input")

    assert waiting_task is not None
    assert waiting_task.status == task_modules.AgentTaskStatus.WAITING_INPUT
    assert task_modules.serialize_event(wait_event)["payload"]["prompt"] == "Need account id"

    input_event = store.add_user_input("needs-input", "account-123", user_id="user-2")
    updated_task = store.get_task("needs-input")
    input_payload = task_modules.serialize_event(input_event)["payload"]

    assert updated_task is not None
    assert updated_task.status == task_modules.AgentTaskStatus.QUEUED
    assert input_payload["content"] == "account-123"
    assert input_payload["userId"] == "user-2"
    assert [event.event_type for event in store.list_events("needs-input")] == [
        "waiting_input",
        "user_input",
    ]


def test_task_workspace_sanitizes_task_id(task_modules, tmp_path):
    store = task_modules.TaskStore()

    created = store.create_task(task_id="../unsafe/task", trace_id="trace-safe")
    payload = task_modules.serialize_task(created)

    assert payload["workDir"].startswith(str(tmp_path / "workspaces"))
    assert ".." not in Path(payload["workDir"]).name
    assert Path(payload["workDir"]).is_dir()


def test_task_create_forces_workspace_under_root(task_modules, tmp_path):
    store = task_modules.TaskStore()
    outside_dir = tmp_path / "outside-workspace"
    outside_dir.mkdir()

    task = store.create_task(
        task_id="forced-workspace",
        trace_id="trace-forced-workspace",
        metadata={"workDir": str(outside_dir)},
    )

    payload = task_modules.serialize_task(task)
    assert payload["workDir"].startswith(str(tmp_path / "workspaces"))
    assert payload["workDir"] != str(outside_dir)

    outside_file = outside_dir / "leak.txt"
    outside_file.write_text("outside", encoding="utf-8")
    with pytest.raises(ValueError, match="inside task workspace"):
        store.add_artifact("forced-workspace", str(outside_file))


def test_task_artifacts_are_scoped_to_task_workspace(task_modules, tmp_path):
    store = task_modules.TaskStore()
    task = store.create_task(task_id="artifact-task", trace_id="trace-artifact")
    task_payload = task_modules.serialize_task(task)
    artifact_path = Path(task_payload["workDir"]) / "result.txt"
    artifact_path.write_text("hello artifact", encoding="utf-8")

    artifact = store.add_artifact(
        "artifact-task",
        str(artifact_path),
        artifact_id="artifact-1",
        description="demo result",
        metadata={"api_key": "sk-test-secretvalue123456"},
    )
    artifact_payload = task_modules.serialize_artifact(artifact)

    assert artifact_payload["artifactId"] == "artifact-1"
    assert artifact_payload["filename"] == "result.txt"
    assert artifact_payload["fileSize"] == len("hello artifact")
    assert artifact_payload["metadata"]["api_key"] == "***"
    assert store.get_artifact("artifact-task", "artifact-1") is not None
    assert [item.artifact_id for item in store.list_artifacts("artifact-task")] == ["artifact-1"]

    outside_path = tmp_path / "outside.txt"
    outside_path.write_text("outside", encoding="utf-8")
    with pytest.raises(ValueError, match="inside task workspace"):
        store.add_artifact("artifact-task", str(outside_path))


def test_remote_task_artifacts_are_serialized_for_replay(task_modules):
    store = task_modules.TaskStore()
    store.create_task(task_id="remote-artifact-task", trace_id="trace-remote")

    artifact = store.add_remote_artifact(
        "remote-artifact-task",
        "https://files.example.test/output/report.md?token=raw-secret&file=report",
        filename="report.md",
        file_size=128,
        metadata={"api_key": "sk-test-remote-secretvalue123"},
    )

    payload = task_modules.serialize_artifact(artifact)
    assert payload["filename"] == "report.md"
    assert payload["remoteUrl"] == "https://files.example.test/output/report.md?token=***&file=report"
    assert payload["filePath"] == "https://files.example.test/output/report.md?token=***&file=report"
    assert payload["isRemote"] is True
    assert payload["fileSize"] == 128
    assert payload["metadata"]["api_key"] == "***"
    assert [item.artifact_id for item in store.list_artifacts("remote-artifact-task")] == [
        artifact.artifact_id
    ]

    with pytest.raises(ValueError, match="http or https"):
        store.add_remote_artifact("remote-artifact-task", "file:///tmp/report.md")


def test_sse_printer_adds_task_id_and_reports_events(task_modules):
    from brain.core.printer import SSEPrinter

    output: List[str] = []
    events: List[Any] = []
    seq_values = iter([10, 11, 12, 13, 14])
    printer = SSEPrinter(
        output.append,
        "request-1",
        task_id="task-1",
        run_id="run-1",
        session_id="session-1",
        event_sink=events.append,
        seq_provider=lambda: next(seq_values),
    )

    printer.send("message-1", "tool_call", {"name": "deepsearch"}, None, False)
    printer.send("phase-1", "agent_phase", {"phase": "planning", "status": "started"}, None, False)
    printer.send("plan-1", "plan_created", {"title": "Plan", "steps": ["Search"]}, None, False)
    printer.send(
        "todo-1",
        "todo_list_updated",
        {"items": [{"title": "Search", "status": "running"}], "currentIndex": 0},
        None,
        False,
    )
    printer.send("tool-result-1", "tool_result", {"tool": "deepsearch", "failed": True, "error": "boom"}, None, True)

    assert len(events) == 5
    assert [event["seq"] for event in events] == [10, 11, 12, 13, 14]
    assert events[0]["requestId"] == "request-1"
    assert events[0]["taskId"] == "task-1"
    assert events[0]["runId"] == "run-1"
    assert events[0]["sessionId"] == "session-1"
    assert events[0]["messageType"] == "tool_call"
    assert events[0]["type"] == "tool_call_started"
    assert events[1]["messageType"] == "agent_phase"
    assert events[1]["type"] == "agent_progress"
    assert events[1]["resultMap"]["phase"] == "planning"
    assert events[2]["messageType"] == "plan_created"
    assert events[2]["type"] == "plan_created"
    assert events[2]["plan"]["title"] == "Plan"
    assert events[2]["resultMap"]["steps"] == ["Search"]
    assert events[3]["messageType"] == "todo_list_updated"
    assert events[3]["type"] == "todo_list_updated"
    assert events[3]["todoList"]["items"][0]["title"] == "Search"
    assert events[3]["resultMap"]["currentIndex"] == 0
    assert events[4]["messageType"] == "tool_result"
    assert events[4]["type"] == "tool_call_failed"
    assert events[4]["resultMap"]["error"] == "boom"

    streamed_payload = json.loads(output[0].removeprefix("data: ").strip())
    assert streamed_payload["seq"] == 10
    assert streamed_payload["taskId"] == "task-1"
    assert streamed_payload["runId"] == "run-1"
    assert streamed_payload["sessionId"] == "session-1"
    assert streamed_payload["type"] == "tool_call_started"
    assert streamed_payload["toolCall"]["name"] == "deepsearch"
    failed_payload = json.loads(output[-1].removeprefix("data: ").strip())
    assert failed_payload["seq"] == 14
    assert failed_payload["messageType"] == "tool_result"
    assert failed_payload["type"] == "tool_call_failed"
    assert failed_payload["resultMap"]["error"] == "boom"


def test_memory_context_respects_agent_read_scope(task_modules, monkeypatch):
    import brain.app as app_module

    app_module = importlib.reload(app_module)
    calls: List[dict[str, Any]] = []

    class FakeMemoryManager:
        def get_search_config(self):
            return {"memory_enabled": True, "rag_enabled": True}

        def unified_search(self, **kwargs):
            calls.append(kwargs)
            return {
                "memory_results": [{"id": "memory-1", "content": "remember this"}]
                if kwargs["memory_limit"]
                else [],
                "rag_results": [{"id": "doc-1", "content": "knowledge"}] if kwargs["rag_limit"] else [],
                "warnings": [],
            }

    monkeypatch.setattr(app_module, "memory_manager", FakeMemoryManager())
    base_context = {
        "requestId": "memory-scope-request",
        "sessionId": "memory-scope-session",
        "user_id": "user-1",
        "agent_id": "memory-agent",
        "run_id": "conversation-1",
        "query": "hello",
        "task": None,
        "printer": None,
        "toolCollection": None,
        "dateInfo": "2026-05-30",
    }

    disabled_ctx = app_module.AgentContext(**base_context, agent_memory={"read": []})
    disabled_payload = asyncio.run(app_module._load_task_memory_context(disabled_ctx, "hello"))

    assert calls == []
    assert disabled_payload["memoryEnabled"] is False
    assert disabled_payload["ragEnabled"] is False
    assert app_module._memory_context_status_text(disabled_payload) == "上下文检索已按 Agent 配置关闭"

    memory_only_ctx = app_module.AgentContext(**base_context, agent_memory={"read": ["task_history"]})
    memory_payload = asyncio.run(app_module._load_task_memory_context(memory_only_ctx, "hello"))

    assert calls[-1]["memory_limit"] == 5
    assert calls[-1]["rag_limit"] == 0
    assert memory_payload["memoryCount"] == 1
    assert memory_payload["ragCount"] == 0


def test_autoagent_persists_task_lifecycle_and_stream_events(task_modules, monkeypatch):
    pytest.importorskip("fastapi")

    import brain.app as app_module
    from brain.core.tools.collection import ToolCollection

    app_module = importlib.reload(app_module)

    async def fake_build_tool_collection(_ctx):
        return ToolCollection()

    class FakeMemoryManager:
        def get_search_config(self):
            return {"memory_enabled": True, "rag_enabled": True}

        def unified_search(self, **kwargs):
            assert kwargs["query"] == "hello"
            assert kwargs["user_id"] == "user-autoagent"
            assert kwargs["agent_id"] == "agent-autoagent"
            assert kwargs["run_id"] == "trace-autoagent"
            return {
                "memory_results": [
                    {
                        "id": "memory-1",
                        "content": "remember public context",
                        "metadata": {"source_file": "notes.md", "api_key": "hidden"},
                        "score": 0.91,
                        "source": "memory",
                    }
                ],
                "rag_results": [
                    {
                        "id": "doc-1",
                        "content": "knowledge base context",
                        "metadata": {"title": "demo"},
                        "score": 0.82,
                    }
                ],
                "warnings": [],
            }

    class FakeHandler:
        async def handle(self, ctx, _request):
            assert ctx.language == "en"
            assert ctx.memory_context["memoryCount"] == 1
            assert ctx.memory_context["ragCount"] == 1
            assert ctx.memory_context["memoryResults"][0]["metadata"]["api_key"] == "***"
            Path(ctx.work_dir, "reports").mkdir(parents=True)
            Path(ctx.work_dir, "reports", "local_report.txt").write_text("local artifact", encoding="utf-8")
            ctx.printer.send(
                "plan-1",
                "plan_created",
                {
                    "title": "Demo Plan",
                    "steps": ["Collect evidence", "Write answer"],
                    "step_status": ["running", "not_started"],
                    "notes": ["", ""],
                    "command": "create",
                },
                None,
                True,
            )
            ctx.printer.send(
                "phase-1",
                "agent_phase",
                {"phase": "react", "status": "started", "agent": "react"},
                None,
                True,
            )
            ctx.printer.send(
                "tool-1",
                "tool_result",
                {
                        "tool": "mcp_local:code_interpreter",
                        "durationMs": 25,
                        "result": json.dumps(
                        {
                            "fileInfo": [
                                {
                                    "fileName": "analysis.txt",
                                    "download_url": "https://files.example.test/analysis.txt",
                                    "fileSize": 42,
                                }
                            ]
                        }
                    ),
                },
                None,
                True,
            )
            ctx.printer.send("result-1", "result", "final ", None, False)
            ctx.printer.send("result-2", "result", "answer", None, False)

    monkeypatch.setattr(app_module, "build_tool_collection", fake_build_tool_collection)
    monkeypatch.setattr(app_module, "memory_manager", FakeMemoryManager())
    monkeypatch.setattr(app_module.agentFactory, "get_handler", lambda _ctx, _request: FakeHandler())

    output: List[str] = []
    req = app_module.GptQueryReq(
        trace_id="trace-autoagent",
        user_id="user-autoagent",
        agent_id="agent-autoagent",
        conversation_id="conversation-autoagent",
        mode="react",
        outputStyle="markdown",
        run_environment="sandbox",
        language="en",
        messages=[app_module.AgentMessage(role="user", content="hello")],
    )

    asyncio.run(app_module._run_autoagent(req, output.append))

    store = task_modules.TaskStore()
    task = store.get_task("trace-autoagent")
    assert task is not None
    assert task.status == task_modules.AgentTaskStatus.COMPLETED
    assert task.output_text == "final answer"
    assert task_modules.serialize_task(task)["metadata"]["runEnvironment"] == "sandbox"
    assert task_modules.serialize_task(task)["metadata"]["language"] == "en"

    event_types = [event.event_type for event in store.list_events("trace-autoagent")]
    assert "task_created" in event_types
    assert "user_message_created" in event_types
    assert "task_running" in event_types
    assert "agent_started" in event_types
    assert "memory_context_loaded" in event_types
    assert "plan_created" in event_types
    assert "plan_completed" in event_types
    assert "agent_phase" in event_types
    assert "runtime_boundary_applied" in event_types
    assert "tool_policy_applied" in event_types
    assert "tool_result" in event_types
    assert "task_artifact_added" in event_types
    assert "result" in event_types
    assert "assistant_message_started" in event_types
    assert "assistant_message_completed" in event_types
    assert "agent_completed" in event_types
    assert "task_completed" in event_types
    assert event_types.index("assistant_message_started") < event_types.index("result")
    assert event_types.index("assistant_message_started") < event_types.index("assistant_message_completed")

    artifacts = store.list_artifacts("trace-autoagent")
    assert len(artifacts) == 2
    artifact_payloads = [task_modules.serialize_artifact(item) for item in artifacts]
    remote_artifact = next(item for item in artifact_payloads if item["isRemote"])
    local_artifact = next(item for item in artifact_payloads if not item["isRemote"])
    assert remote_artifact["filename"] == "analysis.txt"
    assert remote_artifact["remoteUrl"] == "https://files.example.test/analysis.txt"
    assert local_artifact["filename"] == "local_report.txt"
    assert local_artifact["metadata"]["source"] == "task_workspace"
    assert local_artifact["metadata"]["relativePath"] == "reports/local_report.txt"

    artifact_events = [
        event for event in store.list_events("trace-autoagent") if event.event_type == "task_artifact_added"
    ]
    artifact_event_payloads = [task_modules.serialize_event(event)["payload"] for event in artifact_events]
    assert len(artifact_event_payloads) == 2
    assert {item["type"] for item in artifact_event_payloads} == {"artifact_created"}
    assert {item["sessionId"] for item in artifact_event_payloads} == {"conversation-autoagent"}
    assert {item["runId"] for item in artifact_event_payloads} == {"trace-autoagent"}
    assert {item["filename"] for item in artifact_event_payloads} == {"analysis.txt", "local_report.txt"}
    remote_event_payload = next(item for item in artifact_event_payloads if item["isRemote"])
    assert remote_event_payload["remoteUrl"] == "https://files.example.test/analysis.txt"

    task_payload = task_modules.serialize_task(store.get_task("trace-autoagent"))
    assert task_payload["usage"]["toolResults"] == 1
    assert task_payload["usage"]["toolDurationMs"] == 25
    runtime_event = next(
        event for event in store.list_events("trace-autoagent") if event.event_type == "runtime_boundary_applied"
    )
    runtime_payload = task_modules.serialize_event(runtime_event)["payload"]
    assert runtime_payload["runEnvironment"] == "sandbox"
    assert runtime_payload["workDir"] == task_payload["workDir"]
    assert runtime_payload["artifactPolicy"] == "task_workspace_only"
    memory_event = next(
        event for event in store.list_events("trace-autoagent") if event.event_type == "memory_context_loaded"
    )
    memory_payload = task_modules.serialize_event(memory_event)["payload"]
    assert memory_payload["scope"]["runId"] == "trace-autoagent"
    assert memory_payload["memoryCount"] == 1
    assert memory_payload["ragCount"] == 1
    assert memory_payload["memoryResults"][0]["snippet"] == "remember public context"
    assert memory_payload["memoryResults"][0]["metadata"]["api_key"] == "***"
    assert memory_payload["ragResults"][0]["metadata"]["title"] == "demo"
    agent_started_event = next(
        event for event in store.list_events("trace-autoagent") if event.event_type == "agent_started"
    )
    assert task_modules.serialize_event(agent_started_event)["payload"]["language"] == "en"

    streamed_events = [
        json.loads(item.removeprefix("data: ").strip())
        for item in output
        if item.startswith("data: {")
    ]
    assert all(isinstance(item.get("seq"), int) for item in streamed_events)
    assert [item["seq"] for item in streamed_events] == sorted(item["seq"] for item in streamed_events)
    assert streamed_events[0]["taskId"] == "trace-autoagent"
    assert streamed_events[0]["runId"] == "trace-autoagent"
    assert streamed_events[0]["sessionId"] == "conversation-autoagent"
    assert streamed_events[0]["type"] == "run_progress"
    agent_phase_stream_event = next(item for item in streamed_events if item["messageType"] == "agent_phase")
    assert agent_phase_stream_event["type"] == "agent_progress"
    result_stream_event = next(item for item in streamed_events if item["messageType"] == "result")
    assert result_stream_event["type"] == "assistant_text_delta"
    tool_result_stream_event = next(item for item in streamed_events if item["messageType"] == "tool_result")

    session_store = app_module.SessionStore()
    session = session_store.get_session("conversation-autoagent")
    assert session is not None
    session_payload = app_module.serialize_session(session)
    assert session_payload["status"] == app_module.AgentSessionStatus.IDLE
    assert session_payload["currentRunId"] is None
    assert session_payload["lastMessagePreview"] == "final answer"
    session_messages = session_store.list_messages("conversation-autoagent")
    assert [message.role for message in session_messages] == [
        app_module.AgentMessageRole.USER,
        app_module.AgentMessageRole.ASSISTANT,
    ]
    assert session_messages[0].content == "hello"
    assert session_messages[1].content == "final answer"
    assert result_stream_event["assistantMessageId"] == session_messages[1].message_id

    stored_tool_event = next(
        event for event in store.list_events("trace-autoagent") if event.event_type == "tool_result"
    )
    stored_tool_payload = task_modules.serialize_event(stored_tool_event)["payload"]
    stored_tool_serialized = task_modules.serialize_event(stored_tool_event)
    assert stored_tool_payload["runId"] == "trace-autoagent"
    assert stored_tool_payload["sessionId"] == "conversation-autoagent"
    assert stored_tool_payload["seq"] == tool_result_stream_event["seq"]
    assert stored_tool_payload["type"] == "tool_call_completed"
    assert tool_result_stream_event["eventId"] == stored_tool_serialized["eventId"]
    user_message_event = next(
        event for event in store.list_events("trace-autoagent") if event.event_type == "user_message_created"
    )
    user_message_payload = task_modules.serialize_event(user_message_event)["payload"]
    assert user_message_event.message_id == session_messages[0].message_id
    assert user_message_payload["sessionId"] == "conversation-autoagent"
    assert user_message_payload["runId"] == "trace-autoagent"
    assert user_message_payload["role"] == app_module.AgentMessageRole.USER
    assert user_message_payload["contentPreview"] == "hello"
    assistant_started_event = next(
        event for event in store.list_events("trace-autoagent") if event.event_type == "assistant_message_started"
    )
    assistant_started_payload = task_modules.serialize_event(assistant_started_event)["payload"]
    assert assistant_started_event.message_id == session_messages[1].message_id
    assert assistant_started_payload["sessionId"] == "conversation-autoagent"
    assert assistant_started_payload["runId"] == "trace-autoagent"
    assert assistant_started_payload["role"] == app_module.AgentMessageRole.ASSISTANT
    assert assistant_started_payload["status"] == "started"
    assert assistant_started_payload["contentPreview"] == ""
    assistant_message_event = next(
        event for event in store.list_events("trace-autoagent") if event.event_type == "assistant_message_completed"
    )
    assistant_message_payload = task_modules.serialize_event(assistant_message_event)["payload"]
    assert assistant_message_event.message_id == session_messages[1].message_id
    assert assistant_message_payload["role"] == app_module.AgentMessageRole.ASSISTANT
    assert assistant_message_payload["status"] == "final"
    assert assistant_message_payload["contentPreview"] == "final answer"
    plan_completed_event = next(
        event for event in store.list_events("trace-autoagent") if event.event_type == "plan_completed"
    )
    plan_completed_payload = task_modules.serialize_event(plan_completed_event)["payload"]
    assert plan_completed_payload["plan"]["planStatus"] == "completed"
    assert plan_completed_payload["plan"]["step_status"] == ["completed", "completed"]
    completed_task_payload = task_modules.serialize_task(store.get_task("trace-autoagent"))
    assert completed_task_payload["latestPlan"]["planStatus"] == "completed"
    assert completed_task_payload["latestPlanEventType"] == "plan_completed"


def test_autoagent_renews_background_dispatch_lease(task_modules, monkeypatch):
    pytest.importorskip("fastapi")

    import brain.app as app_module
    from brain.core.tools.collection import ToolCollection

    app_module = importlib.reload(app_module)
    store = task_modules.TaskStore()
    store.create_task(
        task_id="trace-renew-autoagent",
        trace_id="trace-renew-autoagent",
        user_id="user-autoagent",
        agent_id="agent-autoagent",
        conversation_id="conversation-autoagent",
        mode="react",
        output_style="markdown",
        input_text="renew lease",
    )
    started = store.mark_background_dispatch_started(
        "trace-renew-autoagent",
        owner=app_module.BACKGROUND_RUNNER_OWNER,
        lease_ms=1,
    )
    original_dispatch = task_modules.serialize_task(started)["metadata"]["backgroundDispatch"]
    time.sleep(0.01)

    async def fake_build_tool_collection(_ctx):
        return ToolCollection()

    class FakeMemoryManager:
        def get_search_config(self):
            return {"memory_enabled": False, "rag_enabled": False}

    class FakeHandler:
        async def handle(self, ctx, _request):
            await asyncio.sleep(0.05)
            ctx.printer.send("result-1", "result", "done", None, False)

    monkeypatch.setattr(app_module, "BACKGROUND_LEASE_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(app_module, "build_tool_collection", fake_build_tool_collection)
    monkeypatch.setattr(app_module, "memory_manager", FakeMemoryManager())
    monkeypatch.setattr(app_module.agentFactory, "get_handler", lambda _ctx, _request: FakeHandler())

    output: List[str] = []
    req = app_module.GptQueryReq(
        trace_id="trace-renew-autoagent",
        user_id="user-autoagent",
        agent_id="agent-autoagent",
        conversation_id="conversation-autoagent",
        mode="react",
        outputStyle="markdown",
        messages=[app_module.AgentMessage(role="user", content="renew lease")],
    )

    asyncio.run(app_module._run_autoagent(req, output.append))

    renewed_payload = task_modules.serialize_task(store.get_task("trace-renew-autoagent"))
    dispatch = renewed_payload["metadata"]["backgroundDispatch"]
    assert renewed_payload["status"] == task_modules.AgentTaskStatus.COMPLETED
    assert dispatch["owner"] == app_module.BACKGROUND_RUNNER_OWNER
    assert dispatch["renewedAt"]
    assert dispatch["leaseExpiresAt"] > original_dispatch["leaseExpiresAt"]


def test_autoagent_marks_existing_plan_failed_when_handler_fails(task_modules, monkeypatch):
    pytest.importorskip("fastapi")

    import brain.app as app_module
    from brain.core.tools.collection import ToolCollection

    app_module = importlib.reload(app_module)

    async def fake_build_tool_collection(_ctx):
        return ToolCollection()

    class FakeHandler:
        async def handle(self, ctx, _request):
            ctx.printer.send(
                "plan-failed-1",
                "plan_created",
                {
                    "title": "Failing Plan",
                    "steps": ["Do risky work", "Summarize"],
                    "step_status": ["running", "not_started"],
                    "notes": ["", ""],
                    "command": "create",
                },
                None,
                True,
            )
            raise RuntimeError("planned failure")

    monkeypatch.setattr(app_module, "build_tool_collection", fake_build_tool_collection)
    monkeypatch.setattr(app_module.agentFactory, "get_handler", lambda _ctx, _request: FakeHandler())

    output: List[str] = []
    req = app_module.GptQueryReq(
        trace_id="trace-plan-failed",
        user_id="user-autoagent",
        agent_id="agent-autoagent",
        conversation_id="conversation-plan-failed",
        mode="react",
        outputStyle="markdown",
        messages=[app_module.AgentMessage(role="user", content="hello")],
    )

    asyncio.run(app_module._run_autoagent(req, output.append))

    store = task_modules.TaskStore()
    task = store.get_task("trace-plan-failed")
    assert task.status == task_modules.AgentTaskStatus.FAILED
    event_types = [event.event_type for event in store.list_events("trace-plan-failed")]
    assert event_types.index("plan_failed") < event_types.index("task_failed")
    plan_failed_event = next(
        event for event in store.list_events("trace-plan-failed") if event.event_type == "plan_failed"
    )
    plan_failed_payload = task_modules.serialize_event(plan_failed_event)["payload"]
    assert plan_failed_payload["plan"]["planStatus"] == "failed"
    assert plan_failed_payload["plan"]["step_status"] == ["failed", "not_started"]
    assert plan_failed_payload["reason"] == "planned failure"
    failed_task_payload = task_modules.serialize_task(store.get_task("trace-plan-failed"))
    assert failed_task_payload["latestPlan"]["planStatus"] == "failed"
    assert failed_task_payload["latestPlanEventType"] == "plan_failed"


def test_autoagent_keeps_task_waiting_when_agent_requests_input(task_modules, monkeypatch):
    pytest.importorskip("fastapi")

    import brain.app as app_module
    from brain.core.tools.collection import ToolCollection

    app_module = importlib.reload(app_module)

    async def fake_build_tool_collection(_ctx):
        return ToolCollection()

    class FakeHandler:
        async def handle(self, ctx, _request):
            ctx.waiting_for_input = True
            ctx.waiting_input_prompt = "Need account id"

    monkeypatch.setattr(app_module, "build_tool_collection", fake_build_tool_collection)
    monkeypatch.setattr(app_module.agentFactory, "get_handler", lambda _ctx, _request: FakeHandler())

    output: List[str] = []
    req = app_module.GptQueryReq(
        trace_id="trace-waiting-autoagent",
        user_id="user-autoagent",
        agent_id="agent-autoagent",
        conversation_id="conversation-autoagent",
        mode="react",
        outputStyle="markdown",
        messages=[app_module.AgentMessage(role="user", content="hello")],
    )

    asyncio.run(app_module._run_autoagent(req, output.append))

    store = task_modules.TaskStore()
    task = store.get_task("trace-waiting-autoagent")
    event_types = [event.event_type for event in store.list_events("trace-waiting-autoagent")]
    assert task.status == task_modules.AgentTaskStatus.WAITING_INPUT
    assert "waiting_input" in event_types
    assert "task_completed" not in event_types
    assert "agent_completed" not in event_types

    session_store = app_module.SessionStore()
    session = session_store.get_session("conversation-autoagent")
    assert session is not None
    session_payload = app_module.serialize_session(session)
    assert session_payload["status"] == app_module.AgentSessionStatus.WAITING_INPUT
    assert session_payload["currentRunId"] == "trace-waiting-autoagent"
    assert session_payload["lastMessagePreview"] == "Need account id"
    messages = session_store.list_messages("conversation-autoagent")
    assert [message.role for message in messages] == [
        app_module.AgentMessageRole.USER,
        app_module.AgentMessageRole.ASSISTANT,
    ]
    assert messages[-1].content == "Need account id"
    assert messages[-1].status == app_module.AgentSessionStatus.WAITING_INPUT
    completed_event = next(
        event
        for event in store.list_events("trace-waiting-autoagent")
        if event.event_type == "assistant_message_completed"
    )
    completed_payload = task_modules.serialize_event(completed_event)["payload"]
    assert completed_payload["status"] == app_module.AgentSessionStatus.WAITING_INPUT
    assert completed_payload["contentPreview"] == "Need account id"


def test_autoagent_records_resume_event_for_existing_task(task_modules, monkeypatch):
    pytest.importorskip("fastapi")

    import brain.app as app_module
    from brain.core.tools.collection import ToolCollection

    app_module = importlib.reload(app_module)
    store = task_modules.TaskStore()
    store.create_task(
        task_id="trace-resume-autoagent",
        trace_id="trace-resume-autoagent",
        user_id="user-autoagent",
        agent_id="agent-autoagent",
        conversation_id="conversation-autoagent",
        mode="react",
        output_style="markdown",
        input_text="original question",
        metadata={
            "inputFiles": [
                {
                    "fileName": "source.csv",
                    "ossUrl": "https://files.example.test/source.csv",
                    "fileSize": 10,
                }
            ],
            "runEnvironment": "sandbox",
        },
    )

    async def fake_build_tool_collection(_ctx):
        return ToolCollection()

    class FakeMemoryManager:
        def get_search_config(self):
            return {"memory_enabled": False, "rag_enabled": False}

    class FakeHandler:
        async def handle(self, ctx, _request):
            assert ctx.query == "new account id"
            assert ctx.messages[0].content == "original question"
            ctx.printer.send("result-1", "result", "done", None, False)

    monkeypatch.setattr(app_module, "build_tool_collection", fake_build_tool_collection)
    monkeypatch.setattr(app_module, "memory_manager", FakeMemoryManager())
    monkeypatch.setattr(app_module.agentFactory, "get_handler", lambda _ctx, _request: FakeHandler())

    output: List[str] = []
    req = app_module.GptQueryReq(
        trace_id="trace-resume-autoagent",
        user_id="user-autoagent",
        agent_id="agent-autoagent",
        conversation_id="conversation-autoagent",
        mode="react",
        outputStyle="markdown",
        run_environment="sandbox",
        messages=[
            app_module.AgentMessage(role="user", content="original question"),
            app_module.AgentMessage(role="user", content="new account id"),
        ],
    )

    asyncio.run(app_module._run_autoagent(req, output.append))

    task = store.get_task("trace-resume-autoagent")
    events = store.list_events("trace-resume-autoagent")
    event_types = [event.event_type for event in events]
    resume_event = next(event for event in events if event.event_type == "task_resumed")
    resume_payload = task_modules.serialize_event(resume_event)["payload"]

    assert task.status == task_modules.AgentTaskStatus.COMPLETED
    assert task.input_text == "original question"
    assert "task_resumed" in event_types
    assert "task_created" not in event_types
    assert resume_payload["status"] == task_modules.AgentTaskStatus.RUNNING
    assert resume_payload["inputFiles"][0]["fileName"] == "source.csv"


def test_autoagent_does_not_resume_cancelled_task(task_modules, monkeypatch):
    pytest.importorskip("fastapi")

    import brain.app as app_module

    app_module = importlib.reload(app_module)
    store = task_modules.TaskStore()
    store.create_task(
        task_id="trace-cancelled-before-start",
        trace_id="trace-cancelled-before-start",
        user_id="user-autoagent",
        agent_id="agent-autoagent",
        conversation_id="conversation-autoagent",
        mode="react",
        output_style="markdown",
        input_text="original question",
    )
    store.update_status("trace-cancelled-before-start", task_modules.AgentTaskStatus.CANCELLED)

    handler_called = False

    def fake_get_handler(_ctx, _request):
        nonlocal handler_called
        handler_called = True
        raise AssertionError("cancelled task should not start a handler")

    monkeypatch.setattr(app_module.agentFactory, "get_handler", fake_get_handler)

    output: List[str] = []
    req = app_module.GptQueryReq(
        trace_id="trace-cancelled-before-start",
        user_id="user-autoagent",
        agent_id="agent-autoagent",
        conversation_id="conversation-autoagent",
        mode="react",
        outputStyle="markdown",
        messages=[app_module.AgentMessage(role="user", content="original question")],
    )

    asyncio.run(app_module._run_autoagent(req, output.append))

    task = store.get_task("trace-cancelled-before-start")
    event_types = [event.event_type for event in store.list_events("trace-cancelled-before-start")]

    assert task.status == task_modules.AgentTaskStatus.CANCELLED
    assert handler_called is False
    assert "task_resumed" not in event_types
    assert "task_running" not in event_types
    assert "trace-cancelled-before-start" not in app_module.runningAgentTasks
    assert output[-1].strip() == "data: [DONE]"
