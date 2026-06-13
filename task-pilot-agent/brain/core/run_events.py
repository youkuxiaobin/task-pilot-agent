from __future__ import annotations

from typing import Any, Dict, Optional


RUN_EVENT_SCHEMA_VERSION = 1


class RunEventType:
    TASK_CREATED = "task_created"
    TASK_RESUMED = "task_resumed"
    TASK_QUEUED = "task_queued"
    TASK_RUNNING = "task_running"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"
    TASK_CANCEL_REQUESTED = "task_cancel_requested"
    TASK_RETRY_REQUESTED = "task_retry_requested"
    TASK_RESUME_REQUESTED = "task_resume_requested"
    TASK_WAITING_APPROVAL = "task_waiting_approval"
    TASK_ARTIFACT_ADDED = "task_artifact_added"

    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    AGENT_FAILED = "agent_failed"
    AGENT_CANCELLED = "agent_cancelled"
    AGENT_SELECTED = "agent_selected"
    AGENT_HANDOFF_REQUESTED = "task_handoff_requested"

    USER_MESSAGE_CREATED = "user_message_created"
    ASSISTANT_MESSAGE_STARTED = "assistant_message_started"
    ASSISTANT_MESSAGE_COMPLETED = "assistant_message_completed"

    MEMORY_CONTEXT_LOADED = "memory_context_loaded"
    RUNTIME_BOUNDARY_APPLIED = "runtime_boundary_applied"
    TOOL_POLICY_APPLIED = "tool_policy_applied"

    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"

    WAITING_INPUT = "waiting_input"
    USER_INPUT = "user_input"

    STREAM_EVENT = "stream_event"

    PLAN = "plan"
    PLAN_CREATED = "plan_created"
    PLAN_UPDATED = "plan_updated"
    PLAN_STEP_STARTED = "plan_step_started"
    PLAN_STEP_COMPLETED = "plan_step_completed"
    PLAN_STEP_FAILED = "plan_step_failed"
    PLAN_STEP_UPDATED = "plan_step_updated"
    PLAN_COMPLETED = "plan_completed"
    PLAN_FAILED = "plan_failed"
    PLAN_CANCELLED = "plan_cancelled"
    TODO_LIST_UPDATED = "todo_list_updated"

    RESULT = "result"
    AGENT_STREAM = "agent_stream"
    TASK_PROGRESS = "task"
    NOTIFICATIONS = "notifications"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    STREAM = "stream"
    DEEP_SEARCH = "deep_search"
    FILE = "file"
    CODE = "code"
    BROWSER = "browser"
    HTML = "html"
    MARKDOWN = "markdown"
    PPT = "ppt"
    KNOWLEDGE = "knowledge"
    TOOL_THOUGHT = "tool_thought"
    AGENT_PHASE = "agent_phase"
    TASK_SUMMARY = "task_summary"
    PLAN_THOUGHT = "plan_thought"


PLAN_EVENT_TYPES = frozenset(
    {
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
    }
)

PRINTER_PLAN_EVENT_TYPES = PLAN_EVENT_TYPES | frozenset({RunEventType.TODO_LIST_UPDATED})

EVENT_TYPE_ALIASES: Dict[str, str] = {
    RunEventType.RESULT: "assistant_text_delta",
    RunEventType.AGENT_STREAM: "assistant_text_delta",
    RunEventType.TASK_PROGRESS: "run_progress",
    RunEventType.NOTIFICATIONS: "run_progress",
    RunEventType.TOOL_CALL: "tool_call_started",
    RunEventType.TOOL_RESULT: "tool_call_completed",
    RunEventType.STREAM: "tool_call_progress",
    RunEventType.DEEP_SEARCH: "search_result",
    RunEventType.FILE: "artifact_created",
    RunEventType.CODE: "tool_call_completed",
    RunEventType.BROWSER: "tool_call_completed",
    RunEventType.HTML: "tool_call_completed",
    RunEventType.MARKDOWN: "tool_call_completed",
    RunEventType.PPT: "tool_call_completed",
    RunEventType.KNOWLEDGE: "tool_call_completed",
    RunEventType.TOOL_THOUGHT: "tool_call_progress",
    RunEventType.AGENT_PHASE: "agent_progress",
    RunEventType.TASK_SUMMARY: "assistant_message_completed",
}

SESSION_EVENT_TYPE_ALIASES: Dict[str, str] = {
    RunEventType.TASK_CREATED: "run_created",
    RunEventType.TASK_RESUMED: "run_started",
    RunEventType.TASK_RUNNING: "run_started",
    RunEventType.TASK_QUEUED: "run_queued",
    RunEventType.TASK_COMPLETED: "run_completed",
    RunEventType.TASK_FAILED: "run_failed",
    RunEventType.TASK_CANCEL_REQUESTED: "run_cancelled",
    RunEventType.TASK_RETRY_REQUESTED: "run_retry_requested",
    RunEventType.TASK_RESUME_REQUESTED: "run_started",
    RunEventType.TASK_ARTIFACT_ADDED: "artifact_created",
    RunEventType.USER_INPUT: "user_input_received",
    RunEventType.USER_MESSAGE_CREATED: RunEventType.USER_MESSAGE_CREATED,
    RunEventType.ASSISTANT_MESSAGE_STARTED: RunEventType.ASSISTANT_MESSAGE_STARTED,
    RunEventType.ASSISTANT_MESSAGE_COMPLETED: RunEventType.ASSISTANT_MESSAGE_COMPLETED,
}

