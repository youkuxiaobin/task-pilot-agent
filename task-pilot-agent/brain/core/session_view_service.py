from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from brain.core.printer import EVENT_TYPE_ALIASES
from brain.core.sessions import (
    SessionStore,
    serialize_agent_artifact,
    serialize_run,
    serialize_run_event,
)
from brain.core.tasks import AgentTaskStatus, TaskStore, serialize_artifact, serialize_event, serialize_task

EVENT_REPLAY_QUERY_LIMIT = 10000

PLAN_EVENT_TYPES = {
    "plan",
    "plan_created",
    "plan_updated",
    "plan_step_started",
    "plan_step_completed",
    "plan_step_failed",
    "plan_step_updated",
    "plan_completed",
    "plan_failed",
    "plan_cancelled",
}

SESSION_EVENT_TYPE_ALIASES = {
    "task_created": "run_created",
    "task_resumed": "run_started",
    "task_running": "run_started",
    "task_queued": "run_queued",
    "task_completed": "run_completed",
    "task_failed": "run_failed",
    "task_cancel_requested": "run_cancelled",
    "task_retry_requested": "run_retry_requested",
    "task_resume_requested": "run_started",
    "task_artifact_added": "artifact_created",
    "user_input": "user_input_received",
    "user_message_created": "user_message_created",
    "assistant_message_started": "assistant_message_started",
    "assistant_message_completed": "assistant_message_completed",
}


def _truncate_text(value: Any, limit: int = 320) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def attach_session_run_record(payload: Dict[str, Any], session_store: SessionStore, run_id: str) -> Dict[str, Any]:
    run_record = session_store.get_run(run_id)
    if run_record:
        payload["runRecord"] = serialize_run(run_record)
    return payload


def serialize_run_record_as_task_payload(run_record: Any) -> Dict[str, Any]:
    run_payload = serialize_run(run_record)
    payload = dict(run_payload)
    run_id = str(payload.get("runId") or "")
    payload["task_id"] = run_id
    payload["taskId"] = run_id
    payload["trace_id"] = payload.get("traceId") or run_id
    payload["conversation_id"] = payload.get("sessionId")
    payload["conversationId"] = payload.get("sessionId")
    payload["session_id"] = payload.get("sessionId")
    payload["runRecord"] = run_payload
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    payload["usage"] = metadata.get("usage") if isinstance(metadata.get("usage"), dict) else {}
    started_at = payload.get("startedAt")
    ended_at = payload.get("endedAt") or payload.get("updatedAt")
    payload["durationMs"] = (
        max(int(ended_at or 0) - int(started_at or 0), 0)
        if started_at is not None and ended_at is not None
        else None
    )
    payload["hasError"] = bool(payload.get("status") == AgentTaskStatus.FAILED or payload.get("errorMessage"))
    return payload


def serialize_session_run_payload(
    session_id: str,
    *,
    run_record: Optional[Any] = None,
    task_record: Optional[Any] = None,
) -> Dict[str, Any]:
    if task_record is not None:
        payload = serialize_task(task_record)
        payload["sessionId"] = session_id
        payload["runId"] = payload.get("taskId")
        if run_record is not None:
            payload["runRecord"] = serialize_run(run_record)
        return payload
    if run_record is not None:
        payload = serialize_run_record_as_task_payload(run_record)
        payload["sessionId"] = session_id
        return payload
    raise ValueError("run payload needs a run or task record")


def _run_payload_sort_key(payload: Dict[str, Any]) -> tuple[int, int]:
    return (
        int(payload.get("createdAt") or 0),
        int(payload.get("id") or 0),
    )


