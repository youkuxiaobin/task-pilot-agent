from __future__ import annotations

from typing import Any, Callable, Dict, List

from brain.core.tasks import (
    BACKGROUND_DISPATCH_DEFAULT_MAX_RECOVERY_ATTEMPTS,
    AgentTaskRecord,
    TaskStore,
    serialize_task,
)
from brain.models.requests import AgentMessage, GptQueryReq
from llm.types import RoleType


def task_request_from_record(
    task: AgentTaskRecord,
    *,
    deserialize_file_items: Callable[[Any], List[Any]],
) -> GptQueryReq:
    task_payload = serialize_task(task)
    metadata = task_payload.get("metadata") if isinstance(task_payload.get("metadata"), dict) else {}
    input_files = metadata.get("inputFiles") if isinstance(metadata.get("inputFiles"), list) else None
    return GptQueryReq(
        trace_id=task.task_id,
        user_id=task.user_id,
        agent_id=task.agent_id,
        conversation_id=task.conversation_id,
        session_message_id=metadata.get("sessionMessageId") if isinstance(metadata, dict) else None,
        outputStyle=task.output_style,
        mode=task.mode,
        selected_tools=metadata.get("selectedTools") if isinstance(metadata, dict) else None,
        approved_tools=metadata.get("approvedTools") if isinstance(metadata, dict) else None,
        run_environment=metadata.get("runEnvironment") if isinstance(metadata, dict) else None,
        language=metadata.get("language") if isinstance(metadata, dict) else None,
        messages=[
            AgentMessage(
                role=RoleType.USER.value,
                content=task.input_text or "",
                uploadFile=deserialize_file_items(input_files),
            )
        ],
    )


def recover_background_tasks(
    *,
    store: TaskStore,
    owner: str,
    start_background_run: Callable[[str, Any], Any],
    run_autoagent: Callable[[GptQueryReq, Callable[[str], None]], Any],
    deserialize_file_items: Callable[[Any], List[Any]],
    limit: int = 50,
    lease_ms: int = 5 * 60 * 1000,
    max_attempts: int = BACKGROUND_DISPATCH_DEFAULT_MAX_RECOVERY_ATTEMPTS,
) -> Dict[str, Any]:
    failed_tasks = store.fail_exhausted_background_recoveries(
        owner=owner,
        limit=limit,
        max_attempts=max_attempts,
    )
    failed: List[Dict[str, Any]] = []
    for task in failed_tasks:
        task_metadata = serialize_task(task).get("metadata")
        dispatch_metadata = (
            task_metadata.get("backgroundDispatch")
            if isinstance(task_metadata, dict)
            else {}
        )
        recovery_event = store.add_event(
            task.task_id,
            "task_recovery_failed",
            {
                "status": "failed",
                "owner": owner,
                "attempt": (
                    dispatch_metadata.get("attempt")
                    if isinstance(dispatch_metadata, dict)
                    else None
                ),
                "maxAttempts": max_attempts,
                "reason": task.error_message,
            },
            trace_id=task.trace_id,
            source="recovery",
        )
        failed.append(
            {
                "taskId": task.task_id,
                "traceId": task.trace_id,
                "eventId": recovery_event.id,
            }
        )

    claimed_tasks = store.claim_recoverable_background_tasks(
        owner=owner,
        limit=limit,
        lease_ms=lease_ms,
        max_attempts=max_attempts,
    )
    recovered: List[Dict[str, Any]] = []
    for task in claimed_tasks:
        request = task_request_from_record(task, deserialize_file_items=deserialize_file_items)
        task_metadata = serialize_task(task).get("metadata")
        dispatch_metadata = (
            task_metadata.get("backgroundDispatch")
            if isinstance(task_metadata, dict)
            else {}
        )
        previous_status = (
            dispatch_metadata.get("previousStatus")
            if isinstance(dispatch_metadata, dict)
            else task.status
        )
        recovery_event = store.add_event(
            task.task_id,
            "task_recovery_requested",
            {
                "status": "queued",
                "previousStatus": previous_status or task.status,
                "owner": owner,
                "leaseMs": lease_ms,
            },
            trace_id=task.trace_id,
            source="recovery",
        )
        start_background_run(task.task_id, run_autoagent(request, lambda _data: None))
        recovered.append(
            {
                "taskId": task.task_id,
                "traceId": task.trace_id,
                "eventId": recovery_event.id,
            }
        )
    return {
        "count": len(recovered),
        "items": recovered,
        "failedCount": len(failed),
        "failedItems": failed,
    }
