from __future__ import annotations

import asyncio
import importlib
import json
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
    import brain.core.sessions as sessions
    import brain.app as app

    tasks = importlib.reload(tasks)
    sessions = importlib.reload(sessions)
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


def test_delete_task_removes_task_and_cancels_running_worker(app_modules):
    app, tasks = app_modules
    store = tasks.TaskStore()
    store.create_task(task_id="delete-api", trace_id="trace-delete-api", input_text="remove")
    store.update_status("delete-api", tasks.AgentTaskStatus.RUNNING)

    class RunningWorker:
        cancelled = False

        def done(self):
            return False

        def cancel(self):
            self.cancelled = True

    worker = RunningWorker()
    app.runningAgentTasks["delete-api"] = worker

    payload = asyncio.run(app.delete_agent_task("delete-api"))

    assert payload == {"taskId": "delete-api", "deleted": True}
    assert worker.cancelled is True
    assert "delete-api" not in app.runningAgentTasks
    assert store.get_task("delete-api") is None


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
            "language": "en",
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

    monkeypatch.setattr(
        app,
        "_resolve_agent_config",
        lambda agent_id: app.AgentConfig(id="agent-1", name="Retry Agent") if agent_id == "agent-1" else None,
    )
    monkeypatch.setattr(app.asyncio, "create_task", fake_create_task)
    payload = asyncio.run(app.retry_agent_task("retry-me"))

    assert payload["status"] == tasks.AgentTaskStatus.QUEUED
    assert payload["input"] == "retry this"
    assert payload["metadata"]["source"] == "retry"
    assert payload["metadata"]["parentTaskId"] == "retry-me"
    assert payload["metadata"]["runEnvironment"] == "sandbox"
    assert payload["metadata"]["language"] == "en"
    assert payload["metadata"]["approvedTools"] == ["mcp_local:code_interpreter"]
    assert payload["metadata"]["inputFiles"][0]["fileName"] == "source.csv"
    assert payload["metadata"]["agentSnapshot"]["name"] == "Retry Agent"
    assert app._deserialize_file_items(payload["metadata"]["inputFiles"])[0].fileName == "source.csv"
    assert created_background
    created_background[0].close()

    parent_events = store.list_events("retry-me")
    assert parent_events[-1].event_type == "task_retry_requested"
    retry_events = store.list_events(payload["taskId"])
    assert retry_events[0].event_type == "task_queued"
    assert tasks.serialize_event(retry_events[0])["payload"]["parentTaskId"] == "retry-me"
    assert tasks.serialize_event(retry_events[0])["payload"]["language"] == "en"
    assert tasks.serialize_event(retry_events[0])["payload"]["agentSnapshot"]["name"] == "Retry Agent"


def test_add_task_input_queues_resume_when_waiting(app_modules, monkeypatch):
    app, tasks = app_modules
    store = tasks.TaskStore()
    store.create_task(
        task_id="wait-for-input",
        trace_id="trace-wait-for-input",
        user_id="user-1",
        agent_id="agent-1",
        input_text="need account lookup",
    )
    store.request_user_input(
        "wait-for-input",
        "请补充账号 ID",
        trace_id="trace-wait-for-input",
        source="agent",
    )

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
        app.add_agent_task_input(
            "wait-for-input",
            app.TaskUserInputReq(content=" account-123 ", user_id="user-2", language="en"),
        )
    )

    assert payload["task"]["status"] == tasks.AgentTaskStatus.QUEUED
    assert payload["event"]["eventType"] == "user_input"
    assert payload["event"]["payload"]["content"] == "account-123"
    assert created_background

    events = store.list_events("wait-for-input")
    event_types = [event.event_type for event in events]
    assert event_types == [
        "waiting_input",
        "user_input",
        "task_queued",
        "task_resume_requested",
    ]
    queued_payload = tasks.serialize_event(events[-2])["payload"]
    assert queued_payload["reason"] == "user_input_received"
    assert queued_payload["language"] == "en"
    resume_payload = tasks.serialize_event(events[-1])["payload"]
    assert resume_payload["userInputEventId"] == payload["event"]["id"]
    assert resume_payload["language"] == "en"


def test_resume_task_after_input_replays_same_task_context(app_modules, monkeypatch):
    app, tasks = app_modules
    store = tasks.TaskStore()
    store.create_task(
        task_id="resume-original-task",
        trace_id="trace-resume-original-task",
        conversation_id="conversation-1",
        user_id="user-1",
        agent_id="agent-1",
        mode="react",
        output_style="markdown",
        input_text="lookup account",
        metadata={
            "selectedTools": ["mcp_local:deepsearch"],
            "approvedTools": ["mcp_local:code_interpreter"],
            "runEnvironment": "sandbox",
            "inputFiles": [
                {
                    "fileName": "source.csv",
                    "description": "input data",
                    "ossUrl": "https://files.example.test/source.csv",
                    "fileSize": 10,
                }
            ],
        },
    )

    captured = {}

    async def fake_run_autoagent(req, enqueue):
        captured["req"] = req
        captured["enqueue"] = enqueue

    monkeypatch.setattr(app, "_run_autoagent", fake_run_autoagent)

    asyncio.run(app._resume_task_after_input("resume-original-task", " account-123 "))

    req = captured["req"]
    assert req.trace_id == "resume-original-task"
    assert req.user_id == "user-1"
    assert req.agent_id == "agent-1"
    assert req.conversation_id == "conversation-1"
    assert req.outputStyle == "markdown"
    assert req.mode == "react"
    assert req.selected_tools == ["mcp_local:deepsearch"]
    assert req.approved_tools == ["mcp_local:code_interpreter"]
    assert req.run_environment == "sandbox"
    assert req.language == "ch"
    assert req.messages[0].content == "lookup account"
    assert req.messages[0].uploadFile[0].fileName == "source.csv"
    assert req.messages[1].content == "用户补充输入：account-123"


def test_resume_task_after_input_can_continue_in_english(app_modules, monkeypatch):
    app, tasks = app_modules
    store = tasks.TaskStore()
    store.create_task(
        task_id="resume-english-task",
        trace_id="trace-resume-english-task",
        conversation_id="conversation-1",
        user_id="user-1",
        agent_id="agent-1",
        mode="react",
        output_style="markdown",
        input_text="lookup account",
        metadata={"language": "en"},
    )

    captured = {}

    async def fake_run_autoagent(req, enqueue):
        captured["req"] = req
        captured["enqueue"] = enqueue

    monkeypatch.setattr(app, "_run_autoagent", fake_run_autoagent)

    asyncio.run(app._resume_task_after_input("resume-english-task", " account-123 "))

    req = captured["req"]
    assert req.language == "en"
    assert req.messages[1].content == "User supplemental input: account-123"


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
                language="en",
                approved_tools=["mcp_local:code_interpreter"],
                messages=[app.AgentMessage(role="user", content="run in background")],
            )
        )
    )

    assert payload["taskId"]
    assert payload["runId"] == payload["taskId"]
    assert payload["sessionId"] == "conversation-1"
    assert payload["conversationId"] == "conversation-1"
    assert payload["status"] == tasks.AgentTaskStatus.QUEUED
    assert payload["metadata"]["source"] == "api"
    assert payload["metadata"]["runEnvironment"] == "sandbox"
    assert payload["metadata"]["language"] == "en"
    assert payload["metadata"]["approvedTools"] == ["mcp_local:code_interpreter"]
    assert payload["metadata"]["agentSnapshot"]["id"] == "task-pilot-agent"
    assert payload["metadata"]["agentSnapshot"]["name"] == "TaskPilot 默认 Agent"
    assert created_background
    background_req = created_background[0].cr_frame.f_locals["req"]
    assert background_req.trace_id == payload["taskId"]
    assert background_req.language == "en"
    created_background[0].close()

    store = tasks.TaskStore()
    events = store.list_events(payload["taskId"])
    assert events[-1].event_type == "task_queued"
    assert tasks.serialize_event(events[-1])["payload"]["runEnvironment"] == "sandbox"
    assert tasks.serialize_event(events[-1])["payload"]["language"] == "en"
    assert tasks.serialize_event(events[-1])["payload"]["approvedTools"] == ["mcp_local:code_interpreter"]
    assert tasks.serialize_event(events[-1])["payload"]["agentSnapshot"]["id"] == "task-pilot-agent"

    detail = asyncio.run(app.get_agent_task(payload["taskId"], current_user=SimpleNamespace(user_id="user-1")))
    assert detail["taskId"] == payload["taskId"]
    assert detail["runId"] == payload["taskId"]
    assert detail["sessionId"] == "conversation-1"

    listed = asyncio.run(
        app.list_agent_tasks(
            status=None,
            keyword=None,
            user_id="user-1",
            agent_id=None,
            agent_type=None,
            created_from=None,
            created_to=None,
            min_duration_ms=None,
            max_duration_ms=None,
            has_error=None,
            limit=50,
            offset=0,
            current_user=SimpleNamespace(user_id="user-1"),
        )
    )
    assert listed["items"][0]["runId"] == payload["taskId"]
    assert listed["items"][0]["sessionId"] == "conversation-1"


def test_create_task_api_defaults_session_to_run_id(app_modules, monkeypatch):
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
                trace_id="run-with-default-session",
                user_id="user-1",
                agent_id="task-pilot-agent",
                messages=[app.AgentMessage(role="user", content="run without session")],
            )
        )
    )

    assert payload["taskId"] == "run-with-default-session"
    assert payload["sessionId"] == "run-with-default-session"
    assert payload["conversationId"] == "run-with-default-session"
    stored_task = tasks.serialize_task(tasks.TaskStore().get_task("run-with-default-session"))
    assert stored_task["sessionId"] == "run-with-default-session"
    assert stored_task["mode"] == "react"
    background_req = created_background[0].cr_frame.f_locals["req"]
    assert background_req.conversation_id == "run-with-default-session"
    created_background[0].close()


def test_session_api_creates_lists_updates_and_loads_detail(app_modules):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")

    created = asyncio.run(
        app.create_agent_session(
            app.AgentSessionCreateReq(
                session_id="session-api-1",
                title="Research chat",
                agent_id="task-pilot-agent",
                metadata={"token": "raw-secret"},
            ),
            current_user=current_user,
        )
    )

    assert created["sessionId"] == "session-api-1"
    assert created["userId"] == "user-1"
    assert created["metadata"]["token"] == "***"

    session_store = app.SessionStore()
    session_store.create_session(session_id="session-other-user", user_id="user-2", title="Other")
    first_message = session_store.add_message(
        "session-api-1",
        run_id="run-1",
        user_id="user-1",
        role=app.AgentMessageRole.USER,
        content="hello detail",
    )
    second_message = session_store.add_message(
        "session-api-1",
        message_id="session-api-message-2",
        run_id="run-1",
        user_id="user-1",
        role=app.AgentMessageRole.ASSISTANT,
        content="assistant detail",
    )
    third_message = session_store.add_message(
        "session-api-1",
        message_id="session-api-message-3",
        run_id="run-1",
        user_id="user-1",
        role=app.AgentMessageRole.USER,
        content="latest detail",
    )
    fourth_message = session_store.add_message(
        "session-api-1",
        message_id="session-api-message-4",
        run_id="run-1",
        user_id="user-1",
        role=app.AgentMessageRole.ASSISTANT,
        content="assistant later detail",
    )
    fifth_message = session_store.add_message(
        "session-api-1",
        message_id="session-api-message-5",
        run_id="run-1",
        user_id="user-1",
        role=app.AgentMessageRole.USER,
        content="newest detail",
    )
    task_store = tasks.TaskStore()
    task_store.create_task(
        task_id="run-1",
        trace_id="run-1",
        conversation_id="session-api-1",
        user_id="user-1",
        input_text="hello detail",
    )
    task_store.add_remote_artifact(
        "run-1",
        "https://files.example.test/report.md",
        filename="report.md",
    )
    session_store.update_session(
        "session-api-1",
        status=app.AgentSessionStatus.RUNNING,
        current_run_id="run-1",
    )

    listed = asyncio.run(
        app.list_agent_sessions(
            status=None,
            keyword=None,
            include_archived=False,
            limit=50,
            offset=0,
            current_user=current_user,
        )
    )
    assert [item["sessionId"] for item in listed["items"]] == ["session-api-1"]
    assert listed["count"] == 1

    active_listed = asyncio.run(
        app.list_agent_sessions(
            status="active",
            keyword=None,
            include_archived=False,
            limit=50,
            offset=0,
            current_user=current_user,
        )
    )
    assert [item["sessionId"] for item in active_listed["items"]] == ["session-api-1"]
    assert active_listed["count"] == 1

    updated = asyncio.run(
        app.update_agent_session(
            "session-api-1",
            app.AgentSessionUpdateReq(title="Renamed", pinned=True),
            current_user=current_user,
        )
    )
    assert updated["title"] == "Renamed"
    assert updated["pinned"] is True

    detail = asyncio.run(
        app.get_agent_session(
            "session-api-1",
            messages_limit=100,
            runs_limit=50,
            current_user=current_user,
        )
    )
    assert detail["sessionId"] == "session-api-1"
    assert [item["content"] for item in detail["messages"]] == [
        "hello detail",
        "assistant detail",
        "latest detail",
        "assistant later detail",
        "newest detail",
    ]
    assert detail["messageCount"] == 5
    assert [item["taskId"] for item in detail["runs"]] == ["run-1"]
    assert detail["runCount"] == 1
    assert detail["currentRun"]["taskId"] == "run-1"
    assert detail["artifactSummary"]["count"] == 1
    assert detail["artifactSummary"]["items"][0]["runId"] == "run-1"
    assert detail["artifactSummary"]["items"][0]["filename"] == "report.md"

    before_payload = asyncio.run(
        app.list_agent_session_messages(
            "session-api-1",
            limit=100,
            offset=0,
            before=third_message.message_id,
            current_user=current_user,
        )
    )
    assert before_payload["before"] == third_message.message_id
    assert before_payload["count"] == 2
    assert before_payload["totalCount"] == 5
    assert before_payload["hasMore"] is False
    assert [item["messageId"] for item in before_payload["items"]] == [
        first_message.message_id,
        second_message.message_id,
    ]

    latest_page = asyncio.run(
        app.list_agent_session_messages(
            "session-api-1",
            limit=2,
            offset=0,
            before=fifth_message.message_id,
            current_user=current_user,
        )
    )
    assert latest_page["count"] == 4
    assert latest_page["totalCount"] == 5
    assert latest_page["hasMore"] is True
    assert [item["messageId"] for item in latest_page["items"]] == [
        third_message.message_id,
        fourth_message.message_id,
    ]

    full_page = asyncio.run(
        app.list_agent_session_messages(
            "session-api-1",
            limit=100,
            offset=0,
            before=None,
            current_user=current_user,
        )
    )
    assert full_page["count"] == 5
    assert full_page["totalCount"] == 5
    assert full_page["hasMore"] is False


