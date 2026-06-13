from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture()
def session_context_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_DB_URL", f"sqlite:///{tmp_path / 'session-context.db'}")
    monkeypatch.setenv(
        "APP_CONFIG_FILE",
        str(Path(__file__).resolve().parents[3] / "config" / "config.yaml"),
    )

    import file.db_engine as db_engine

    db_engine.get_engine.cache_clear()

    import brain.core.sessions as sessions
    import brain.core.session_context as session_context

    sessions = importlib.reload(sessions)
    session_context = importlib.reload(session_context)
    yield sessions, session_context

    db_engine.get_engine.cache_clear()


def test_session_context_restores_message_files_and_roles(session_context_modules):
    sessions, session_context = session_context_modules
    store = sessions.SessionStore()
    store.create_session(session_id="ctx-files", user_id="user-1")
    message = store.add_message(
        "ctx-files",
        message_id="file-message",
        role=sessions.AgentMessageRole.ASSISTANT,
        content="attached result",
        metadata={
            "inputFiles": [
                {
                    "fileName": "source.csv",
                    "description": "input data",
                    "fileSize": 123,
                },
                {"description": "missing filename"},
            ]
        },
    )

    agent_message = session_context.agent_message_from_session_message(message)

    assert agent_message.role == "assistant"
    assert agent_message.content == "attached result"
    assert len(agent_message.uploadFile or []) == 1
    assert agent_message.uploadFile[0].fileName == "source.csv"
    assert agent_message.uploadFile[0].fileSize == 123


def test_session_context_builds_summary_and_recent_history(session_context_modules):
    sessions, session_context = session_context_modules
    store = sessions.SessionStore()
    store.create_session(
        session_id="ctx-history",
        user_id="user-1",
        metadata={"summary": {"text": "Earlier decision: use Alpha."}},
    )
    store.add_message("ctx-history", message_id="old-1", role=sessions.AgentMessageRole.USER, content="old question")
    store.add_message("ctx-history", message_id="old-2", role=sessions.AgentMessageRole.ASSISTANT, content="old answer")
    current = store.add_message(
        "ctx-history",
        message_id="current",
        role=sessions.AgentMessageRole.USER,
        content="current question",
    )

    messages = session_context.build_session_model_messages(
        store,
        "ctx-history",
        [session_context.AgentMessage(role="user", content="current question")],
        current.message_id,
        history_limit=1,
    )

    assert [message.role for message in messages] == ["system", "assistant", "user"]
    assert "Earlier decision: use Alpha." in messages[0].content
    assert [message.content for message in messages[1:]] == ["old answer", "current question"]


def test_session_context_applies_character_budget_to_history(session_context_modules):
    sessions, session_context = session_context_modules
    store = sessions.SessionStore()
    store.create_session(session_id="ctx-budget", user_id="user-1")
    for index in range(5):
        store.add_message(
            "ctx-budget",
            message_id=f"history-{index}",
            role=sessions.AgentMessageRole.ASSISTANT,
            content=f"history {index} " + ("x" * 80),
        )
    current = store.add_message(
        "ctx-budget",
        message_id="current",
        role=sessions.AgentMessageRole.USER,
        content="current question",
    )

    messages = session_context.build_session_model_messages(
        store,
        "ctx-budget",
        [session_context.AgentMessage(role="user", content="current question")],
        current.message_id,
        history_limit=5,
        max_context_chars=80,
    )

    assert messages[-1].content == "current question"
    assert sum(len(message.content or "") for message in messages) <= 80
    assert "history 4" in messages[0].content
    assert all("history 0" not in message.content for message in messages)


def test_session_context_updates_summary_and_records_event(session_context_modules):
    sessions, session_context = session_context_modules

    class FakeTaskStore:
        def __init__(self) -> None:
            self.events = []

        def add_event(self, task_id, event_type, payload, *, trace_id=None, source=None):
            self.events.append(
                {
                    "task_id": task_id,
                    "event_type": event_type,
                    "payload": payload,
                    "trace_id": trace_id,
                    "source": source,
                }
            )

    store = sessions.SessionStore()
    store.create_session(session_id="ctx-summary", user_id="user-1")
    for index in range(5):
        role = sessions.AgentMessageRole.USER if index % 2 == 0 else sessions.AgentMessageRole.ASSISTANT
        store.add_message("ctx-summary", message_id=f"summary-{index}", role=role, content=f"detail {index}")

    task_store = FakeTaskStore()
    payload = session_context.maybe_update_session_summary(
        store,
        "ctx-summary",
        task_store=task_store,
        task_id="run-summary",
        trace_id="trace-summary",
        trigger_message_count=3,
        recent_message_count=2,
        now_ms=12345,
    )

    assert payload is not None
    assert payload["messageCount"] == 5
    assert payload["summarizedMessageCount"] == 3
    assert payload["recentMessageCount"] == 2
    assert payload["updatedAt"] == 12345
    assert "detail 0" in payload["text"]
    assert "detail 2" in payload["text"]
    session_payload = sessions.serialize_session(store.get_session("ctx-summary"))
    assert session_payload["metadata"]["summary"]["text"] == payload["text"]
    assert task_store.events[0]["event_type"] == "session_summary_updated"
    assert task_store.events[0]["source"] == "memory"
