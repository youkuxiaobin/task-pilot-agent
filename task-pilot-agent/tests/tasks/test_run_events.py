from __future__ import annotations

from brain.core.run_events import (
    PLAN_EVENT_TYPES,
    PRINTER_PLAN_EVENT_TYPES,
    RUN_EVENT_SCHEMA_VERSION,
    RunEventType,
    event_category,
    event_contract_fields,
    plan_event_type_for_command,
    session_event_alias,
)


def test_plan_event_contract_includes_plan_events_and_separates_todo_progress():
    assert {
        RunEventType.PLAN,
        RunEventType.PLAN_CREATED,
        RunEventType.PLAN_UPDATED,
        RunEventType.PLAN_STEP_STARTED,
        RunEventType.PLAN_STEP_COMPLETED,
        RunEventType.PLAN_STEP_FAILED,
        RunEventType.PLAN_STEP_UPDATED,
        RunEventType.PLAN_COMPLETED,
        RunEventType.PLAN_FAILED,
        RunEventType.PLAN_CANCELLED,
    }.issubset(PLAN_EVENT_TYPES)
    assert RunEventType.TODO_LIST_UPDATED not in PLAN_EVENT_TYPES
    assert RunEventType.TODO_LIST_UPDATED in PRINTER_PLAN_EVENT_TYPES


def test_plan_event_type_for_command_matches_builtin_plan_tool_outputs():
    assert plan_event_type_for_command("create") == RunEventType.PLAN_CREATED
    assert plan_event_type_for_command("update") == RunEventType.PLAN_UPDATED
    assert plan_event_type_for_command("get_plan") == RunEventType.PLAN_UPDATED
    assert plan_event_type_for_command("add_step") == RunEventType.PLAN_UPDATED
    assert plan_event_type_for_command("finish") == RunEventType.PLAN_COMPLETED
    assert plan_event_type_for_command("skip_step") == RunEventType.PLAN_STEP_UPDATED
    assert plan_event_type_for_command("mark_step", "running") == RunEventType.PLAN_STEP_STARTED
    assert plan_event_type_for_command("mark_step", "completed") == RunEventType.PLAN_STEP_COMPLETED
    assert plan_event_type_for_command("mark_step", "failed") == RunEventType.PLAN_STEP_FAILED
    assert plan_event_type_for_command("mark_step", "waiting_input") == RunEventType.PLAN_STEP_UPDATED
    assert plan_event_type_for_command("unknown") == RunEventType.PLAN_UPDATED


def test_session_event_aliases_keep_frontend_replay_names_stable():
    assert session_event_alias(RunEventType.TASK_CREATED) == "run_created"
    assert session_event_alias(RunEventType.TASK_RUNNING) == "run_started"
    assert session_event_alias(RunEventType.TASK_QUEUED) == "run_queued"
    assert session_event_alias(RunEventType.TASK_COMPLETED) == "run_completed"
    assert session_event_alias(RunEventType.TASK_FAILED) == "run_failed"
    assert session_event_alias(RunEventType.USER_INPUT) == "user_input_received"
    assert session_event_alias(RunEventType.TASK_ARTIFACT_ADDED) == "artifact_created"
    assert session_event_alias(RunEventType.RESULT) == "assistant_text_delta"
    assert session_event_alias("custom_event") == "custom_event"


def test_event_category_groups_runtime_contract_events():
    assert event_category(RunEventType.TASK_CREATED) == "task"
    assert event_category(RunEventType.AGENT_STARTED) == "agent"
    assert event_category(RunEventType.ASSISTANT_MESSAGE_COMPLETED) == "message"
    assert event_category(RunEventType.PLAN_STEP_COMPLETED) == "plan"
    assert event_category(RunEventType.TOOL_CALL) == "tool"
    assert event_category(RunEventType.TOOL_POLICY_APPLIED) == "policy"
    assert event_category(RunEventType.RUNTIME_BOUNDARY_APPLIED) == "context"
    assert event_category(RunEventType.APPROVAL_REQUESTED) == "approval"
    assert event_category(RunEventType.WAITING_INPUT) == "user_input"
    assert event_category(RunEventType.TASK_ARTIFACT_ADDED) == "artifact"
    assert event_category(RunEventType.TODO_LIST_UPDATED) == "progress"
    assert event_category(RunEventType.STREAM_EVENT) == "stream"
    assert event_category("custom_event") == "unknown"


def test_event_contract_fields_add_schema_category_and_alias():
    fields = event_contract_fields(RunEventType.TOOL_CALL)

    assert fields["eventSchemaVersion"] == RUN_EVENT_SCHEMA_VERSION
    assert fields["event_schema_version"] == RUN_EVENT_SCHEMA_VERSION
    assert fields["eventCategory"] == "tool"
    assert fields["event_category"] == "tool"
    assert fields["eventAlias"] == "tool_call_started"
    assert fields["event_alias"] == "tool_call_started"