def test_session_message_api_adds_user_message_and_starts_run(app_modules, monkeypatch):
    app, _tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    session_store = app.SessionStore()
    session_store.create_session(
        session_id="session-chat",
        user_id="user-1",
        agent_id="task-pilot-agent",
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

    payload = asyncio.run(
        app.add_agent_session_message(
            "session-chat",
            app.AgentSessionMessageReq(
                content="search this",
                mode="react",
                run_environment="sandbox",
                selected_tools=["mcp_local:web_search"],
            ),
            current_user=current_user,
        )
    )

    assert payload["sessionId"] == "session-chat"
    assert payload["runId"]
    assert payload["messageId"] == payload["message"]["messageId"]
    assert payload["status"] == app.AgentSessionStatus.RUNNING
    assert payload["message"]["content"] == "search this"
    assert created_background
    background_req = created_background[0].cr_frame.f_locals["req"]
    assert background_req.trace_id == payload["runId"]
    assert background_req.conversation_id == "session-chat"
    assert background_req.session_message_id == payload["message"]["messageId"]
    assert background_req.selected_tools == ["mcp_local:web_search"]
    created_background[0].close()

    updated_session = app.serialize_session(session_store.get_session("session-chat"))
    assert updated_session["status"] == app.AgentSessionStatus.RUNNING
    assert updated_session["currentRunId"] == payload["runId"]
    assert updated_session["lastMessageId"] == payload["message"]["messageId"]


def test_session_api_accepts_documented_camel_case_fields(app_modules, monkeypatch):
    app, _tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
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

    created = asyncio.run(
        app.create_agent_session(
            app.AgentSessionCreateReq(
                sessionId="session-camel",
                title="Camel session",
                agentId="search_agent",
            ),
            current_user=current_user,
        )
    )
    updated = asyncio.run(
        app.update_agent_session(
            "session-camel",
            app.AgentSessionUpdateReq(agentId="report_agent"),
            current_user=current_user,
        )
    )
    payload = asyncio.run(
        app.add_agent_session_message(
            "session-camel",
            app.AgentSessionMessageReq(
                content="search with documented fields",
                agentId="search_agent",
                mode="react",
                runEnvironment="sandbox",
                selectedTools=["mcp_local:web_search"],
                approvedTools=["mcp_local:code_interpreter"],
            ),
            current_user=current_user,
        )
    )

    assert created["sessionId"] == "session-camel"
    assert created["agentId"] == "search_agent"
    assert updated["agentId"] == "report_agent"
    assert payload["status"] == app.AgentSessionStatus.RUNNING
    assert payload["messageId"] == payload["message"]["messageId"]
    assert created_background
    background_req = created_background[0].cr_frame.f_locals["req"]
    assert background_req.agent_id == "search_agent"
    assert background_req.selected_tools == ["mcp_local:web_search"]
    assert background_req.approved_tools == ["mcp_local:code_interpreter"]
    assert background_req.run_environment == "sandbox"
    created_background[0].close()


def test_autoagent_uses_recent_session_messages_as_model_context(app_modules, monkeypatch):
    app, tasks = app_modules
    session_store = app.SessionStore()
    session_store.create_session(
        session_id="session-context",
        user_id="user-1",
        agent_id="agent-1",
    )
    session_store.add_message(
        "session-context",
        message_id="context-msg-1",
        run_id="old-run",
        user_id="user-1",
        role=app.AgentMessageRole.USER,
        content="My project is Alpha.",
    )
    session_store.add_message(
        "session-context",
        message_id="context-msg-2",
        run_id="old-run",
        user_id="user-1",
        role=app.AgentMessageRole.ASSISTANT,
        content="I will remember Alpha.",
    )
    current_message = session_store.add_message(
        "session-context",
        message_id="context-msg-3",
        run_id="context-run",
        user_id="user-1",
        role=app.AgentMessageRole.USER,
        content="What project did I mention?",
    )
    captured = {}

    class FakeHandler:
        async def handle(self, ctx, req):
            captured["ctx"] = ctx
            captured["req"] = req
            ctx.printer.send(None, "result", "You mentioned Alpha.", None, True)

    async def fake_memory_context(ctx, query):
        return {"memoryCount": 0, "ragCount": 0, "querySummary": query}

    async def fake_tool_collection(ctx):
        return SimpleNamespace(tool_map={}, blocked_tools=[])

    monkeypatch.setattr(
        app,
        "_resolve_agent_config",
        lambda agent_id: app.AgentConfig(id="agent-1", name="Context Agent", mode="react"),
    )
    monkeypatch.setattr(app, "_load_task_memory_context", fake_memory_context)
    monkeypatch.setattr(app, "build_tool_collection", fake_tool_collection)
    monkeypatch.setattr(app.agentFactory, "get_handler", lambda ctx, req: FakeHandler())

    asyncio.run(
        app._run_autoagent(
            app.GptQueryReq(
                trace_id="context-run",
                user_id="user-1",
                agent_id="agent-1",
                conversation_id="session-context",
                session_message_id=current_message.message_id,
                messages=[
                    app.AgentMessage(
                        role="user",
                        content="What project did I mention?",
                    )
                ],
            ),
            lambda _data: None,
        )
    )

    ctx = captured["ctx"]
    assert ctx.sessionId == "session-context"
    assert ctx.run_id == "context-run"
    assert ctx.mode == "react"
    assert ctx.query == "What project did I mention?"
    assert [(item.role, item.content) for item in ctx.messages] == [
        ("user", "My project is Alpha."),
        ("assistant", "I will remember Alpha."),
    ]
    assert [item.content for item in captured["req"].messages] == [
        "My project is Alpha.",
        "I will remember Alpha.",
        "What project did I mention?",
    ]
    task = tasks.serialize_task(tasks.TaskStore().get_task("context-run"))
    assert task["mode"] == "react"
    assert task["metadata"]["sessionHistoryMessageCount"] == 2
    events = tasks.TaskStore().list_events("context-run")
    assert tasks.serialize_event(events[0])["payload"]["sessionHistoryMessageCount"] == 2
    run_payload = app.serialize_run(app.SessionStore().get_run("context-run"))
    assert run_payload["sessionId"] == "session-context"
    assert run_payload["userMessageId"] == current_message.message_id
    assert run_payload["status"] == tasks.AgentTaskStatus.COMPLETED
    assert run_payload["output"] == "You mentioned Alpha."
    assert run_payload["metadata"]["sessionHistoryMessageCount"] == 2


def test_autoagent_defaults_to_react_when_agent_config_is_missing(app_modules, monkeypatch):
    app, tasks = app_modules
    captured = {}

    class FakeHandler:
        async def handle(self, ctx, req):
            captured["ctx"] = ctx
            captured["req"] = req
            ctx.printer.send(None, "result", "default mode answer", None, True)

    async def fake_memory_context(ctx, query):
        return {"memoryCount": 0, "ragCount": 0, "querySummary": query}

    async def fake_tool_collection(ctx):
        return SimpleNamespace(tool_map={}, blocked_tools=[])

    monkeypatch.setattr(app, "_resolve_agent_config", lambda agent_id: None)
    monkeypatch.setattr(app, "_load_task_memory_context", fake_memory_context)
    monkeypatch.setattr(app, "build_tool_collection", fake_tool_collection)
    monkeypatch.setattr(app.agentFactory, "get_handler", lambda ctx, req: FakeHandler())

    asyncio.run(
        app._run_autoagent(
            app.GptQueryReq(
                trace_id="default-react-run",
                user_id="user-1",
                agent_id="missing-agent",
                conversation_id="default-react-session",
                messages=[app.AgentMessage(role="user", content="hello")],
            ),
            lambda _data: None,
        )
    )

    assert captured["ctx"].mode == "react"
    task = tasks.serialize_task(tasks.TaskStore().get_task("default-react-run"))
    assert task["mode"] == "react"


def test_completed_run_updates_session_summary_and_reuses_it(app_modules, monkeypatch):
    app, tasks = app_modules
    monkeypatch.setattr(app, "SESSION_SUMMARY_TRIGGER_MESSAGE_COUNT", 4)
    monkeypatch.setattr(app, "SESSION_SUMMARY_RECENT_MESSAGE_COUNT", 2)
    monkeypatch.setattr(app, "SESSION_CONTEXT_HISTORY_LIMIT", 2)
    session_store = app.SessionStore()
    session_store.create_session(
        session_id="session-summary",
        user_id="user-1",
        agent_id="agent-1",
    )
    for index in range(4):
        role = app.AgentMessageRole.USER if index % 2 == 0 else app.AgentMessageRole.ASSISTANT
        session_store.add_message(
            "session-summary",
            message_id=f"summary-old-{index}",
            run_id="summary-old-run",
            user_id="user-1",
            role=role,
            content=f"old detail {index}",
        )
    current_message = session_store.add_message(
        "session-summary",
        message_id="summary-current-1",
        run_id="summary-run-1",
        user_id="user-1",
        role=app.AgentMessageRole.USER,
        content="summarize the old context",
    )
    captured_contexts = []

    class FakeHandler:
        async def handle(self, ctx, req):
            captured_contexts.append(ctx)
            ctx.printer.send(None, "result", f"answer {len(captured_contexts)}", None, True)

    async def fake_memory_context(ctx, query):
        return {"memoryCount": 0, "ragCount": 0, "querySummary": query}

    async def fake_tool_collection(ctx):
        return SimpleNamespace(tool_map={}, blocked_tools=[])

    monkeypatch.setattr(
        app,
        "_resolve_agent_config",
        lambda agent_id: app.AgentConfig(id="agent-1", name="Summary Agent", mode="react"),
    )
    monkeypatch.setattr(app, "_load_task_memory_context", fake_memory_context)
    monkeypatch.setattr(app, "build_tool_collection", fake_tool_collection)
    monkeypatch.setattr(app.agentFactory, "get_handler", lambda ctx, req: FakeHandler())

    asyncio.run(
        app._run_autoagent(
            app.GptQueryReq(
                trace_id="summary-run-1",
                user_id="user-1",
                agent_id="agent-1",
                conversation_id="session-summary",
                session_message_id=current_message.message_id,
                messages=[
                    app.AgentMessage(
                        role="user",
                        content="summarize the old context",
                    )
                ],
            ),
            lambda _data: None,
        )
    )

    session_payload = app.serialize_session(session_store.get_session("session-summary"))
    summary = session_payload["metadata"]["summary"]
    assert summary["messageCount"] == 6
    assert summary["summarizedMessageCount"] == 4
    assert "old detail 0" in summary["text"]
    assert "old detail 3" in summary["text"]

    summary_events = [
        tasks.serialize_event(event)
        for event in tasks.TaskStore().list_events("summary-run-1")
        if event.event_type == "session_summary_updated"
    ]
    assert summary_events
    assert summary_events[0]["payload"]["lastMessageId"]

    follow_up_message = session_store.add_message(
        "session-summary",
        message_id="summary-current-2",
        run_id="summary-run-2",
        user_id="user-1",
        role=app.AgentMessageRole.USER,
        content="what was in the old context?",
    )
    asyncio.run(
        app._run_autoagent(
            app.GptQueryReq(
                trace_id="summary-run-2",
                user_id="user-1",
                agent_id="agent-1",
                conversation_id="session-summary",
                session_message_id=follow_up_message.message_id,
                messages=[
                    app.AgentMessage(
                        role="user",
                        content="what was in the old context?",
                    )
                ],
            ),
            lambda _data: None,
        )
    )

    second_context = captured_contexts[-1]
    assert second_context.query == "what was in the old context?"
    assert second_context.messages[0].role == "system"
    assert "会话摘要" in second_context.messages[0].content
    assert "old detail 0" in second_context.messages[0].content
    assert [message.content for message in second_context.messages[-2:]] == [
        "summarize the old context",
        "answer 1",
    ]


def test_session_api_rejects_other_user_and_archived_message(app_modules):
    app, _tasks = app_modules
    session_store = app.SessionStore()
    session_store.create_session(session_id="private-session", user_id="user-2")
    session_store.create_session(session_id="archived-session", user_id="user-1")
    session_store.update_session("archived-session", archived=True)

    with pytest.raises(app.HTTPException) as other_user_exc:
        asyncio.run(
            app.get_agent_session(
                "private-session",
                messages_limit=100,
                runs_limit=50,
                current_user=SimpleNamespace(user_id="user-1"),
            )
        )
    assert other_user_exc.value.status_code == 404

    with pytest.raises(app.HTTPException) as archived_exc:
        asyncio.run(
            app.add_agent_session_message(
                "archived-session",
                app.AgentSessionMessageReq(content="hello"),
                current_user=SimpleNamespace(user_id="user-1"),
            )
        )
    assert archived_exc.value.status_code == 409


def test_session_message_api_rejects_new_message_while_running(app_modules, monkeypatch):
    app, _tasks = app_modules
    session_store = app.SessionStore()
    session_store.create_session(
        session_id="running-session",
        user_id="user-1",
        agent_id="task-pilot-agent",
    )
    session_store.update_session(
        "running-session",
        status=app.AgentSessionStatus.RUNNING,
        current_run_id="active-run",
    )

    def fail_create_task(_coro):
        raise AssertionError("running session must not start a new run")

    monkeypatch.setattr(app.asyncio, "create_task", fail_create_task)

    with pytest.raises(app.HTTPException) as exc_info:
        asyncio.run(
            app.add_agent_session_message(
                "running-session",
                app.AgentSessionMessageReq(content="new message"),
                current_user=SimpleNamespace(user_id="user-1"),
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "session is busy"
    assert session_store.list_messages("running-session") == []
    session_payload = app.serialize_session(session_store.get_session("running-session"))
    assert session_payload["status"] == app.AgentSessionStatus.RUNNING
    assert session_payload["currentRunId"] == "active-run"


def test_session_message_api_rejects_new_message_while_waiting_approval(app_modules, monkeypatch):
    app, _tasks = app_modules
    session_store = app.SessionStore()
    session_store.create_session(
        session_id="approval-wait-session",
        user_id="user-1",
        agent_id="task-pilot-agent",
    )
    session_store.update_session(
        "approval-wait-session",
        status=app.AgentSessionStatus.WAITING_APPROVAL,
        current_run_id="approval-run",
    )

    def fail_create_task(_coro):
        raise AssertionError("waiting approval session must not start a new run")

    monkeypatch.setattr(app.asyncio, "create_task", fail_create_task)

    with pytest.raises(app.HTTPException) as exc_info:
        asyncio.run(
            app.add_agent_session_message(
                "approval-wait-session",
                app.AgentSessionMessageReq(content="skip approval"),
                current_user=SimpleNamespace(user_id="user-1"),
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "session is waiting for approval"
    assert session_store.list_messages("approval-wait-session") == []
    session_payload = app.serialize_session(session_store.get_session("approval-wait-session"))
    assert session_payload["status"] == app.AgentSessionStatus.WAITING_APPROVAL
    assert session_payload["currentRunId"] == "approval-run"


def test_session_archive_and_delete_are_owner_scoped_soft_deletes(app_modules):
    app, tasks = app_modules
    session_store = app.SessionStore()
    task_store = tasks.TaskStore()
    current_user = SimpleNamespace(user_id="user-1")
    session_store.create_session(
        session_id="delete-session",
        user_id="user-1",
    )
    session_store.update_session("delete-session", current_run_id="run-delete-session", status=app.AgentSessionStatus.RUNNING)
    task_store.create_task(
        task_id="run-delete-session",
        trace_id="run-delete-session",
        conversation_id="delete-session",
        user_id="user-1",
        input_text="running work",
    )
    task_store.update_status("run-delete-session", tasks.AgentTaskStatus.RUNNING)

    class RunningWorker:
        cancelled = False

        def done(self):
            return False

        def cancel(self):
            self.cancelled = True

    worker = RunningWorker()
    app.runningAgentTasks["run-delete-session"] = worker

    payload = asyncio.run(app.delete_agent_session("delete-session", current_user=current_user))

    assert payload["sessionId"] == "delete-session"
    assert payload["deleted"] is True
    assert payload["archived"] is True
    assert payload["cancelledRunId"] == "run-delete-session"
    assert worker.cancelled is True
    assert "run-delete-session" not in app.runningAgentTasks

    archived = app.serialize_session(session_store.get_session("delete-session"))
    assert archived["status"] == app.AgentSessionStatus.ARCHIVED
    assert archived["currentRunId"] is None
    assert archived["archivedAt"]
    assert session_store.list_messages("delete-session") == []

    run = tasks.serialize_task(task_store.get_task("run-delete-session"))
    assert run["status"] == tasks.AgentTaskStatus.CANCELLED
    events = task_store.list_events("run-delete-session")
    assert events[-1].event_type == "task_cancel_requested"
    assert tasks.serialize_event(events[-1])["payload"]["reason"] == "session deleted"

    listed = asyncio.run(
        app.list_agent_sessions(
            status=None,
            keyword=None,
            include_archived=False,
            limit=50,
            offset=0,
            current_user=current_user,
        )
    )
    assert listed["items"] == []

    visible_archived = asyncio.run(
        app.list_agent_sessions(
            status=None,
            keyword=None,
            include_archived=True,
            limit=50,
            offset=0,
            current_user=current_user,
        )
    )
    assert [item["sessionId"] for item in visible_archived["items"]] == ["delete-session"]

    session_store.create_session(session_id="other-delete-session", user_id="user-2")
    with pytest.raises(app.HTTPException) as exc_info:
        asyncio.run(
            app.archive_agent_session(
                "other-delete-session",
                current_user=current_user,
            )
        )
    assert exc_info.value.status_code == 404


def test_session_delete_cancels_session_run_without_legacy_task(app_modules):
    app, tasks = app_modules
    session_store = app.SessionStore()
    current_user = SimpleNamespace(user_id="user-1")
    session_store.create_session(
        session_id="delete-session-run-only",
        user_id="user-1",
    )
    session_store.create_run(
        run_id="run-delete-only",
        session_id="delete-session-run-only",
        user_id="user-1",
        status=tasks.AgentTaskStatus.RUNNING,
        input_text="running work",
    )
    session_store.update_session(
        "delete-session-run-only",
        current_run_id="run-delete-only",
        status=app.AgentSessionStatus.RUNNING,
    )
    assert tasks.TaskStore().get_task("run-delete-only") is None

    class RunningWorker:
        cancelled = False

        def done(self):
            return False

        def cancel(self):
            self.cancelled = True

    worker = RunningWorker()
    app.runningAgentTasks["run-delete-only"] = worker

    payload = asyncio.run(app.delete_agent_session("delete-session-run-only", current_user=current_user))

    assert payload["sessionId"] == "delete-session-run-only"
    assert payload["deleted"] is True
    assert payload["archived"] is True
    assert payload["cancelledRunId"] == "run-delete-only"
    assert worker.cancelled is True
    assert "run-delete-only" not in app.runningAgentTasks

    archived = app.serialize_session(session_store.get_session("delete-session-run-only"))
    assert archived["status"] == app.AgentSessionStatus.ARCHIVED
    assert archived["currentRunId"] is None
    run = app.serialize_run(session_store.get_run("run-delete-only"))
    assert run["status"] == tasks.AgentTaskStatus.CANCELLED
    events = session_store.list_run_events("delete-session-run-only", run_id="run-delete-only")
    assert app.serialize_run_event(events[-1])["eventType"] == "run_cancelled"
    assert app.serialize_run_event(events[-1])["payload"]["reason"] == "session deleted"


def test_session_archive_endpoint_soft_deletes_idle_session(app_modules):
    app, _tasks = app_modules
    session_store = app.SessionStore()
    session_store.create_session(session_id="archive-session", user_id="user-1")

    payload = asyncio.run(
        app.archive_agent_session(
            "archive-session",
            current_user=SimpleNamespace(user_id="user-1"),
        )
    )

    assert payload["sessionId"] == "archive-session"
    assert payload["deleted"] is False
    assert payload["archived"] is True
    assert payload["cancelledRunId"] == ""
    assert app.serialize_session(session_store.get_session("archive-session"))["status"] == app.AgentSessionStatus.ARCHIVED


def test_waiting_session_message_resumes_existing_run(app_modules, monkeypatch):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    session_store = app.SessionStore()
    session_store.create_session(
        session_id="session-waiting",
        user_id="user-1",
        agent_id="task-pilot-agent",
    )
    session_store.update_session(
        "session-waiting",
        status=app.AgentSessionStatus.WAITING_INPUT,
        current_run_id="run-waiting",
    )
    task_store = tasks.TaskStore()
    task_store.create_task(
        task_id="run-waiting",
        trace_id="run-waiting",
        conversation_id="session-waiting",
        user_id="user-1",
        agent_id="task-pilot-agent",
        input_text="need account lookup",
        metadata={"language": "en"},
    )
    task_store.request_user_input("run-waiting", "Need account id", trace_id="run-waiting")
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
        app.add_agent_session_message(
            "session-waiting",
            app.AgentSessionMessageReq(content="account-123"),
            current_user=current_user,
        )
    )

    assert payload["runId"] == "run-waiting"
    assert payload["messageId"] == payload["message"]["messageId"]
    assert payload["event"]["eventType"] == "user_input"
    assert payload["message"]["content"] == "account-123"
    assert created_background
    resume_coro = created_background[0]
    assert resume_coro.cr_frame.f_locals["task_id"] == "run-waiting"
    assert resume_coro.cr_frame.f_locals["session_message_id"] == payload["message"]["messageId"]
    resume_coro.close()

    updated_session = app.serialize_session(session_store.get_session("session-waiting"))
    assert updated_session["status"] == app.AgentSessionStatus.RUNNING
    assert updated_session["currentRunId"] == "run-waiting"

    event_types = [event.event_type for event in task_store.list_events("run-waiting")]
    assert event_types == ["waiting_input", "user_input", "task_queued", "task_resume_requested"]


def test_waiting_session_message_resumes_session_run_without_legacy_task(app_modules, monkeypatch):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    session_store = app.SessionStore()
    session_store.create_session(
        session_id="session-waiting-run-only",
        user_id="user-1",
        agent_id="task-pilot-agent",
    )
    session_store.create_run(
        run_id="run-waiting-only",
        session_id="session-waiting-run-only",
        user_id="user-1",
        agent_id="task-pilot-agent",
        mode="react",
        status=tasks.AgentTaskStatus.WAITING_INPUT,
        input_text="need account lookup",
        metadata={
            "language": "en",
            "selectedTools": ["mcp_local:web_search"],
            "approvedTools": ["mcp_local:file_read"],
        },
    )
    session_store.update_session(
        "session-waiting-run-only",
        status=app.AgentSessionStatus.WAITING_INPUT,
        current_run_id="run-waiting-only",
    )
    session_store.add_run_event(
        session_id="session-waiting-run-only",
        run_id="run-waiting-only",
        event_type="waiting_input",
        source="runtime",
        payload={"prompt": "Need account id"},
    )
    assert tasks.TaskStore().get_task("run-waiting-only") is None
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
        app.add_agent_session_message(
            "session-waiting-run-only",
            app.AgentSessionMessageReq(content="account-123"),
            current_user=current_user,
        )
    )

    assert payload["runId"] == "run-waiting-only"
    assert payload["event"]["eventType"] == "user_input"
    assert payload["message"]["content"] == "account-123"
    assert created_background
    resume_coro = created_background[0]
    assert resume_coro.cr_code.co_name == "_resume_session_run_after_input"
    assert resume_coro.cr_frame.f_locals["run_record"].run_id == "run-waiting-only"
    assert resume_coro.cr_frame.f_locals["session_message_id"] == payload["message"]["messageId"]
    resume_coro.close()

    updated_session = app.serialize_session(session_store.get_session("session-waiting-run-only"))
    assert updated_session["status"] == app.AgentSessionStatus.RUNNING
    assert updated_session["currentRunId"] == "run-waiting-only"
    updated_run = app.serialize_run(session_store.get_run("run-waiting-only"))
    assert updated_run["status"] == tasks.AgentTaskStatus.QUEUED

    event_types = [
        app.serialize_run_event(event)["eventType"]
        for event in session_store.list_run_events("session-waiting-run-only", run_id="run-waiting-only")
    ]
    assert event_types == ["waiting_input", "user_input", "task_queued", "task_resume_requested"]


def test_session_events_api_aggregates_task_events(app_modules):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    session_store = app.SessionStore()
    session_store.create_session(session_id="session-events", user_id="user-1")
    task_store = tasks.TaskStore()
    task_store.create_task(
        task_id="run-a",
        trace_id="run-a",
        conversation_id="session-events",
        user_id="user-1",
    )
    task_store.add_event("run-a", "task_created", {"status": "queued"}, trace_id="run-a", source="api")
    task_store.add_event(
        "run-a",
        "user_message_created",
        {"messageId": "msg-user", "role": "user", "contentPreview": "hello"},
        trace_id="run-a",
        source="session",
        message_id="msg-user",
    )
    task_store.add_event("run-a", "tool_call", {"tool": "demo"}, trace_id="run-a", source="sse")
    task_store.add_event(
        "run-a",
        "assistant_message_started",
        {"messageId": "msg-assistant", "role": "assistant", "contentPreview": "", "status": "started"},
        trace_id="run-a",
        source="session",
        message_id="msg-assistant",
    )
    task_store.add_event(
        "run-a",
        "assistant_message_completed",
        {"messageId": "msg-assistant", "role": "assistant", "contentPreview": "done"},
        trace_id="run-a",
        source="session",
        message_id="msg-assistant",
    )

    payload = asyncio.run(
        app.list_agent_session_events(
            "session-events",
            event_type=None,
            source=None,
            limit=50,
            offset=0,
            current_user=current_user,
        )
    )

    assert payload["sessionId"] == "session-events"
    assert payload["count"] == 5
    assert payload["latestSeq"] == 5
    assert payload["nextSeq"] == 5
    assert payload["hasMore"] is False
    assert [item["seq"] for item in payload["items"]] == [1, 2, 3, 4, 5]
    assert all(item["eventId"] == f"evt_{item['id']}" for item in payload["items"])
    assert [item["eventType"] for item in payload["items"]] == [
        "task_created",
        "user_message_created",
        "tool_call",
        "assistant_message_started",
        "assistant_message_completed",
    ]
    assert {item["runId"] for item in payload["items"]} == {"run-a"}
    assert [item["type"] for item in payload["items"]] == [
        "run_created",
        "user_message_created",
        "tool_call_started",
        "assistant_message_started",
        "assistant_message_completed",
    ]
    assert payload["nextSeq"] == 5

    filtered = asyncio.run(
        app.list_agent_session_events(
            "session-events",
            event_type="tool_call",
            source=None,
            limit=50,
            offset=0,
            current_user=current_user,
        )
    )
    assert [item["seq"] for item in filtered["items"]] == [3]
    assert [item["eventType"] for item in filtered["items"]] == ["tool_call"]
    assert filtered["count"] == 1
    assert filtered["nextSeq"] == 3

    normalized_filtered = asyncio.run(
        app.list_agent_session_events(
            "session-events",
            event_type="tool_call_started",
            source=None,
            limit=50,
            offset=0,
            current_user=current_user,
        )
    )
    assert [item["seq"] for item in normalized_filtered["items"]] == [3]
    assert [item["eventType"] for item in normalized_filtered["items"]] == ["tool_call"]
    assert [item["type"] for item in normalized_filtered["items"]] == ["tool_call_started"]

    task_store.add_event(
        "run-a",
        "tool_result",
        {"tool": "demo", "type": "tool_call_failed", "failed": True, "error": "boom"},
        trace_id="run-a",
        source="sse",
    )
    failed_filtered = asyncio.run(
        app.list_agent_session_events(
            "session-events",
            event_type="tool_call_failed",
            source=None,
            limit=50,
            offset=0,
            current_user=current_user,
        )
    )
    assert [item["eventType"] for item in failed_filtered["items"]] == ["tool_result"]
    assert [item["type"] for item in failed_filtered["items"]] == ["tool_call_failed"]
    assert failed_filtered["items"][0]["payload"]["error"] == "boom"
    assert failed_filtered["count"] == 1

    resumed = asyncio.run(
        app.list_agent_session_events(
            "session-events",
            event_type=None,
            source=None,
            after_seq=1,
            limit=50,
            offset=0,
            current_user=current_user,
        )
    )
    assert resumed["afterSeq"] == 1
    assert resumed["count"] == 5
    assert resumed["latestSeq"] == 6
    assert resumed["hasMore"] is False
    assert [item["eventType"] for item in resumed["items"]] == [
        "user_message_created",
        "tool_call",
        "assistant_message_started",
        "assistant_message_completed",
        "tool_result",
    ]
    assert resumed["nextSeq"] == 6

    first_page = asyncio.run(
        app.list_agent_session_events(
            "session-events",
            event_type=None,
            source=None,
            after_seq=None,
            limit=2,
            offset=0,
            current_user=current_user,
        )
    )
    assert [item["seq"] for item in first_page["items"]] == [1, 2]
    assert first_page["count"] == 6
    assert first_page["latestSeq"] == 6
    assert first_page["nextSeq"] == 2
    assert first_page["hasMore"] is True


def test_next_session_event_seq_accounts_for_new_and_legacy_events(app_modules):
    app, tasks = app_modules
    session_store = app.SessionStore()
    task_store = tasks.TaskStore()
    task_store.create_task(
        task_id="run-next-seq-legacy",
        trace_id="run-next-seq-legacy",
        conversation_id="session-next-seq",
        user_id="user-1",
    )
    task_store.add_event(
        "run-next-seq-legacy",
        "task_created",
        {"status": "queued"},
        trace_id="run-next-seq-legacy",
        source="api",
    )
    task_store.add_event(
        "run-next-seq-legacy",
        "tool_call",
        {"tool": "demo"},
        trace_id="run-next-seq-legacy",
        source="sse",
    )
    session_store.create_session(session_id="session-next-seq", user_id="user-1")

    assert app._next_session_event_seq(session_store, task_store, "session-next-seq", "user-1") == 3

    session_store.add_run_event(
        session_id="session-next-seq",
        run_id="run-next-seq-new",
        event_type="run_started",
        source="runtime",
        seq=3,
        payload={"status": "running"},
    )

    assert app._next_session_event_seq(session_store, task_store, "session-next-seq", "user-1") == 6


def test_session_event_filter_accepts_normalized_artifact_type(app_modules):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    app.SessionStore().create_session(
        session_id="session-artifact-events",
        user_id="user-1",
        title="Artifact events",
    )
    task_store = tasks.TaskStore()
    task_store.create_task(
        task_id="run-artifact-events",
        trace_id="run-artifact-events",
        conversation_id="session-artifact-events",
        user_id="user-1",
        input_text="make file",
    )
    task_store.add_event(
        "run-artifact-events",
        "task_artifact_added",
        {"artifactId": "artifact-1", "filename": "report.md"},
        trace_id="run-artifact-events",
        source="artifact",
    )

    payload = asyncio.run(
        app.list_agent_session_events(
            "session-artifact-events",
            event_type="artifact_created",
            source=None,
            limit=50,
            offset=0,
            current_user=current_user,
        )
    )

    assert [item["eventType"] for item in payload["items"]] == ["task_artifact_added"]
    assert [item["type"] for item in payload["items"]] == ["artifact_created"]
    assert payload["items"][0]["sessionId"] == "session-artifact-events"
    assert payload["items"][0]["runId"] == "run-artifact-events"


def test_session_events_merge_new_run_events_with_legacy_task_events(app_modules):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    task_store = tasks.TaskStore()
    task_store.create_task(
        task_id="run-mixed-legacy",
        trace_id="run-mixed-legacy",
        conversation_id="session-mixed-events",
        user_id="user-1",
        input_text="legacy event",
    )
    legacy_event = task_store.add_event(
        "run-mixed-legacy",
        "task_created",
        {"status": "queued"},
        trace_id="run-mixed-legacy",
        source="api",
    )

    session_store = app.SessionStore()
    session_store.create_session(session_id="session-mixed-events", user_id="user-1")
    session_store.add_run_event(
        session_id="session-mixed-events",
        run_id="run-new",
        event_id="evt-new-run-started",
        event_type="run_started",
        source="runtime",
        payload={"status": "running"},
    )

    payload = asyncio.run(
        app.list_agent_session_events(
            "session-mixed-events",
            event_type=None,
            source=None,
            limit=50,
            offset=0,
            current_user=current_user,
        )
    )
    assert payload["count"] == 2
    assert payload["latestSeq"] == 2
    assert {item["eventId"] for item in payload["items"]} == {
        "evt-new-run-started",
        f"evt_{legacy_event.id}",
    }
    assert {item["runId"] for item in payload["items"]} == {"run-new", "run-mixed-legacy"}

    legacy_filtered = asyncio.run(
        app.list_agent_session_events(
            "session-mixed-events",
            event_type="run_created",
            source=None,
            limit=50,
            offset=0,
            current_user=current_user,
        )
    )
    assert [item["eventId"] for item in legacy_filtered["items"]] == [f"evt_{legacy_event.id}"]
    assert [item["eventType"] for item in legacy_filtered["items"]] == ["task_created"]
    assert [item["type"] for item in legacy_filtered["items"]] == ["run_created"]


def test_session_event_stream_replays_after_seq_and_finishes(app_modules):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    session_store = app.SessionStore()
    session_store.create_session(session_id="session-stream", user_id="user-1")
    task_store = tasks.TaskStore()
    task_store.create_task(
        task_id="run-stream",
        trace_id="run-stream",
        conversation_id="session-stream",
        user_id="user-1",
    )
    task_store.add_event("run-stream", "task_created", {"status": "queued"}, trace_id="run-stream", source="api")
    task_store.add_event("run-stream", "tool_call", {"tool": "demo"}, trace_id="run-stream", source="sse")

    async def collect_stream():
        response = await app.stream_agent_session_events(
            "session-stream",
            after_seq=1,
            limit=50,
            current_user=current_user,
        )
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
        return "".join(chunks)

    stream_text = asyncio.run(collect_stream())
    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in stream_text.splitlines()
        if line.startswith("data: ")
    ]

    assert payloads[0]["type"] == "session_event"
    assert payloads[0]["seq"] == 2
    assert payloads[0]["event"]["eventType"] == "tool_call"
    assert payloads[-1]["type"] == "done"
    assert payloads[-1]["status"] == app.AgentSessionStatus.IDLE
    assert payloads[-1]["afterSeq"] == 2


def test_session_websocket_replays_after_seq_and_reports_status(app_modules, monkeypatch):
    app, tasks = app_modules
    session_store = app.SessionStore()
    session_store.create_session(session_id="session-ws", user_id="user-1")
    task_store = tasks.TaskStore()
    task_store.create_task(
        task_id="run-ws",
        trace_id="run-ws",
        conversation_id="session-ws",
        user_id="user-1",
    )
    task_store.add_event("run-ws", "task_created", {"status": "queued"}, trace_id="run-ws", source="api")
    task_store.add_event("run-ws", "tool_call", {"tool": "demo"}, trace_id="run-ws", source="sse")

    async def fake_websocket_user(_websocket):
        return SimpleNamespace(user_id="user-1")

    class FakeWebSocket:
        query_params = {"afterSeq": "1", "limit": "50"}

        def __init__(self):
            self.accepted = False
            self.closed = False
            self.sent = []

        async def accept(self):
            self.accepted = True

        async def send_json(self, payload):
            self.sent.append(payload)
            if payload.get("type") == "session_status":
                raise app.WebSocketDisconnect()

        async def close(self, code=None):
            self.closed = True
            self.close_code = code

    websocket = FakeWebSocket()
    monkeypatch.setattr(app, "require_current_websocket_user", fake_websocket_user)

    asyncio.run(app.session_events_ws(websocket, "session-ws"))

    assert websocket.accepted is True
    assert websocket.closed is True
    assert websocket.sent[0]["type"] == "session_event"
    assert websocket.sent[0]["seq"] == 2
    assert websocket.sent[0]["event"]["eventType"] == "tool_call"
    assert websocket.sent[1] == {
        "type": "session_status",
        "sessionId": "session-ws",
        "status": app.AgentSessionStatus.IDLE,
        "afterSeq": 2,
    }


def test_session_run_api_lists_current_detail_and_artifacts(app_modules):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    session_store = app.SessionStore()
    session_store.create_session(session_id="session-runs", user_id="user-1")
    session_store.update_session(
        "session-runs",
        status=app.AgentSessionStatus.RUNNING,
        current_run_id="run-current",
    )
    task_store = tasks.TaskStore()
    current_task = task_store.create_task(
        task_id="run-current",
        trace_id="run-current",
        conversation_id="session-runs",
        user_id="user-1",
        input_text="current work",
    )
    session_store.create_run(
        run_id="run-current",
        session_id="session-runs",
        user_id="user-1",
        user_message_id="msg-current",
        trace_id="run-current",
        agent_id="agent-1",
        mode="react",
        status=tasks.AgentTaskStatus.RUNNING,
        input_text="current work",
    )
    task_store.update_status("run-current", tasks.AgentTaskStatus.RUNNING)
    task_store.add_event("run-current", "task_running", {"status": "running"}, trace_id="run-current", source="api")
    work_dir = Path(tasks.serialize_task(current_task)["workDir"])
    artifact_file = work_dir / "result.txt"
    artifact_file.write_text("result", encoding="utf-8")
    local_artifact = task_store.add_artifact("run-current", str(artifact_file), filename="result.txt")
    remote_artifact = task_store.add_remote_artifact(
        "run-current",
        "https://files.example.test/result.csv?token=raw-secret",
        filename="result.csv",
    )
    task_store.create_task(
        task_id="run-old",
        trace_id="run-old",
        conversation_id="session-runs",
        user_id="user-1",
        input_text="old work",
    )

    current = asyncio.run(app.get_agent_session_current_run("session-runs", current_user=current_user))
    assert current["run"]["runId"] == "run-current"
    assert current["run"]["sessionId"] == "session-runs"
    assert current["run"]["runRecord"]["runId"] == "run-current"
    assert current["run"]["runRecord"]["userMessageId"] == "msg-current"

    runs = asyncio.run(
        app.list_agent_session_runs(
            "session-runs",
            status=None,
            limit=50,
            offset=0,
            current_user=current_user,
        )
    )
    assert {item["runId"] for item in runs["items"]} == {"run-current", "run-old"}
    assert [item for item in runs["items"] if item["runId"] == "run-current"][0]["runRecord"]["status"] == tasks.AgentTaskStatus.RUNNING
    assert runs["count"] == 2
    assert runs["hasMore"] is False

    first_run_page = asyncio.run(
        app.list_agent_session_runs(
            "session-runs",
            status=None,
            limit=1,
            offset=0,
            current_user=current_user,
        )
    )
    assert len(first_run_page["items"]) == 1
    assert first_run_page["count"] == 2
    assert first_run_page["hasMore"] is True

    running_runs = asyncio.run(
        app.list_agent_session_runs(
            "session-runs",
            status=tasks.AgentTaskStatus.RUNNING,
            limit=50,
            offset=0,
            current_user=current_user,
        )
    )
    assert [item["runId"] for item in running_runs["items"]] == ["run-current"]
    assert running_runs["count"] == 1
    assert running_runs["hasMore"] is False

    detail = asyncio.run(
        app.get_agent_session_run(
            "session-runs",
            "run-current",
            events_limit=50,
            current_user=current_user,
        )
    )
    assert detail["runId"] == "run-current"
    assert detail["runRecord"]["input"] == "current work"
    assert detail["events"][0]["eventType"] == "task_running"
    assert detail["events"][0]["type"] == "run_started"
    assert {item["artifactId"] for item in detail["artifacts"]} == {
        local_artifact.artifact_id,
        remote_artifact.artifact_id,
    }

    session_artifacts = asyncio.run(app.list_agent_session_artifacts("session-runs", current_user=current_user))
    assert {item["artifactId"] for item in session_artifacts["items"]} == {
        local_artifact.artifact_id,
        remote_artifact.artifact_id,
    }
    assert {item["runId"] for item in session_artifacts["items"]} == {"run-current"}

    run_artifacts = asyncio.run(
        app.list_agent_session_run_artifacts(
            "session-runs",
            "run-current",
            current_user=current_user,
        )
    )
    assert {item["artifactId"] for item in run_artifacts["items"]} == {
        local_artifact.artifact_id,
        remote_artifact.artifact_id,
    }

    response = asyncio.run(
        app.download_agent_session_artifact(
            "session-runs",
            remote_artifact.artifact_id,
            current_user=current_user,
        )
    )
    assert response.status_code in {302, 307}
    assert response.headers["location"] == "https://files.example.test/result.csv?token=raw-secret"


def test_session_run_api_reads_session_run_without_legacy_task(app_modules):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    session_store = app.SessionStore()
    session_store.create_session(session_id="session-run-only", user_id="user-1")
    session_store.create_run(
        run_id="run-only",
        session_id="session-run-only",
        user_id="user-1",
        user_message_id="msg-run-only",
        assistant_message_id="msg-assistant-only",
        trace_id="trace-run-only",
        agent_id="agent-1",
        mode="react",
        output_style="markdown",
        status=tasks.AgentTaskStatus.RUNNING,
        input_text="run-only input",
        metadata={"usage": {"events": 1}},
    )
    session_store.update_session(
        "session-run-only",
        status=app.AgentSessionStatus.RUNNING,
        current_run_id="run-only",
    )
    session_store.add_run_event(
        session_id="session-run-only",
        run_id="run-only",
        event_type="run_started",
        source="runtime",
        payload={"status": "running"},
    )
    session_store.add_artifact(
        session_id="session-run-only",
        run_id="run-only",
        artifact_id="artifact-run-only",
        file_path="https://files.example.test/run-only.md?token=raw-secret",
        filename="run-only.md",
        file_size=42,
    )
    assert tasks.TaskStore().get_task("run-only") is None

    current = asyncio.run(app.get_agent_session_current_run("session-run-only", current_user=current_user))
    assert current["run"]["runId"] == "run-only"
    assert current["run"]["taskId"] == "run-only"
    assert current["run"]["runRecord"]["userMessageId"] == "msg-run-only"
    assert current["run"]["status"] == tasks.AgentTaskStatus.RUNNING
    assert current["run"]["usage"]["events"] == 1

    runs = asyncio.run(
        app.list_agent_session_runs(
            "session-run-only",
            status=None,
            limit=50,
            offset=0,
            current_user=current_user,
        )
    )
    assert runs["count"] == 1
    assert runs["items"][0]["runId"] == "run-only"

    running_runs = asyncio.run(
        app.list_agent_session_runs(
            "session-run-only",
            status=tasks.AgentTaskStatus.RUNNING,
            limit=50,
            offset=0,
            current_user=current_user,
        )
    )
    assert [item["runId"] for item in running_runs["items"]] == ["run-only"]

    detail = asyncio.run(
        app.get_agent_session_run(
            "session-run-only",
            "run-only",
            events_limit=50,
            current_user=current_user,
        )
    )
    assert detail["runId"] == "run-only"
    assert detail["events"][0]["eventType"] == "run_started"
    assert detail["events"][0]["type"] == "run_started"
    assert detail["artifacts"][0]["artifactId"] == "artifact-run-only"
    assert detail["artifacts"][0]["remoteUrl"] == "https://files.example.test/run-only.md?token=***"

    run_artifacts = asyncio.run(
        app.list_agent_session_run_artifacts(
            "session-run-only",
            "run-only",
            current_user=current_user,
        )
    )
    assert run_artifacts["items"][0]["artifactId"] == "artifact-run-only"
    assert run_artifacts["items"][0]["runId"] == "run-only"

    response = asyncio.run(
        app.download_agent_session_artifact(
            "session-run-only",
            "artifact-run-only",
            current_user=current_user,
        )
    )
    assert response.status_code in {302, 307}
    assert response.headers["location"] == "https://files.example.test/run-only.md?token=raw-secret"

    session_detail = asyncio.run(
        app.get_agent_session(
            "session-run-only",
            messages_limit=100,
            runs_limit=50,
            current_user=current_user,
        )
    )
    assert session_detail["currentRun"]["runId"] == "run-only"
    assert session_detail["runCount"] == 1
    assert session_detail["artifactSummary"]["count"] == 1


def test_session_detail_and_current_run_include_pending_approval(app_modules):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    session_store = app.SessionStore()
    session_store.create_session(session_id="session-pending-approval", user_id="user-1")
    session_store.update_session(
        "session-pending-approval",
        status=app.AgentSessionStatus.WAITING_APPROVAL,
        current_run_id="run-pending-approval",
    )
    task_store = tasks.TaskStore()
    task_store.create_task(
        task_id="run-pending-approval",
        trace_id="run-pending-approval",
        conversation_id="session-pending-approval",
        user_id="user-1",
        input_text="run code",
    )
    task_store.update_status("run-pending-approval", tasks.AgentTaskStatus.WAITING_APPROVAL)
    approval_event = task_store.add_event(
        "run-pending-approval",
        "approval_requested",
        {
            "approvalType": "high_risk_tools",
            "requests": [{"tool": "mcp_local:code_interpreter"}],
        },
        trace_id="run-pending-approval",
        source="policy",
    )

    detail = asyncio.run(
        app.get_agent_session(
            "session-pending-approval",
            messages_limit=100,
            runs_limit=50,
            current_user=current_user,
        )
    )
    current = asyncio.run(app.get_agent_session_current_run("session-pending-approval", current_user=current_user))
    run_detail = asyncio.run(
        app.get_agent_session_run(
            "session-pending-approval",
            "run-pending-approval",
            events_limit=500,
            current_user=current_user,
        )
    )

    assert detail["pendingApproval"]["id"] == approval_event.id
    assert detail["pendingApproval"]["pending"] is True
    assert detail["currentRun"]["pendingApproval"]["id"] == approval_event.id
    assert current["run"]["pendingApproval"]["id"] == approval_event.id
    assert current["run"]["pendingApproval"]["payload"]["requests"][0]["tool"] == "mcp_local:code_interpreter"
    assert run_detail["pendingApproval"]["id"] == approval_event.id

    resolved_event = task_store.add_event(
        "run-pending-approval",
        "approval_resolved",
        {"approvalRequestEventId": approval_event.id, "approved": False},
        trace_id="run-pending-approval",
        source="user",
    )
    assert resolved_event.id > approval_event.id

    resolved_current = asyncio.run(
        app.get_agent_session_current_run("session-pending-approval", current_user=current_user)
    )
    assert "pendingApproval" not in resolved_current["run"]


def test_session_run_only_detail_and_current_run_include_pending_approval(app_modules):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    session_store = app.SessionStore()
    session_store.create_session(session_id="session-pending-approval-run-only", user_id="user-1")
    session_store.create_run(
        run_id="run-pending-approval-only",
        session_id="session-pending-approval-run-only",
        user_id="user-1",
        status=tasks.AgentTaskStatus.WAITING_APPROVAL,
        input_text="run code",
    )
    session_store.update_session(
        "session-pending-approval-run-only",
        status=app.AgentSessionStatus.WAITING_APPROVAL,
        current_run_id="run-pending-approval-only",
    )
    approval_event = session_store.add_run_event(
        session_id="session-pending-approval-run-only",
        run_id="run-pending-approval-only",
        event_type="approval_requested",
        source="policy",
        payload={
            "approvalType": "high_risk_tools",
            "requests": [{"tool": "mcp_local:code_interpreter"}],
        },
    )
    assert tasks.TaskStore().get_task("run-pending-approval-only") is None

    detail = asyncio.run(
        app.get_agent_session(
            "session-pending-approval-run-only",
            messages_limit=100,
            runs_limit=50,
            current_user=current_user,
        )
    )
    current = asyncio.run(
        app.get_agent_session_current_run(
            "session-pending-approval-run-only",
            current_user=current_user,
        )
    )
    run_detail = asyncio.run(
        app.get_agent_session_run(
            "session-pending-approval-run-only",
            "run-pending-approval-only",
            events_limit=500,
            current_user=current_user,
        )
    )

    assert detail["pendingApproval"]["id"] == approval_event.id
    assert detail["pendingApproval"]["pending"] is True
    assert detail["currentRun"]["pendingApproval"]["id"] == approval_event.id
    assert current["run"]["pendingApproval"]["id"] == approval_event.id
    assert current["run"]["pendingApproval"]["payload"]["requests"][0]["tool"] == "mcp_local:code_interpreter"
    assert run_detail["pendingApproval"]["id"] == approval_event.id

    resolved_event = session_store.add_run_event(
        session_id="session-pending-approval-run-only",
        run_id="run-pending-approval-only",
        event_type="approval_resolved",
        source="user",
        payload={
            "approvalRequestEventId": approval_event.id,
            "approvalRequestEventEventId": approval_event.event_id,
            "approved": False,
        },
    )
    assert resolved_event.id > approval_event.id

    resolved_current = asyncio.run(
        app.get_agent_session_current_run(
            "session-pending-approval-run-only",
            current_user=current_user,
        )
    )
    assert "pendingApproval" not in resolved_current["run"]


def test_session_run_plan_api_restores_latest_plan(app_modules):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    session_store = app.SessionStore()
    session_store.create_session(session_id="session-plan", user_id="user-1")
    task_store = tasks.TaskStore()
    task_store.create_task(
        task_id="run-plan",
        trace_id="run-plan",
        conversation_id="session-plan",
        user_id="user-1",
        input_text="complex research",
    )
    task_store.add_event(
        "run-plan",
        "plan_created",
        {
            "messageType": "plan_created",
            "plan": {
                "title": "Research Plan",
                "steps": ["Search", "Summarize"],
                "step_status": ["not_started", "not_started"],
                "notes": ["", ""],
                "command": "create",
            },
        },
        trace_id="run-plan",
        source="sse",
    )
    task_store.add_event(
        "run-plan",
        "plan_step_completed",
        {
            "messageType": "plan_step_completed",
            "plan": {
                "title": "Research Plan",
                "steps": ["Search", "Summarize"],
                "step_status": ["completed", "not_started"],
                "notes": ["found sources", ""],
                "evidence": [[{"summary": "source page", "url": "https://example.test/source"}], []],
                "command": "mark_step",
                "stepIndex": 1,
            },
        },
        trace_id="run-plan",
        source="sse",
    )

    payload = asyncio.run(
        app.get_agent_session_run_plan(
            "session-plan",
            "run-plan",
            current_user=current_user,
        )
    )

    assert payload["sessionId"] == "session-plan"
    assert payload["runId"] == "run-plan"
    assert payload["plan"]["step_status"] == ["completed", "not_started"]
    assert payload["plan"]["notes"][0] == "found sources"
    assert payload["plan"]["evidence"][0][0]["url"] == "https://example.test/source"
    assert [event["eventType"] for event in payload["events"]] == [
        "plan_created",
        "plan_step_completed",
    ]
    assert [event["seq"] for event in payload["events"]] == [1, 2]


def test_session_run_plan_api_reads_session_run_events_without_legacy_task(app_modules):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    session_store = app.SessionStore()
    session_store.create_session(session_id="session-plan-run-only", user_id="user-1")
    session_store.create_run(
        run_id="run-plan-only",
        session_id="session-plan-run-only",
        user_id="user-1",
        status=tasks.AgentTaskStatus.RUNNING,
        input_text="complex run-only plan",
    )
    session_store.add_run_event(
        session_id="session-plan-run-only",
        run_id="run-plan-only",
        event_type="plan_created",
        source="runtime",
        payload={
            "plan": {
                "title": "Run Only Plan",
                "steps": ["Read", "Answer"],
                "step_status": ["not_started", "not_started"],
            }
        },
    )
    session_store.add_run_event(
        session_id="session-plan-run-only",
        run_id="run-plan-only",
        event_type="plan_step_completed",
        source="runtime",
        payload={
            "plan": {
                "title": "Run Only Plan",
                "steps": ["Read", "Answer"],
                "step_status": ["completed", "not_started"],
                "notes": ["read complete", ""],
            }
        },
    )
    assert tasks.TaskStore().get_task("run-plan-only") is None

    payload = asyncio.run(
        app.get_agent_session_run_plan(
            "session-plan-run-only",
            "run-plan-only",
            current_user=current_user,
        )
    )

    assert payload["sessionId"] == "session-plan-run-only"
    assert payload["runId"] == "run-plan-only"
    assert payload["plan"]["step_status"] == ["completed", "not_started"]
    assert payload["plan"]["notes"][0] == "read complete"
    assert [event["eventType"] for event in payload["events"]] == [
        "plan_created",
        "plan_step_completed",
    ]
    assert [event["seq"] for event in payload["events"]] == [1, 2]


def test_session_run_cancel_and_retry_update_session(app_modules, monkeypatch):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    session_store = app.SessionStore()
    session_store.create_session(session_id="session-control", user_id="user-1", agent_id="agent-1")
    session_store.update_session(
        "session-control",
        status=app.AgentSessionStatus.RUNNING,
        current_run_id="run-cancel",
    )
    task_store = tasks.TaskStore()
    task_store.create_task(
        task_id="run-cancel",
        trace_id="run-cancel",
        conversation_id="session-control",
        user_id="user-1",
        agent_id="agent-1",
        mode="react",
        output_style="markdown",
        input_text="cancel this",
    )
    task_store.update_status("run-cancel", tasks.AgentTaskStatus.RUNNING)
    task_store.add_event(
        "run-cancel",
        "plan_created",
        {
            "messageType": "plan_created",
            "plan": {
                "title": "Cancel Plan",
                "steps": ["Run long step", "Write result"],
                "step_status": ["running", "not_started"],
                "notes": ["", ""],
                "command": "create",
            },
        },
        trace_id="run-cancel",
        source="sse",
    )

    cancel_payload = asyncio.run(
        app.cancel_agent_session_run(
            "session-control",
            "run-cancel",
            current_user=current_user,
        )
    )
    assert cancel_payload["status"] == tasks.AgentTaskStatus.CANCELLED
    assert cancel_payload["sessionId"] == "session-control"
    assert cancel_payload["runId"] == "run-cancel"
    cancelled_session = app.serialize_session(session_store.get_session("session-control"))
    assert cancelled_session["status"] == app.AgentSessionStatus.IDLE
    assert cancelled_session["currentRunId"] is None
    plan_payload = asyncio.run(
        app.get_agent_session_run_plan(
            "session-control",
            "run-cancel",
            current_user=current_user,
        )
    )
    assert [event["eventType"] for event in plan_payload["events"]] == [
        "plan_created",
        "plan_cancelled",
    ]
    assert plan_payload["plan"]["planStatus"] == "cancelled"
    assert plan_payload["plan"]["step_status"] == ["cancelled", "not_started"]

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
    retry_payload = asyncio.run(
        app.retry_agent_session_run(
            "session-control",
            "run-cancel",
            current_user=current_user,
        )
    )

    assert retry_payload["sessionId"] == "session-control"
    assert retry_payload["runId"] == retry_payload["taskId"]
    assert retry_payload["metadata"]["source"] == "retry"
    assert created_background
    created_background[0].close()
    retry_session = app.serialize_session(session_store.get_session("session-control"))
    assert retry_session["status"] == app.AgentSessionStatus.RUNNING
    assert retry_session["currentRunId"] == retry_payload["runId"]


def test_session_run_cancel_updates_session_run_without_legacy_task(app_modules):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    session_store = app.SessionStore()
    session_store.create_session(session_id="session-cancel-run-only", user_id="user-1")
    session_store.create_run(
        run_id="run-cancel-only",
        session_id="session-cancel-run-only",
        user_id="user-1",
        status=tasks.AgentTaskStatus.RUNNING,
        input_text="cancel this run",
    )
    session_store.update_session(
        "session-cancel-run-only",
        status=app.AgentSessionStatus.RUNNING,
        current_run_id="run-cancel-only",
    )
    assert tasks.TaskStore().get_task("run-cancel-only") is None

    payload = asyncio.run(
        app.cancel_agent_session_run(
            "session-cancel-run-only",
            "run-cancel-only",
            current_user=current_user,
        )
    )

    assert payload["runId"] == "run-cancel-only"
    assert payload["status"] == tasks.AgentTaskStatus.CANCELLED
    assert payload["errorMessage"] == "task cancelled"
    assert payload["event"]["eventType"] == "run_cancelled"
    updated_session = app.serialize_session(session_store.get_session("session-cancel-run-only"))
    assert updated_session["status"] == app.AgentSessionStatus.IDLE
    assert updated_session["currentRunId"] is None

    detail = asyncio.run(
        app.get_agent_session_run(
            "session-cancel-run-only",
            "run-cancel-only",
            events_limit=50,
            current_user=current_user,
        )
    )
    assert detail["status"] == tasks.AgentTaskStatus.CANCELLED
    assert [event["eventType"] for event in detail["events"]] == ["run_cancelled"]


def test_session_run_retry_starts_from_session_run_without_legacy_task(app_modules, monkeypatch):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    session_store = app.SessionStore()
    session_store.create_session(session_id="session-retry-run-only", user_id="user-1", agent_id="agent-1")
    session_store.create_run(
        run_id="run-retry-only",
        session_id="session-retry-run-only",
        user_id="user-1",
        trace_id="run-retry-only",
        agent_id="agent-1",
        mode="react",
        output_style="markdown",
        status=tasks.AgentTaskStatus.FAILED,
        input_text="retry this run",
        metadata={
            "selectedTools": ["mcp_local:web_search"],
            "approvedTools": ["mcp_local:file_read"],
            "runEnvironment": "sandbox",
            "language": "en",
        },
    )
    assert tasks.TaskStore().get_task("run-retry-only") is None

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
        app.retry_agent_session_run(
            "session-retry-run-only",
            "run-retry-only",
            current_user=current_user,
        )
    )

    assert payload["sessionId"] == "session-retry-run-only"
    assert payload["runId"]
    assert payload["runId"] != "run-retry-only"
    assert payload["metadata"]["source"] == "retry"
    assert payload["metadata"]["parentRunId"] == "run-retry-only"
    assert payload["message"]["metadata"]["source"] == "retry"
    assert payload["retryRequested"]["eventType"] == "run_retry_requested"
    updated_session = app.serialize_session(session_store.get_session("session-retry-run-only"))
    assert updated_session["status"] == app.AgentSessionStatus.RUNNING
    assert updated_session["currentRunId"] == payload["runId"]
    assert created_background
    retry_req = created_background[0].cr_frame.f_locals["req"]
    assert retry_req.trace_id == payload["runId"]
    assert retry_req.conversation_id == "session-retry-run-only"
    assert retry_req.session_message_id == payload["message"]["messageId"]
    assert retry_req.selected_tools == ["mcp_local:web_search"]
    assert retry_req.approved_tools == ["mcp_local:file_read"]
    created_background[0].close()


def test_session_run_approval_denial_updates_session_run_without_legacy_task(app_modules, monkeypatch):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    session_store = app.SessionStore()
    session_store.create_session(session_id="session-approval-run-only-deny", user_id="user-1")
    session_store.create_run(
        run_id="run-approval-only-deny",
        session_id="session-approval-run-only-deny",
        user_id="user-1",
        status=tasks.AgentTaskStatus.WAITING_APPROVAL,
        input_text="run shell",
    )
    session_store.update_session(
        "session-approval-run-only-deny",
        status=app.AgentSessionStatus.WAITING_APPROVAL,
        current_run_id="run-approval-only-deny",
    )
    approval_event = session_store.add_run_event(
        session_id="session-approval-run-only-deny",
        run_id="run-approval-only-deny",
        event_type="approval_requested",
        source="policy",
        payload={
            "approvalType": "high_risk_tools",
            "requests": [{"tool": "mcp_local:shell_exec", "reason": "high_risk_requires_approval"}],
        },
    )
    assert tasks.TaskStore().get_task("run-approval-only-deny") is None

    def fail_create_task(_coro):
        raise AssertionError("denied approval must not start a rerun")

    monkeypatch.setattr(app.asyncio, "create_task", fail_create_task)

    payload = asyncio.run(
        app.resolve_agent_session_run_approval(
            "session-approval-run-only-deny",
            "run-approval-only-deny",
            app.AgentRunApprovalReq(approved=False, reason="blocked"),
            current_user=current_user,
        )
    )

    assert payload["sessionId"] == "session-approval-run-only-deny"
    assert payload["runId"] == "run-approval-only-deny"
    assert payload["approved"] is False
    assert payload["event"]["eventType"] == "approval_resolved"
    assert payload["event"]["payload"]["approvalRequestEventId"] == approval_event.id
    updated_run = app.serialize_run(session_store.get_run("run-approval-only-deny"))
    assert updated_run["status"] == tasks.AgentTaskStatus.CANCELLED
    assert updated_run["errorMessage"] == "approval rejected"
    updated_session = app.serialize_session(session_store.get_session("session-approval-run-only-deny"))
    assert updated_session["status"] == app.AgentSessionStatus.IDLE
    assert updated_session["currentRunId"] is None
    event_types = [
        app.serialize_run_event(event)["eventType"]
        for event in session_store.list_run_events("session-approval-run-only-deny", run_id="run-approval-only-deny")
    ]
    assert event_types == ["approval_requested", "approval_resolved", "run_cancelled"]


def test_session_run_approval_approves_and_reruns_without_legacy_task(app_modules, monkeypatch):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    session_store = app.SessionStore()
    session_store.create_session(session_id="session-approval-run-only", user_id="user-1", agent_id="agent-1")
    session_store.create_run(
        run_id="run-approval-only",
        session_id="session-approval-run-only",
        user_id="user-1",
        trace_id="run-approval-only",
        agent_id="agent-1",
        mode="react",
        output_style="markdown",
        status=tasks.AgentTaskStatus.WAITING_APPROVAL,
        input_text="run code",
        metadata={
            "selectedTools": ["mcp_local:code_interpreter"],
            "approvedTools": ["mcp_local:file_read"],
            "runEnvironment": "sandbox",
            "language": "en",
        },
    )
    session_store.update_session(
        "session-approval-run-only",
        status=app.AgentSessionStatus.WAITING_APPROVAL,
        current_run_id="run-approval-only",
    )
    session_store.add_run_event(
        session_id="session-approval-run-only",
        run_id="run-approval-only",
        event_type="approval_requested",
        source="policy",
        payload={
            "approvalType": "high_risk_tools",
            "requests": [{"tool": "mcp_local:code_interpreter", "reason": "high_risk_requires_approval"}],
        },
    )
    assert tasks.TaskStore().get_task("run-approval-only") is None

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
        app.resolve_agent_session_run_approval(
            "session-approval-run-only",
            "run-approval-only",
            app.AgentRunApprovalReq(approved=True),
            current_user=current_user,
        )
    )

    assert payload["sessionId"] == "session-approval-run-only"
    assert payload["runId"]
    assert payload["metadata"]["source"] == "approval_retry"
    assert payload["metadata"]["parentRunId"] == "run-approval-only"
    assert payload["metadata"]["approvedTools"] == [
        "mcp_local:file_read",
        "mcp_local:code_interpreter",
    ]
    assert payload["approvalResolved"]["payload"]["approved"] is True
    assert payload["approvalResolved"]["payload"]["retryRunId"] == payload["runId"]
    assert payload["message"]["metadata"]["source"] == "approval_retry"
    original_run = app.serialize_run(session_store.get_run("run-approval-only"))
    assert original_run["status"] == tasks.AgentTaskStatus.COMPLETED
    updated_session = app.serialize_session(session_store.get_session("session-approval-run-only"))
    assert updated_session["status"] == app.AgentSessionStatus.RUNNING
    assert updated_session["currentRunId"] == payload["runId"]
    assert created_background
    retry_req = created_background[0].cr_frame.f_locals["req"]
    assert retry_req.trace_id == payload["runId"]
    assert retry_req.conversation_id == "session-approval-run-only"
    assert retry_req.session_message_id == payload["message"]["messageId"]
    assert retry_req.approved_tools == [
        "mcp_local:file_read",
        "mcp_local:code_interpreter",
    ]
    created_background[0].close()


