from __future__ import annotations

import importlib
import asyncio
import json
from pathlib import Path
from typing import Any, List

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
    event_payload = task_modules.serialize_event(events[0])["payload"]
    assert event_payload["args"]["query"] == "public info"
    assert event_payload["args"]["authorization"] == "***"


def test_task_store_lists_tasks_by_owner_status_and_agent(task_modules):
    store = task_modules.TaskStore()

    store.create_task(
        task_id="task-a",
        trace_id="trace-a",
        user_id="user-a",
        agent_id="agent-a",
        input_text="weather lookup",
    )
    store.create_task(
        task_id="task-b",
        trace_id="trace-b",
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

    completed_tasks = store.list_tasks(status=task_modules.AgentTaskStatus.COMPLETED)
    assert [task.task_id for task in completed_tasks] == ["task-a"]

    agent_a_tasks = store.list_tasks(agent_id="agent-a")
    assert {task.task_id for task in agent_a_tasks} == {"task-a", "task-c"}

    weather_tasks = store.list_tasks(keyword="weather")
    assert [task.task_id for task in weather_tasks] == ["task-a"]

    error_tasks = store.list_tasks(keyword="missing source")
    assert [task.task_id for task in error_tasks] == ["task-b"]


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
        "https://files.example.test/output/report.md",
        filename="report.md",
        file_size=128,
        metadata={"api_key": "sk-test-remote-secretvalue123"},
    )

    payload = task_modules.serialize_artifact(artifact)
    assert payload["filename"] == "report.md"
    assert payload["remoteUrl"] == "https://files.example.test/output/report.md"
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
    printer = SSEPrinter(output.append, "request-1", task_id="task-1", event_sink=events.append)

    printer.send("message-1", "tool_call", {"name": "deepsearch"}, None, False)
    printer.send("phase-1", "agent_phase", {"phase": "planning", "status": "started"}, None, False)

    assert len(events) == 2
    assert events[0]["requestId"] == "request-1"
    assert events[0]["taskId"] == "task-1"
    assert events[0]["messageType"] == "tool_call"
    assert events[1]["messageType"] == "agent_phase"
    assert events[1]["resultMap"]["phase"] == "planning"

    streamed_payload = json.loads(output[0].removeprefix("data: ").strip())
    assert streamed_payload["taskId"] == "task-1"
    assert streamed_payload["toolCall"]["name"] == "deepsearch"


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
            assert kwargs["run_id"] == "conversation-autoagent"
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
            assert ctx.memory_context["memoryCount"] == 1
            assert ctx.memory_context["ragCount"] == 1
            assert ctx.memory_context["memoryResults"][0]["metadata"]["api_key"] == "***"
            Path(ctx.work_dir, "reports").mkdir(parents=True)
            Path(ctx.work_dir, "reports", "local_report.txt").write_text("local artifact", encoding="utf-8")
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
            ctx.printer.send("result-1", "result", "final answer", None, True)

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
        messages=[app_module.AgentMessage(role="user", content="hello")],
    )

    asyncio.run(app_module._run_autoagent(req, output.append))

    store = task_modules.TaskStore()
    task = store.get_task("trace-autoagent")
    assert task is not None
    assert task.status == task_modules.AgentTaskStatus.COMPLETED
    assert task.output_text == "final answer"
    assert task_modules.serialize_task(task)["metadata"]["runEnvironment"] == "sandbox"

    event_types = [event.event_type for event in store.list_events("trace-autoagent")]
    assert "task_created" in event_types
    assert "task_running" in event_types
    assert "agent_started" in event_types
    assert "memory_context_loaded" in event_types
    assert "agent_phase" in event_types
    assert "runtime_boundary_applied" in event_types
    assert "tool_policy_applied" in event_types
    assert "tool_result" in event_types
    assert "task_artifact_added" in event_types
    assert "result" in event_types
    assert "agent_completed" in event_types
    assert "task_completed" in event_types

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
    assert memory_payload["memoryCount"] == 1
    assert memory_payload["ragCount"] == 1
    assert memory_payload["memoryResults"][0]["snippet"] == "remember public context"
    assert memory_payload["memoryResults"][0]["metadata"]["api_key"] == "***"
    assert memory_payload["ragResults"][0]["metadata"]["title"] == "demo"

    streamed_events = [
        json.loads(item.removeprefix("data: ").strip())
        for item in output
        if item.startswith("data: {")
    ]
    assert streamed_events[0]["taskId"] == "trace-autoagent"