def collect_session_run_payloads(
    session_store: SessionStore,
    task_store: TaskStore,
    session_record: Any,
    *,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    session_id = session_record.session_id
    owner_user_id = session_record.user_id or ""
    normalized_status = (status or "").strip()
    task_records = task_store.list_tasks(
        user_id=owner_user_id or None,
        conversation_id=session_id,
        status=normalized_status or None,
        limit=2000,
    )
    task_by_run_id = {str(task.task_id): task for task in task_records}
    payloads: List[Dict[str, Any]] = []
    seen_run_ids: set[str] = set()

    for run_record in session_store.list_runs(
        session_id,
        status=normalized_status or None,
        limit=2000,
    ):
        run_id = str(getattr(run_record, "run_id", "") or "")
        if not run_id:
            continue
        if owner_user_id and getattr(run_record, "user_id", "") not in {"", owner_user_id}:
            continue
        payloads.append(
            serialize_session_run_payload(
                session_id,
                run_record=run_record,
                task_record=task_by_run_id.get(run_id),
            )
        )
        seen_run_ids.add(run_id)

    for task_record in task_records:
        run_id = str(task_record.task_id)
        if run_id in seen_run_ids:
            continue
        payloads.append(
            serialize_session_run_payload(
                session_id,
                task_record=task_record,
            )
        )

    payloads.sort(key=_run_payload_sort_key, reverse=True)
    count = len(payloads)
    resolved_offset = max(offset, 0)
    resolved_limit = max(min(limit, 200), 1)
    return {
        "items": payloads[resolved_offset : resolved_offset + resolved_limit],
        "count": count,
        "hasMore": resolved_offset + resolved_limit < count,
        "allItems": payloads,
        "taskRecords": task_records,
    }


def load_session_run_records(
    session_store: SessionStore,
    task_store: TaskStore,
    session_record: Any,
    run_id: str,
    current_user: Any,
    ensure_task_owner: Callable[[Any, Any], None],
) -> tuple[Optional[Any], Optional[Any]]:
    run_record = session_store.get_run(run_id)
    if run_record and run_record.session_id != session_record.session_id:
        run_record = None
    if run_record and session_record.user_id and run_record.user_id not in {"", session_record.user_id}:
        run_record = None
    task = task_store.get_task(run_id)
    if task and task.conversation_id != session_record.session_id:
        task = None
    if task:
        ensure_task_owner(task, current_user)
    if not run_record and not task:
        raise HTTPException(status_code=404, detail="run not found")
    return run_record, task


def collect_session_run_event_payloads(
    session_store: SessionStore,
    task_store: TaskStore,
    session_id: str,
    run_id: str,
    *,
    events_limit: int = 500,
) -> List[Dict[str, Any]]:
    run_events = session_store.list_run_events(
        session_id,
        run_id=run_id,
        limit=events_limit,
    )
    if run_events:
        payloads = [serialize_run_event(event) for event in run_events]
        for payload in payloads:
            payload["type"] = session_event_type(payload)
        return payloads

    return [
        {
            **serialize_event(event),
            "sessionId": session_id,
            "runId": run_id,
            "type": event.event_type,
        }
        for event in task_store.list_events(run_id, limit=events_limit)
    ]


def run_record_metadata(run_record: Any) -> Dict[str, Any]:
    payload = serialize_run(run_record)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return dict(metadata if isinstance(metadata, dict) else {})


def serialize_session_artifact(session_id: str, run_id: str, artifact: Any) -> Dict[str, Any]:
    if hasattr(artifact, "session_id") and hasattr(artifact, "run_id"):
        payload = serialize_agent_artifact(artifact)
        payload["sessionId"] = session_id or payload.get("sessionId")
        payload["runId"] = run_id or payload.get("runId")
        payload["taskId"] = payload.get("runId")
        return payload
    payload = serialize_artifact(artifact)
    payload["sessionId"] = session_id
    payload["runId"] = run_id
    return payload


def artifact_event_payload(session_id: str, run_id: str, artifact: Any) -> Dict[str, Any]:
    payload = serialize_session_artifact(session_id, run_id, artifact)
    payload["type"] = "artifact_created"
    return payload


def collect_session_artifacts(
    session_store: SessionStore,
    task_store: TaskStore,
    session_id: str,
    *,
    runs: Optional[List[Any]] = None,
    run_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    artifacts: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def append_payload(payload: Dict[str, Any]) -> None:
        artifact_key = str(payload.get("artifactId") or "")
        if artifact_key and artifact_key in seen:
            return
        if artifact_key:
            seen.add(artifact_key)
        artifacts.append(payload)

    for artifact in session_store.list_artifacts(session_id, run_id=run_id, limit=2000):
        append_payload(
            serialize_session_artifact(
                session_id,
                str(getattr(artifact, "run_id", "") or run_id or ""),
                artifact,
            )
        )

    if run_id:
        for artifact in task_store.list_artifacts(run_id):
            append_payload(serialize_session_artifact(session_id, run_id, artifact))
    else:
        task_runs = runs
        if task_runs is None:
            task_runs = task_store.list_tasks(conversation_id=session_id, limit=200)
        for run in task_runs:
            current_run_id = str(getattr(run, "task_id", "") or "")
            if not current_run_id:
                continue
            for artifact in task_store.list_artifacts(current_run_id):
                append_payload(serialize_session_artifact(session_id, current_run_id, artifact))

    return artifacts


def message_event_payload(
    *,
    session_id: str,
    run_id: str,
    message_id: str,
    role: str,
    content: Optional[str],
    status: str = "created",
    input_files: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    files = input_files or []
    return {
        "sessionId": session_id,
        "runId": run_id,
        "messageId": message_id,
        "role": role,
        "status": status,
        "contentPreview": _truncate_text(content or "", 240),
        "fileCount": len(files),
        "inputFiles": files,
    }


def _csv_filter_values(value: Optional[str]) -> List[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _matches_optional_filter(value: Any, allowed: List[str]) -> bool:
    return not allowed or str(value or "") in allowed


def _matches_session_event_type(item: Dict[str, Any], allowed: List[str]) -> bool:
    if not allowed:
        return True
    candidates = {
        str(item.get("eventType") or ""),
        str(item.get("type") or ""),
    }
    return any(candidate in allowed for candidate in candidates if candidate)


def session_event_type(payload: Dict[str, Any]) -> str:
    inner_payload = payload.get("payload")
    if isinstance(inner_payload, dict) and inner_payload.get("type"):
        return str(inner_payload.get("type") or "")
    event_type = str(payload.get("eventType") or "")
    return SESSION_EVENT_TYPE_ALIASES.get(event_type) or EVENT_TYPE_ALIASES.get(event_type, event_type)


def plan_payload_from_event_payload(event_payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(event_payload, dict):
        return None
    plan = event_payload.get("plan")
    if isinstance(plan, dict):
        return plan
    result_map = event_payload.get("resultMap")
    if isinstance(result_map, dict) and isinstance(result_map.get("plan"), dict):
        return result_map["plan"]
    if "steps" in event_payload or "step_status" in event_payload:
        return event_payload
    return None


def serialize_plan_event(session_id: str, run_id: str, event: Any, seq: int) -> Optional[Dict[str, Any]]:
    payload = serialize_event(event)
    plan = plan_payload_from_event_payload(payload.get("payload"))
    if plan is None:
        return None
    payload["sessionId"] = session_id
    payload["runId"] = run_id
    payload["seq"] = seq
    payload["type"] = payload.get("eventType")
    payload["plan"] = plan
    return payload


def serialize_plan_event_payload(
    session_id: str,
    run_id: str,
    payload: Dict[str, Any],
    seq: int,
) -> Optional[Dict[str, Any]]:
    plan = plan_payload_from_event_payload(payload.get("payload"))
    if plan is None:
        return None
    item = dict(payload)
    item["sessionId"] = session_id
    item["runId"] = run_id
    item["seq"] = seq
    item["type"] = item.get("type") or item.get("eventType")
    item["plan"] = plan
    return item


def latest_plan_payload(task_store: TaskStore, run_id: str, *, events_limit: int = EVENT_REPLAY_QUERY_LIMIT) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    latest_plan: Optional[Dict[str, Any]] = None
    latest_event_type: Optional[str] = None
    for event in task_store.list_events(run_id, limit=events_limit):
        if event.event_type not in PLAN_EVENT_TYPES:
            continue
        payload = serialize_event(event).get("payload")
        plan = plan_payload_from_event_payload(payload)
        if plan is None:
            continue
        latest_plan = dict(plan)
        latest_event_type = event.event_type
    return latest_plan, latest_event_type


def terminal_plan_payload(
    plan: Dict[str, Any],
    *,
    terminal_status: str,
    reason: str,
) -> Dict[str, Any]:
    payload = dict(plan)
    payload["planStatus"] = terminal_status
    payload["status"] = terminal_status
    payload["terminalReason"] = reason
    payload["eventType"] = f"plan_{terminal_status}"
    payload["command"] = terminal_status
    steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    statuses = list(payload.get("step_status") if isinstance(payload.get("step_status"), list) else [])
    statuses = [str(item or "not_started") for item in statuses]
    while len(statuses) < len(steps):
        statuses.append("not_started")
    statuses = statuses[: len(steps)]

    if terminal_status == "completed":
        payload["step_status"] = ["completed" for _ in steps]
        return payload

    if terminal_status in {"failed", "cancelled"} and steps:
        target_status = terminal_status
        if not any(status in {"running", "failed", "cancelled"} for status in statuses):
            for index, status in enumerate(statuses):
                if status != "completed":
                    statuses[index] = target_status
                    break
        else:
            statuses = [
                target_status if status == "running" else status
                for status in statuses
            ]
        payload["step_status"] = statuses
    return payload


def sync_plan_terminal_status(
    task_store: TaskStore,
    run_id: str,
    *,
    trace_id: str,
    terminal_status: str,
    reason: str,
    source: str,
    events_limit: int = EVENT_REPLAY_QUERY_LIMIT,
) -> None:
    if terminal_status not in {"completed", "failed", "cancelled"}:
        return
    latest_plan, latest_event_type = latest_plan_payload(task_store, run_id, events_limit=events_limit)
    if latest_plan is None:
        return
    target_event_type = f"plan_{terminal_status}"
    if latest_event_type == target_event_type:
        return
    terminal_plan = terminal_plan_payload(
        latest_plan,
        terminal_status=terminal_status,
        reason=reason,
    )
    task_store.add_event(
        run_id,
        target_event_type,
        {
            "messageType": target_event_type,
            "plan": terminal_plan,
            "reason": reason,
        },
        trace_id=trace_id,
        source=source,
    )


def pending_approval_from_event_payloads(
    session_id: str,
    run_id: str,
    events: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    approval_request_event = None
    approval_request_index = -1
    for index, event in enumerate(events):
        event_type = str(event.get("eventType") or event.get("type") or "")
        if event_type == "approval_requested":
            approval_request_event = event
            approval_request_index = index
    if approval_request_event is None:
        return None

    approval_request_id = approval_request_event.get("id")
    approval_request_event_id = (
        approval_request_event.get("eventId")
        or approval_request_event.get("event_id")
        or approval_request_event.get("approvalEventId")
    )
    for event in events[approval_request_index + 1 :]:
        event_type = str(event.get("eventType") or event.get("type") or "")
        if event_type != "approval_resolved":
            continue
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        resolved_request_id = payload.get("approvalRequestEventId")
        resolved_request_event_id = (
            payload.get("approvalRequestEventEventId")
            or payload.get("approvalEventId")
        )
        if approval_request_id is not None and resolved_request_id is not None:
            if str(resolved_request_id) == str(approval_request_id):
                return None
        if approval_request_event_id and resolved_request_event_id:
            if str(resolved_request_event_id) == str(approval_request_event_id):
                return None

    payload = dict(approval_request_event)
    payload["sessionId"] = session_id
    payload["runId"] = run_id
    payload["type"] = "approval_requested"
    payload["pending"] = True
    return payload


def pending_approval_payload(task_store: TaskStore, session_id: str, run_id: str, *, events_limit: int = EVENT_REPLAY_QUERY_LIMIT) -> Optional[Dict[str, Any]]:
    return pending_approval_from_event_payloads(
        session_id,
        run_id,
        [
            {
                **serialize_event(event),
                "sessionId": session_id,
                "runId": run_id,
                "type": event.event_type,
            }
            for event in task_store.list_events(run_id, limit=events_limit)
        ],
    )


def session_pending_approval_payload(
    session_store: SessionStore,
    task_store: TaskStore,
    session_id: str,
    run_id: str,
    *,
    events_limit: int = EVENT_REPLAY_QUERY_LIMIT,
) -> Optional[Dict[str, Any]]:
    return pending_approval_from_event_payloads(
        session_id,
        run_id,
        collect_session_run_event_payloads(
            session_store,
            task_store,
            session_id,
            run_id,
            events_limit=events_limit,
        ),
    )


def legacy_session_event_payloads(
    task_store: TaskStore,
    session_record: Any,
    *,
    events_limit: int = EVENT_REPLAY_QUERY_LIMIT,
) -> List[Dict[str, Any]]:
    session_id = session_record.session_id
    tasks = task_store.list_tasks(
        user_id=session_record.user_id,
        conversation_id=session_id,
        limit=events_limit,
    )
    events: List[Dict[str, Any]] = []
    for task in tasks:
        for event in task_store.list_events(task.task_id, limit=events_limit):
            payload = serialize_event(event)
            payload["sessionId"] = session_id
            payload["runId"] = payload.get("taskId")
            payload["type"] = session_event_type(payload)
            events.append(payload)
    events.sort(key=lambda item: (item.get("createdAt") or 0, item.get("id") or 0))
    return events


def collect_session_events(
    session_record: Any,
    *,
    event_type: Optional[str] = None,
    source: Optional[str] = None,
    after_seq: Optional[int] = None,
    limit: int = 500,
    offset: int = 0,
    events_limit: int = EVENT_REPLAY_QUERY_LIMIT,
) -> Dict[str, Any]:
    session_id = session_record.session_id
    session_store = SessionStore()
    task_store = TaskStore()
    run_event_records = session_store.list_run_events(session_id, limit=events_limit)
    if run_event_records:
        events = [serialize_run_event(event) for event in run_event_records]
        for item in events:
            item["type"] = session_event_type(item)
        seen_event_ids = {str(item.get("eventId") or "") for item in events if item.get("eventId")}
        next_seq = max([int(item.get("seq") or 0) for item in events] + [0])
        for item in legacy_session_event_payloads(task_store, session_record, events_limit=events_limit):
            if str(item.get("eventId") or "") in seen_event_ids:
                continue
            next_seq += 1
            item["seq"] = next_seq
            events.append(item)
        events.sort(key=lambda item: (int(item.get("seq") or 0), int(item.get("id") or 0)))
    else:
        events = legacy_session_event_payloads(task_store, session_record, events_limit=events_limit)
        for index, item in enumerate(events, start=1):
            item["seq"] = index

    event_type_filters = _csv_filter_values(event_type)
    source_filters = _csv_filter_values(source)
    latest_seq = max([int(item.get("seq") or 0) for item in events] + [0])
    if event_type_filters:
        events = [
            item
            for item in events
            if _matches_session_event_type(item, event_type_filters)
        ]
    if source_filters:
        events = [
            item
            for item in events
            if _matches_optional_filter(item.get("source"), source_filters)
        ]
    effective_after_seq = after_seq if isinstance(after_seq, int) else None
    if effective_after_seq is not None:
        events = [item for item in events if int(item.get("seq") or 0) > effective_after_seq]
    count = len(events)
    sliced = events[max(offset, 0) : max(offset, 0) + max(min(limit, events_limit), 1)]
    next_seq = max(
        [int(item.get("seq") or 0) for item in sliced]
        + [int(effective_after_seq or 0)]
    )
    return {
        "items": sliced,
        "sessionId": session_id,
        "eventType": event_type,
        "source": source,
        "afterSeq": effective_after_seq,
        "nextSeq": next_seq,
        "latestSeq": latest_seq,
        "count": count,
        "hasMore": offset + len(sliced) < count,
        "limit": limit,
        "offset": offset,
    }


def next_session_event_seq(
    session_store: SessionStore,
    task_store: TaskStore,
    session_id: str,
    user_id: Optional[str],
    *,
    events_limit: int = EVENT_REPLAY_QUERY_LIMIT,
) -> int:
    run_events = session_store.list_run_events(session_id, limit=events_limit)
    if run_events:
        seen_event_ids = {str(getattr(event, "event_id", "") or "") for event in run_events}
        next_seq = max([int(getattr(event, "seq", 0) or 0) for event in run_events] + [0])
        for item in legacy_session_event_payloads(
            task_store,
            SimpleNamespace(session_id=session_id, user_id=user_id or ""),
            events_limit=events_limit,
        ):
            if str(item.get("eventId") or "") in seen_event_ids:
                continue
            next_seq += 1
        return next_seq + 1

    runs = task_store.list_tasks(
        user_id=user_id,
        conversation_id=session_id,
        limit=200,
    )
    total_events = 0
    for run in runs:
        total_events += len(task_store.list_events(run.task_id, limit=events_limit))
    return total_events + 1


def artifact_download_response(artifact: Any) -> Any:
    if str(artifact.file_path).startswith(("http://", "https://")):
        return RedirectResponse(artifact.file_path)
    file_path = Path(artifact.file_path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="artifact file not found")
    return FileResponse(
        str(file_path),
        media_type=artifact.mime_type or "application/octet-stream",
        filename=artifact.filename,
    )