def test_session_run_approval_approves_tools_and_reruns(app_modules, monkeypatch):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    session_store = app.SessionStore()
    session_store.create_session(
        session_id="session-approval",
        user_id="user-1",
        agent_id="approval-agent",
    )
    session_store.update_session(
        "session-approval",
        status=app.AgentSessionStatus.WAITING_APPROVAL,
        current_run_id="run-approval",
    )
    task_store = tasks.TaskStore()
    task_store.create_task(
        task_id="run-approval",
        trace_id="run-approval",
        conversation_id="session-approval",
        user_id="user-1",
        agent_id="approval-agent",
        mode="react",
        output_style="markdown",
        input_text="run code",
        metadata={
            "selectedTools": ["mcp_local:code_interpreter"],
            "approvedTools": ["mcp_local:file_read"],
            "runEnvironment": "sandbox",
            "language": "en",
        },
    )
    task_store.update_status("run-approval", tasks.AgentTaskStatus.WAITING_APPROVAL)
    task_store.add_event(
        "run-approval",
        "approval_requested",
        {
            "approvalType": "high_risk_tools",
            "requests": [
                {
                    "tool": "mcp_local:code_interpreter",
                    "reason": "high_risk_requires_approval",
                    "riskLevel": "high",
                }
            ],
            "selectedTools": ["mcp_local:code_interpreter"],
            "approvedTools": ["mcp_local:file_read"],
        },
        trace_id="run-approval",
        source="policy",
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

    payload = asyncio.run(
        app.resolve_agent_session_run_approval(
            "session-approval",
            "run-approval",
            app.AgentRunApprovalReq(approved=True),
            current_user=current_user,
        )
    )

    assert payload["sessionId"] == "session-approval"
    assert payload["runId"] == payload["taskId"]
    assert payload["metadata"]["source"] == "approval_retry"
    assert payload["metadata"]["parentTaskId"] == "run-approval"
    assert payload["metadata"]["approvedTools"] == [
        "mcp_local:file_read",
        "mcp_local:code_interpreter",
    ]
    assert payload["approvalResolved"]["payload"]["approved"] is True
    assert payload["approvalResolved"]["payload"]["retryRunId"] == payload["runId"]
    assert payload["message"]["metadata"]["source"] == "approval_retry"
    assert payload["message"]["runId"] == payload["runId"]

    updated_session = app.serialize_session(session_store.get_session("session-approval"))
    assert updated_session["status"] == app.AgentSessionStatus.RUNNING
    assert updated_session["currentRunId"] == payload["runId"]
    assert updated_session["lastMessageId"] == payload["message"]["messageId"]

    original_run = task_store.get_task("run-approval")
    assert original_run.status == tasks.AgentTaskStatus.COMPLETED
    events = task_store.list_events("run-approval")
    assert events[-1].event_type == "task_completed"
    assert tasks.serialize_event(events[-1])["payload"]["retryRunId"] == payload["runId"]
    resolved_event = next(event for event in events if event.event_type == "approval_resolved")
    resolved_payload = tasks.serialize_event(resolved_event)["payload"]
    assert resolved_payload["approvedTools"] == [
        "mcp_local:file_read",
        "mcp_local:code_interpreter",
    ]
    assert created_background
    retry_req = created_background[0].cr_frame.f_locals["req"]
    assert retry_req.trace_id == payload["runId"]
    assert retry_req.conversation_id == "session-approval"
    assert retry_req.session_message_id == payload["message"]["messageId"]
    assert retry_req.approved_tools == [
        "mcp_local:file_read",
        "mcp_local:code_interpreter",
    ]
    created_background[0].close()


def test_session_run_approval_denial_records_resolution_without_rerun(app_modules, monkeypatch):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    session_store = app.SessionStore()
    session_store.create_session(session_id="session-approval-deny", user_id="user-1")
    task_store = tasks.TaskStore()
    task_store.create_task(
        task_id="run-approval-deny",
        trace_id="run-approval-deny",
        conversation_id="session-approval-deny",
        user_id="user-1",
        input_text="run shell",
    )
    task_store.add_event(
        "run-approval-deny",
        "approval_requested",
        {
            "approvalType": "high_risk_tools",
            "requests": [{"tool": "mcp_local:shell_exec", "reason": "high_risk_requires_approval"}],
        },
        trace_id="run-approval-deny",
        source="policy",
    )

    def fail_create_task(_coro):
        raise AssertionError("denied approval must not start a rerun")

    monkeypatch.setattr(app.asyncio, "create_task", fail_create_task)

    payload = asyncio.run(
        app.resolve_agent_session_run_approval(
            "session-approval-deny",
            "run-approval-deny",
            app.AgentRunApprovalReq(approved=False, reason="not needed"),
            current_user=current_user,
        )
    )

    assert payload["sessionId"] == "session-approval-deny"
    assert payload["runId"] == "run-approval-deny"
    assert payload["approved"] is False
    assert payload["rerun"] is False
    assert payload["event"]["eventType"] == "approval_resolved"
    assert payload["event"]["payload"]["approved"] is False
    assert payload["event"]["payload"]["requestedTools"] == ["mcp_local:shell_exec"]
    assert payload["event"]["payload"]["reason"] == "not needed"
    unchanged_session = app.serialize_session(session_store.get_session("session-approval-deny"))
    assert unchanged_session["status"] == app.AgentSessionStatus.IDLE
    assert unchanged_session["currentRunId"] is None


def test_session_run_approval_denial_clears_waiting_approval(app_modules, monkeypatch):
    app, tasks = app_modules
    current_user = SimpleNamespace(user_id="user-1")
    session_store = app.SessionStore()
    session_store.create_session(session_id="session-approval-wait-deny", user_id="user-1")
    session_store.update_session(
        "session-approval-wait-deny",
        status=app.AgentSessionStatus.WAITING_APPROVAL,
        current_run_id="run-approval-wait-deny",
    )
    task_store = tasks.TaskStore()
    task_store.create_task(
        task_id="run-approval-wait-deny",
        trace_id="run-approval-wait-deny",
        conversation_id="session-approval-wait-deny",
        user_id="user-1",
        input_text="run shell",
    )
    task_store.update_status("run-approval-wait-deny", tasks.AgentTaskStatus.WAITING_APPROVAL)
    task_store.add_event(
        "run-approval-wait-deny",
        "approval_requested",
        {
            "approvalType": "high_risk_tools",
            "requests": [{"tool": "mcp_local:shell_exec", "reason": "high_risk_requires_approval"}],
        },
        trace_id="run-approval-wait-deny",
        source="policy",
    )

    def fail_create_task(_coro):
        raise AssertionError("denied approval must not start a rerun")

    monkeypatch.setattr(app.asyncio, "create_task", fail_create_task)

    payload = asyncio.run(
        app.resolve_agent_session_run_approval(
            "session-approval-wait-deny",
            "run-approval-wait-deny",
            app.AgentRunApprovalReq(approved=False, reason="blocked"),
            current_user=current_user,
        )
    )

    assert payload["approved"] is False
    assert task_store.get_task("run-approval-wait-deny").status == tasks.AgentTaskStatus.CANCELLED
    events = task_store.list_events("run-approval-wait-deny")
    assert events[-1].event_type == "task_cancelled"
    assert tasks.serialize_event(events[-1])["payload"]["reason"] == "approval_rejected"
    updated_session = app.serialize_session(session_store.get_session("session-approval-wait-deny"))
    assert updated_session["status"] == app.AgentSessionStatus.IDLE
    assert updated_session["currentRunId"] is None
    assert updated_session["lastMessagePreview"] == "blocked"


def test_agent_diagnostics_api_reports_config_errors(app_modules, tmp_path, monkeypatch):
    app, _tasks = app_modules
    agents_root = tmp_path / "agents"
    good_dir = agents_root / "good_agent"
    bad_dir = agents_root / "bad_agent"
    good_dir.mkdir(parents=True)
    bad_dir.mkdir()
    (good_dir / "agent.yaml").write_text("id: good_agent\nname: Good Agent\n", encoding="utf-8")
    (bad_dir / "agent.yaml").write_text(
        "id: bad_agent\nname: Bad Agent\ntype: python.module.Agent\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(app.agentRegistry, "root_dir", agents_root)

    payload = asyncio.run(app.get_agent_diagnostics())

    assert payload["status"] == "invalid"
    assert payload["validCount"] == 1
    assert payload["invalidCount"] == 1
    bad_agent = next(item for item in payload["items"] if item["agentId"] == "bad_agent")
    assert bad_agent["status"] == "invalid"
    assert "Unsupported agent type" in bad_agent["error"]


def test_agent_api_returns_diagnostics_when_config_is_invalid(app_modules, tmp_path, monkeypatch):
    app, _tasks = app_modules
    agents_root = tmp_path / "agents"
    router_dir = agents_root / "router_agent"
    router_dir.mkdir(parents=True)
    (router_dir / "agent.yaml").write_text(
        "id: router_agent\nname: Router Agent\ntype: supervisor\nhandoffs:\n  allowed: [missing_agent]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(app.agentRegistry, "root_dir", agents_root)

    with pytest.raises(app.HTTPException) as exc_info:
        asyncio.run(app.list_agents())

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "agent_config_invalid"
    assert "handoff target not found" in exc_info.value.detail["message"]
    assert exc_info.value.detail["diagnostics"]["status"] == "invalid"
    assert exc_info.value.detail["diagnostics"]["invalidCount"] == 1


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


def test_list_task_events_api_filters_by_event_type_and_source(app_modules):
    app, tasks = app_modules
    store = tasks.TaskStore()
    store.create_task(task_id="api-event-filter", trace_id="trace-api-event-filter")
    store.add_event("api-event-filter", "tool_call", {"tool": "deepsearch"}, trace_id="trace-api-event-filter", source="sse")
    store.add_event("api-event-filter", "tool_result", {"tool": "deepsearch"}, trace_id="trace-api-event-filter", source="sse")
    store.add_event("api-event-filter", "agent_failed", {"error": "boom"}, trace_id="trace-api-event-filter", source="agent")

    payload = asyncio.run(
        app.list_agent_task_events(
            "api-event-filter",
            event_type="tool_call,tool_result",
            source="sse",
            limit=50,
            offset=0,
        )
    )

    assert payload["eventType"] == "tool_call,tool_result"
    assert payload["source"] == "sse"
    assert [item["eventType"] for item in payload["items"]] == ["tool_call", "tool_result"]


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
            AgentToolSpec(name="builtin:set_todo_list"),
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
    assert "builtin:set_todo_list" in names
    assert "mcp_local:deepsearch" in names
    todo_tool = payload["items"][names.index("builtin:set_todo_list")]
    assert todo_tool["source"] == "builtin"
    assert todo_tool["riskLevel"] == "low"
    assert todo_tool["requiresApproval"] is False
    deepsearch_tool = payload["items"][names.index("mcp_local:deepsearch")]
    assert deepsearch_tool["id"] == "mcp_local:deepsearch"
    assert deepsearch_tool["displayName"] == "deepsearch"
    assert deepsearch_tool["source"] == "mcp"
    assert deepsearch_tool["riskLevel"] == "low"
    assert deepsearch_tool["requiresApproval"] is False
    assert deepsearch_tool["available"] is True
    assert deepsearch_tool["availability"] == "available"
    assert deepsearch_tool["toolPrefix"] == "mcp_local"
    assert deepsearch_tool["mcpServerId"] == "mcp_local"
    assert deepsearch_tool["inputSchema"]["properties"]["query"]["type"] == "string"

    assert payload["unavailable"] == payload["blockedTools"]
    blocked = payload["blockedTools"][0]
    assert blocked["name"] == "mcp_local:code_interpreter"
    assert blocked["id"] == "mcp_local:code_interpreter"
    assert blocked["description"] == "Configured code runner"
    assert blocked["allowed"] is False
    assert blocked["available"] is False
    assert blocked["availability"] == "unavailable"
    assert blocked["blockReason"] == "high_risk_requires_enable"
    assert blocked["unavailableReason"] == "high_risk_requires_enable"
    assert blocked["source"] == "mcp"
    assert blocked["riskLevel"] == "high"
    assert blocked["requiresApproval"] is True
    assert blocked["inputSchema"] == {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}
    assert blocked["outputSchema"] == {"type": "object", "properties": {"result": {"type": "string"}}}
    assert blocked["policy"] == {"risk": "high"}


def test_session_tools_api_uses_session_owner_and_tool_policy(app_modules, monkeypatch):
    app, _tasks = app_modules
    from brain.core.agent_registry import AgentToolSpec

    session_store = app.SessionStore()
    session_store.create_session(
        session_id="session-tools",
        user_id="user-1",
        agent_id="tool-agent",
    )
    agent = app.AgentConfig(
        id="tool-agent",
        name="Tool Agent",
        tools=[
            AgentToolSpec(name="builtin:plan_tool"),
            AgentToolSpec(name="builtin:set_todo_list"),
            AgentToolSpec(name="mcp_local:deepsearch"),
            AgentToolSpec(name="mcp_local:code_interpreter", policy={"risk": "high"}),
        ],
    )

    async def fake_fetch_tools(_self):
        return [
            SimpleNamespace(
                name="mcp_local:deepsearch",
                description="Search",
                input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
                output_schema={},
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

    payload = asyncio.run(
        app.list_agent_session_tools(
            "session-tools",
            agent_id=None,
            current_user=SimpleNamespace(user_id="user-1"),
        )
    )

    names = [item["name"] for item in payload["items"]]
    assert payload["sessionId"] == "session-tools"
    assert payload["agentId"] == "tool-agent"
    assert "builtin:plan_tool" in names
    assert "builtin:set_todo_list" in names
    assert "mcp_local:deepsearch" in names
    assert payload["items"][names.index("builtin:plan_tool")]["source"] == "builtin"
    assert payload["items"][names.index("builtin:set_todo_list")]["source"] == "builtin"
    assert payload["items"][names.index("mcp_local:deepsearch")]["source"] == "mcp"
    assert payload["items"][names.index("mcp_local:deepsearch")]["displayName"] == "deepsearch"
    assert payload["items"][names.index("mcp_local:deepsearch")]["mcpServerId"] == "mcp_local"
    assert payload["blockedTools"][0]["name"] == "mcp_local:code_interpreter"
    assert payload["blockedTools"][0]["blockReason"] == "high_risk_requires_enable"
    assert payload["blockedTools"][0]["requiresApproval"] is True
    assert payload["unavailable"] == payload["blockedTools"]

    with pytest.raises(app.HTTPException) as exc_info:
        asyncio.run(
            app.list_agent_session_tools(
                "session-tools",
                agent_id=None,
                current_user=SimpleNamespace(user_id="user-2"),
            )
        )
    assert exc_info.value.status_code == 404


def test_mcp_server_status_and_refresh_api(app_modules, monkeypatch):
    app, _tasks = app_modules

    class FakeRegistry:
        refreshed = False
        refreshed_server_id = None
        refreshed_keep_last = None

        def refresh(self, keep_last_on_failure=True):
            self.refreshed = keep_last_on_failure

        def refresh_server(self, server_id, keep_last_on_failure=True):
            self.refreshed_server_id = server_id
            self.refreshed_keep_last = keep_last_on_failure
            return server_id == "mcp_local"

        def list_tools(self):
            return [SimpleNamespace(name="web_search"), SimpleNamespace(name="file_read")]

        def list_servers(self):
            return [
                SimpleNamespace(
                    url="http://mcp.example.test/mcp",
                    protocol=SimpleNamespace(value="streamable-http"),
                    tool_prefix="mcp_local",
                    authorization_configured=False,
                    status="ok",
                    tool_count=2,
                    error="",
                    last_checked_at=1780730000.0,
                    duration_ms=25,
                )
            ]

    registry = FakeRegistry()
    monkeypatch.setattr(app, "_get_mcp_market_registry", lambda: registry)

    payload = asyncio.run(app.list_agent_mcp_servers(current_user=SimpleNamespace(user_id="user-1")))
    assert payload["source"] == "registry"
    assert payload["toolCount"] == 2
    assert payload["items"][0]["toolPrefix"] == "mcp_local"
    assert payload["items"][0]["status"] == "ok"

    refreshed = asyncio.run(app.refresh_agent_mcp_tools(current_user=SimpleNamespace(user_id="user-1")))
    assert refreshed["refreshed"] is True
    assert registry.refreshed is True

    refreshed_server = asyncio.run(
        app.refresh_agent_mcp_server("mcp_local", current_user=SimpleNamespace(user_id="user-1"))
    )
    assert refreshed_server["refreshed"] is True
    assert refreshed_server["serverId"] == "mcp_local"
    assert refreshed_server["refreshScope"] == "server"
    assert registry.refreshed_server_id == "mcp_local"
    assert registry.refreshed_keep_last is True

    with pytest.raises(app.HTTPException) as exc_info:
        asyncio.run(
            app.refresh_agent_mcp_server("missing", current_user=SimpleNamespace(user_id="user-1"))
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "MCP server not found"


def test_mcp_tool_dry_run_executes_allowed_tool_with_alias(app_modules, monkeypatch):
    app, _tasks = app_modules
    from brain.core.agent_registry import AgentToolSpec

    agent = app.AgentConfig(
        id="tool-agent",
        name="Tool Agent",
        tools=[AgentToolSpec(name="mcp_local:web_search")],
    )

    class FakeTool:
        name = "mcp_local-web_search"
        description = "Search"
        input_schema = {"type": "object", "properties": {"query": {"type": "string"}}}
        output_schema = {}

        async def execute(self, input_obj):
            return f"searched:{input_obj['query']}"

    async def fake_fetch_tools(_self):
        return [FakeTool()]

    monkeypatch.setattr(app.agentRegistry, "reload", lambda: None)
    monkeypatch.setattr(app.agentRegistry, "get", lambda agent_id: agent if agent_id == "tool-agent" else None)
    monkeypatch.setattr(app.MCPToolFetcher, "fetch_tools", fake_fetch_tools)

    payload = asyncio.run(
        app.dry_run_agent_mcp_tool(
            "mcp_local:web_search",
            app.AgentMCPToolDryRunReq(
                arguments={"query": "TaskPilot"},
                agent_id="tool-agent",
            ),
            current_user=SimpleNamespace(user_id="user-1"),
        )
    )

    assert payload["ok"] is True
    assert payload["dryRun"] is True
    assert payload["agentId"] == "tool-agent"
    assert payload["requestedToolName"] == "mcp_local:web_search"
    assert payload["toolName"] == "mcp_local-web_search"
    assert payload["result"] == "searched:TaskPilot"
    assert payload["execution"]["tool"] == "mcp_local-web_search"


def test_mcp_tool_dry_run_blocks_disallowed_tool(app_modules, monkeypatch):
    app, _tasks = app_modules
    from brain.core.agent_registry import AgentToolSpec

    agent = app.AgentConfig(
        id="tool-agent",
        name="Tool Agent",
        tools=[AgentToolSpec(name="mcp_local:shell_exec", policy={"risk": "high"})],
    )

    class FakeTool:
        name = "mcp_local-shell_exec"
        description = "Shell"
        input_schema = {"type": "object"}
        output_schema = {}

        async def execute(self, input_obj):
            return "should-not-run"

    async def fake_fetch_tools(_self):
        return [FakeTool()]

    monkeypatch.delenv("APP_ALLOW_HIGH_RISK_TOOLS", raising=False)
    monkeypatch.delenv("ALLOW_HIGH_RISK_TOOLS", raising=False)
    monkeypatch.setattr(app.agentRegistry, "reload", lambda: None)
    monkeypatch.setattr(app.agentRegistry, "get", lambda agent_id: agent if agent_id == "tool-agent" else None)
    monkeypatch.setattr(app.MCPToolFetcher, "fetch_tools", fake_fetch_tools)

    with pytest.raises(app.HTTPException) as exc_info:
        asyncio.run(
            app.dry_run_agent_mcp_tool(
                "mcp_local:shell_exec",
                app.AgentMCPToolDryRunReq(
                    arguments={"command": "pwd"},
                    agent_id="tool-agent",
                ),
                current_user=SimpleNamespace(user_id="user-1"),
            )
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "high_risk_requires_enable"


def test_session_tool_test_executes_allowed_tool_with_colon_hyphen_alias(app_modules, monkeypatch):
    app, _tasks = app_modules
    from brain.core.agent_registry import AgentToolSpec

    session_store = app.SessionStore()
    session_store.create_session(
        session_id="session-tool-test",
        user_id="user-1",
        agent_id="tool-agent",
    )
    agent = app.AgentConfig(
        id="tool-agent",
        name="Tool Agent",
        tools=[AgentToolSpec(name="mcp_local:web_search")],
    )

    class FakeTool:
        name = "mcp_local-web_search"
        description = "Search"
        input_schema = {"type": "object", "properties": {"query": {"type": "string"}}}
        output_schema = {}

        async def execute(self, input_obj):
            return f"searched:{input_obj['query']}"

    async def fake_fetch_tools(_self):
        return [FakeTool()]

    monkeypatch.setattr(app.agentRegistry, "reload", lambda: None)
    monkeypatch.setattr(app.agentRegistry, "get", lambda agent_id: agent if agent_id == "tool-agent" else None)
    monkeypatch.setattr(app.MCPToolFetcher, "fetch_tools", fake_fetch_tools)

    payload = asyncio.run(
        app.test_agent_session_tool(
            "session-tool-test",
            app.AgentMCPToolTestReq(
                tool_name="mcp_local:web_search",
                arguments={"query": "TaskPilot"},
            ),
            current_user=SimpleNamespace(user_id="user-1"),
        )
    )

    assert payload["ok"] is True
    assert payload["requestedToolName"] == "mcp_local:web_search"
    assert payload["toolName"] == "mcp_local-web_search"
    assert payload["result"] == "searched:TaskPilot"
    assert payload["execution"]["tool"] == "mcp_local-web_search"


def test_session_tool_test_blocks_disallowed_tool(app_modules, monkeypatch):
    app, _tasks = app_modules
    from brain.core.agent_registry import AgentToolSpec

    session_store = app.SessionStore()
    session_store.create_session(
        session_id="session-tool-block",
        user_id="user-1",
        agent_id="tool-agent",
    )
    agent = app.AgentConfig(
        id="tool-agent",
        name="Tool Agent",
        tools=[AgentToolSpec(name="mcp_local:shell_exec", policy={"risk": "high"})],
    )

    class FakeTool:
        name = "mcp_local-shell_exec"
        description = "Shell"
        input_schema = {"type": "object"}
        output_schema = {}

        async def execute(self, input_obj):
            return "should-not-run"

    async def fake_fetch_tools(_self):
        return [FakeTool()]

    monkeypatch.delenv("APP_ALLOW_HIGH_RISK_TOOLS", raising=False)
    monkeypatch.delenv("ALLOW_HIGH_RISK_TOOLS", raising=False)
    monkeypatch.setattr(app.agentRegistry, "reload", lambda: None)
    monkeypatch.setattr(app.agentRegistry, "get", lambda agent_id: agent if agent_id == "tool-agent" else None)
    monkeypatch.setattr(app.MCPToolFetcher, "fetch_tools", fake_fetch_tools)

    with pytest.raises(app.HTTPException) as exc_info:
        asyncio.run(
            app.test_agent_session_tool(
                "session-tool-block",
                app.AgentMCPToolTestReq(
                    tool_name="mcp_local:shell_exec",
                    arguments={"command": "pwd"},
                ),
                current_user=SimpleNamespace(user_id="user-1"),
            )
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "high_risk_requires_enable"


def test_autoagent_records_approval_requested_for_unapproved_high_risk_tool(app_modules, monkeypatch):
    app, tasks = app_modules
    from brain.core.agent_registry import AgentToolSpec

    agent = app.AgentConfig(
        id="approval-agent",
        name="Approval Agent",
        tools=[
            AgentToolSpec(
                name="mcp_local:code_interpreter",
                description="Run code",
                policy={"risk": "high"},
            )
        ],
        permissions={"require_approval_for": ["high_risk_tools"]},
    )

    async def fake_memory_context(ctx, query):
        return {"memoryCount": 0, "ragCount": 0, "querySummary": query}

    async def fake_tool_collection(ctx):
        return SimpleNamespace(
            tool_map={},
            blocked_tools=["mcp_local:code_interpreter"],
        )

    handler_requested = []

    class FakeHandler:
        async def handle(self, ctx, _request):
            ctx.printer.send(None, "result", "should not run", None, True)

    def fake_get_handler(_ctx, _request):
        handler_requested.append(True)
        return FakeHandler()

    monkeypatch.setattr(app, "_resolve_agent_config", lambda agent_id: agent)
    monkeypatch.setattr(app, "_load_task_memory_context", fake_memory_context)
    monkeypatch.setattr(app, "build_tool_collection", fake_tool_collection)
    monkeypatch.setattr(app.agentFactory, "get_handler", fake_get_handler)

    asyncio.run(
        app._run_autoagent(
            app.GptQueryReq(
                trace_id="approval-run",
                user_id="user-1",
                agent_id="approval-agent",
                conversation_id="approval-session",
                language="ch",
                selected_tools=["mcp_local:code_interpreter"],
                messages=[app.AgentMessage(role="user", content="run code")],
            ),
            lambda _data: None,
        )
    )

    store = tasks.TaskStore()
    task = store.get_task("approval-run")
    events = store.list_events("approval-run")
    policy_event = next(event for event in events if event.event_type == "tool_policy_applied")
    approval_event = next(event for event in events if event.event_type == "approval_requested")
    waiting_event = next(event for event in events if event.event_type == "task_waiting_approval")
    policy_payload = tasks.serialize_event(policy_event)["payload"]
    approval_payload = tasks.serialize_event(approval_event)["payload"]
    waiting_payload = tasks.serialize_event(waiting_event)["payload"]

    assert handler_requested == []
    assert task.status == tasks.AgentTaskStatus.WAITING_APPROVAL
    assert policy_payload["blockedToolReasons"] == {
        "mcp_local:code_interpreter": "high_risk_requires_approval"
    }
    assert approval_payload["approvalType"] == "high_risk_tools"
    assert approval_payload["requests"][0]["tool"] == "mcp_local:code_interpreter"
    assert approval_payload["requests"][0]["reason"] == "high_risk_requires_approval"
    assert approval_payload["requests"][0]["riskLevel"] == "high"
    assert approval_payload["selectedTools"] == ["mcp_local:code_interpreter"]
    assert approval_payload["approvedTools"] is None
    assert waiting_payload["status"] == tasks.AgentTaskStatus.WAITING_APPROVAL
    assert "task_completed" not in [event.event_type for event in events]
    assert "agent_completed" not in [event.event_type for event in events]

    session_store = app.SessionStore()
    session_payload = app.serialize_session(session_store.get_session("approval-session"))
    assert session_payload["status"] == app.AgentSessionStatus.WAITING_APPROVAL
    assert session_payload["currentRunId"] == "approval-run"
    assert session_payload["lastMessagePreview"] == "需要审批工具：mcp_local:code_interpreter"
    messages = session_store.list_messages("approval-session")
    assert messages[-1].role == app.AgentMessageRole.ASSISTANT
    assert messages[-1].status == app.AgentSessionStatus.WAITING_APPROVAL


def test_autoagent_does_not_request_approval_for_unselected_high_risk_tool(app_modules, monkeypatch):
    app, tasks = app_modules
    from brain.core.agent_registry import AgentToolSpec

    agent = app.AgentConfig(
        id="approval-agent",
        name="Approval Agent",
        tools=[
            AgentToolSpec(
                name="mcp_local:code_interpreter",
                description="Run code",
                policy={"risk": "high"},
            )
        ],
        permissions={"require_approval_for": ["high_risk_tools"]},
    )

    async def fake_memory_context(ctx, query):
        return {"memoryCount": 0, "ragCount": 0, "querySummary": query}

    async def fake_tool_collection(ctx):
        return SimpleNamespace(
            tool_map={},
            blocked_tools=["mcp_local:code_interpreter"],
        )

    handler_requested = []

    class FakeHandler:
        async def handle(self, ctx, _request):
            handler_requested.append(True)
            ctx.printer.send(None, "result", "北京今天晴", None, True)

    monkeypatch.setattr(app, "_resolve_agent_config", lambda agent_id: agent)
    monkeypatch.setattr(app, "_load_task_memory_context", fake_memory_context)
    monkeypatch.setattr(app, "build_tool_collection", fake_tool_collection)
    monkeypatch.setattr(app.agentFactory, "get_handler", lambda _ctx, _request: FakeHandler())

    asyncio.run(
        app._run_autoagent(
            app.GptQueryReq(
                trace_id="approval-not-selected-run",
                user_id="user-1",
                agent_id="approval-agent",
                conversation_id="approval-not-selected-session",
                language="ch",
                messages=[app.AgentMessage(role="user", content="北京天气")],
            ),
            lambda _data: None,
        )
    )

    store = tasks.TaskStore()
    task = store.get_task("approval-not-selected-run")
    events = store.list_events("approval-not-selected-run")
    event_types = [event.event_type for event in events]
    policy_event = next(event for event in events if event.event_type == "tool_policy_applied")
    policy_payload = tasks.serialize_event(policy_event)["payload"]

    assert handler_requested == [True]
    assert task.status == tasks.AgentTaskStatus.COMPLETED
    assert policy_payload["blockedToolReasons"] == {
        "mcp_local:code_interpreter": "high_risk_requires_approval"
    }
    assert "approval_requested" not in event_types
    assert "task_waiting_approval" not in event_types
    assert "task_completed" in event_types


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
        "https://files.example.test/output.csv?token=raw-secret",
        filename="output.csv",
    )

    list_payload = asyncio.run(app.list_agent_task_artifacts("remote-download"))
    response = asyncio.run(app.download_agent_task_artifact("remote-download", artifact.artifact_id))

    assert list_payload["items"][0]["remoteUrl"] == "https://files.example.test/output.csv?token=***"
    assert response.status_code in {302, 307}
    assert response.headers["location"] == "https://files.example.test/output.csv?token=raw-secret"


def test_tool_result_search_urls_are_not_registered_as_remote_artifacts(app_modules):
    app, _tasks = app_modules

    search_event = {
        "messageType": "tool_result",
        "tool": "mcp_local:web_search",
        "result": {
            "results": [
                {
                    "title": "遵化",
                    "url": "https://zh.wikipedia.org/zh-hans/%E9%81%B5%E5%8C%96%E5%B8%82",
                    "content": "百科页面摘要",
                }
            ]
        },
    }
    file_event = {
        "messageType": "tool_result",
        "tool": "mcp_local:code_interpreter",
        "result": {
            "fileInfo": [
                {
                    "fileName": "analysis.txt",
                    "download_url": "https://files.example.test/analysis.txt",
                    "fileSize": 42,
                }
            ]
        },
    }

    assert app._extract_remote_artifacts(search_event) == []
    artifacts = app._extract_remote_artifacts(file_event)
    assert len(artifacts) == 1
    assert artifacts[0]["filename"] == "analysis.txt"
    assert artifacts[0]["url"] == "https://files.example.test/analysis.txt"


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
        run_id="parent-run",
        query="parent task",
        task=None,
        printer=None,
        toolCollection=None,
        dateInfo="2026-05-30",
        task_id="parent-task",
        outputStyle="markdown",
        approved_tools=["mcp_local:code_interpreter"],
        run_environment="sandbox",
        language="en",
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
    assert payload["sessionId"] == "session-parent"
    assert payload["metadata"]["source"] == "handoff"
    assert payload["metadata"]["parentTaskId"] == "parent-task"
    assert payload["metadata"]["runEnvironment"] == "sandbox"
    assert payload["metadata"]["language"] == "en"
    assert payload["metadata"]["approvedTools"] == ["mcp_local:code_interpreter"]
    assert payload["metadata"]["agentSnapshot"]["id"] == "child-agent"
    assert created_background

    store = tasks.TaskStore()
    events = store.list_events(payload["taskId"])
    assert events[-1].event_type == "task_queued"
    assert tasks.serialize_event(events[-1])["payload"]["parentAgentId"] == "parent-agent"
    assert tasks.serialize_event(events[-1])["payload"]["approvedTools"] == ["mcp_local:code_interpreter"]
    assert tasks.serialize_event(events[-1])["payload"]["language"] == "en"
    parent_events = store.list_events("parent-task")
    assert parent_events[-1].event_type == "task_handoff_requested"
    assert tasks.serialize_event(parent_events[-1])["payload"]["targetAgentId"] == "child-agent"
    assert tasks.serialize_event(parent_events[-1])["payload"]["childTaskId"] == payload["taskId"]
    assert tasks.serialize_event(parent_events[-1])["payload"]["approvedTools"] == ["mcp_local:code_interpreter"]
    assert tasks.serialize_event(parent_events[-1])["payload"]["language"] == "en"
    assert tasks.serialize_event(parent_events[-1])["payload"]["targetAgentSnapshot"]["id"] == "child-agent"

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

    payload = asyncio.run(app.run_agent_evals("eval-agent", user_id="tester", output_style="markdown"))

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


def test_evaluate_eval_task_records_result_event(app_modules, tmp_path, monkeypatch):
    app, tasks = app_modules
    monkeypatch.setenv("TASK_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    store = tasks.TaskStore()
    store.create_task(
        task_id="eval-result-task",
        trace_id="trace-eval-result",
        input_text="run eval",
        metadata={
            "source": "eval",
            "evalCaseId": "case-a",
            "expected": "answer includes result",
            "evalMetadata": {
                "checks": {
                    "final_status": "completed",
                    "output_contains": ["result"],
                    "required_event_types": ["tool_call"],
                    "min_artifacts": 1,
                }
            },
        },
    )
    store.update_status("eval-result-task", tasks.AgentTaskStatus.RUNNING)
    store.add_event("eval-result-task", "tool_call", {"tool": "demo"}, trace_id="trace-eval-result", source="sse")
    work_dir = tasks.serialize_task(store.get_task("eval-result-task"))["workDir"]
    artifact_path = Path(work_dir) / "result.txt"
    artifact_path.write_text("artifact", encoding="utf-8")
    store.add_artifact("eval-result-task", str(artifact_path), filename="result.txt")
    store.update_status("eval-result-task", tasks.AgentTaskStatus.COMPLETED, output_text="final result")

    payload = asyncio.run(app.evaluate_agent_task("eval-result-task"))

    assert payload["status"] == "passed"
    assert payload["passed"] is True
    eval_events = store.list_events("eval-result-task", event_type="eval_result")
    assert tasks.serialize_event(eval_events[-1])["payload"]["status"] == "passed"


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