TASK_LIFECYCLE_EVENT_TYPES = frozenset(
    {
        RunEventType.TASK_CREATED,
        RunEventType.TASK_RESUMED,
        RunEventType.TASK_QUEUED,
        RunEventType.TASK_RUNNING,
        RunEventType.TASK_COMPLETED,
        RunEventType.TASK_FAILED,
        RunEventType.TASK_CANCELLED,
        RunEventType.TASK_CANCEL_REQUESTED,
        RunEventType.TASK_RETRY_REQUESTED,
        RunEventType.TASK_RESUME_REQUESTED,
        RunEventType.TASK_WAITING_APPROVAL,
    }
)

AGENT_EVENT_TYPES = frozenset(
    {
        RunEventType.AGENT_STARTED,
        RunEventType.AGENT_COMPLETED,
        RunEventType.AGENT_FAILED,
        RunEventType.AGENT_CANCELLED,
        RunEventType.AGENT_SELECTED,
        RunEventType.AGENT_HANDOFF_REQUESTED,
        RunEventType.AGENT_PHASE,
    }
)

MESSAGE_EVENT_TYPES = frozenset(
    {
        RunEventType.USER_MESSAGE_CREATED,
        RunEventType.ASSISTANT_MESSAGE_STARTED,
        RunEventType.ASSISTANT_MESSAGE_COMPLETED,
        RunEventType.TASK_SUMMARY,
        RunEventType.RESULT,
        RunEventType.AGENT_STREAM,
    }
)

CONTEXT_EVENT_TYPES = frozenset({RunEventType.MEMORY_CONTEXT_LOADED, RunEventType.RUNTIME_BOUNDARY_APPLIED})
POLICY_EVENT_TYPES = frozenset({RunEventType.TOOL_POLICY_APPLIED})
APPROVAL_EVENT_TYPES = frozenset({RunEventType.APPROVAL_REQUESTED, RunEventType.APPROVAL_RESOLVED})
USER_INPUT_EVENT_TYPES = frozenset({RunEventType.WAITING_INPUT, RunEventType.USER_INPUT})
ARTIFACT_EVENT_TYPES = frozenset({RunEventType.TASK_ARTIFACT_ADDED, RunEventType.FILE})
TOOL_EVENT_TYPES = frozenset(
    {
        RunEventType.TOOL_CALL,
        RunEventType.TOOL_RESULT,
        RunEventType.STREAM,
        RunEventType.DEEP_SEARCH,
        RunEventType.CODE,
        RunEventType.BROWSER,
        RunEventType.HTML,
        RunEventType.MARKDOWN,
        RunEventType.PPT,
        RunEventType.KNOWLEDGE,
        RunEventType.TOOL_THOUGHT,
    }
)
PROGRESS_EVENT_TYPES = frozenset({RunEventType.TASK_PROGRESS, RunEventType.NOTIFICATIONS, RunEventType.TODO_LIST_UPDATED})


def session_event_alias(event_type: str) -> str:
    normalized = str(event_type or "")
    return SESSION_EVENT_TYPE_ALIASES.get(normalized) or EVENT_TYPE_ALIASES.get(normalized, normalized)


def event_category(event_type: str) -> str:
    normalized = str(event_type or "")
    if normalized in PLAN_EVENT_TYPES or normalized == RunEventType.PLAN_THOUGHT:
        return "plan"
    if normalized in TOOL_EVENT_TYPES:
        return "tool"
    if normalized in TASK_LIFECYCLE_EVENT_TYPES:
        return "task"
    if normalized in AGENT_EVENT_TYPES:
        return "agent"
    if normalized in MESSAGE_EVENT_TYPES:
        return "message"
    if normalized in CONTEXT_EVENT_TYPES:
        return "context"
    if normalized in POLICY_EVENT_TYPES:
        return "policy"
    if normalized in APPROVAL_EVENT_TYPES:
        return "approval"
    if normalized in USER_INPUT_EVENT_TYPES:
        return "user_input"
    if normalized in ARTIFACT_EVENT_TYPES:
        return "artifact"
    if normalized in PROGRESS_EVENT_TYPES:
        return "progress"
    if normalized == RunEventType.STREAM_EVENT:
        return "stream"
    return "unknown"


def event_contract_fields(event_type: str) -> Dict[str, Any]:
    normalized = str(event_type or "")
    return {
        "eventSchemaVersion": RUN_EVENT_SCHEMA_VERSION,
        "event_schema_version": RUN_EVENT_SCHEMA_VERSION,
        "eventCategory": event_category(normalized),
        "event_category": event_category(normalized),
        "eventAlias": session_event_alias(normalized),
        "event_alias": session_event_alias(normalized),
    }


def plan_event_type_for_command(command: str, step_status: Optional[str] = None) -> str:
    normalized_command = str(command or "")
    if normalized_command == "create":
        return RunEventType.PLAN_CREATED
    if normalized_command in {"update", "get_plan", "add_step"}:
        return RunEventType.PLAN_UPDATED
    if normalized_command == "finish":
        return RunEventType.PLAN_COMPLETED
    if normalized_command == "skip_step":
        return RunEventType.PLAN_STEP_UPDATED
    if normalized_command == "mark_step":
        normalized_status = str(step_status or "")
        if normalized_status == "running":
            return RunEventType.PLAN_STEP_STARTED
        if normalized_status == "completed":
            return RunEventType.PLAN_STEP_COMPLETED
        if normalized_status == "failed":
            return RunEventType.PLAN_STEP_FAILED
        return RunEventType.PLAN_STEP_UPDATED
    return RunEventType.PLAN_UPDATED
