from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException

from brain.core.sessions import (
    AgentMessageRole,
    AgentSessionStatus,
    SessionStore,
    serialize_message,
    serialize_run_event,
)
from brain.core.tasks import AgentTaskStatus, TaskStore, serialize_event, serialize_task
from brain.models.requests import AgentMessage, AgentRunApprovalReq, GptQueryReq
from llm.types import RoleType


@dataclass
class ApprovalServiceDeps:
    event_replay_query_limit: int
    default_agent_id: str
    load_owned_session: Callable[[str, Any], Any]
    load_session_run_records: Callable[[SessionStore, TaskStore, Any, str, Any], tuple[Any, Any]]
    run_record_metadata: Callable[[Any], Dict[str, Any]]
    run_record_retry_runtime: Callable[..., Dict[str, Any]]
    serialize_session_run_payload: Callable[..., Dict[str, Any]]
    normalize_tool_selection: Callable[[Any], Optional[List[str]]]
    merge_tool_selection: Callable[..., List[str]]
    normalize_run_environment: Callable[[Optional[str]], str]
    normalize_language: Callable[[Optional[str]], str]
    resolve_agent_config: Callable[[str], Any]
    deserialize_file_items: Callable[[Any], List[Any]]
    sync_session_run: Callable[..., None]
    run_autoagent: Callable[[GptQueryReq, Callable[[str], None]], Any]


def start_retry_from_session_run_record(
    session_store: SessionStore,
    session_record: Any,
    run_record: Any,
    deps: ApprovalServiceDeps,
    *,
    source: str,
    approved_tools_override: Optional[List[str]] = None,
    approval_type: Optional[str] = None,
    parent_event_type: Optional[str] = None,
) -> Dict[str, Any]:
    input_text = (getattr(run_record, "input_text", None) or "").strip()
    if not input_text:
        raise HTTPException(status_code=400, detail="run has no input to retry")

    retry_run_id = str(uuid.uuid4())
    runtime = deps.run_record_retry_runtime(run_record, approved_tools_override=approved_tools_override)
    user_id = getattr(run_record, "user_id", None) or getattr(session_record, "user_id", "") or ""
    agent_id = getattr(run_record, "agent_id", None) or getattr(session_record, "agent_id", "") or deps.default_agent_id
    mode = getattr(run_record, "mode", None) or ""
    output_style = getattr(run_record, "output_style", None) or ""

    message_metadata = {
        "source": source,
        "parentRunId": getattr(run_record, "run_id", ""),
        "inputFiles": runtime["inputFiles"],
        "approvedTools": runtime["approvedTools"],
    }
    if approval_type:
        message_metadata["approvalType"] = approval_type
    user_message = session_store.add_message(
        session_record.session_id,
        run_id=retry_run_id,
        user_id=user_id,
        role=AgentMessageRole.USER,
        content=input_text,
        metadata=message_metadata,
    )

    retry_metadata = {
        "source": source,
        "parentRunId": getattr(run_record, "run_id", ""),
        "parentTaskId": getattr(run_record, "run_id", ""),
        "agentSnapshot": runtime["agentSnapshot"],
        "selectedTools": runtime["selectedTools"],
        "approvedTools": runtime["approvedTools"],
        "runEnvironment": runtime["runEnvironment"],
        "language": runtime["language"],
        "inputFiles": runtime["inputFiles"],
        "sessionMessageId": user_message.message_id,
    }
    if approval_type:
        retry_metadata["approvalType"] = approval_type
    retry_run = session_store.create_run(
        run_id=retry_run_id,
        session_id=session_record.session_id,
        user_id=user_id,
        user_message_id=user_message.message_id,
        trace_id=retry_run_id,
        agent_id=agent_id,
        mode=mode,
        output_style=output_style,
        status=AgentTaskStatus.QUEUED,
        input_text=input_text,
        metadata=retry_metadata,
    )
    queued_event = session_store.add_run_event(
        session_id=session_record.session_id,
        run_id=retry_run_id,
        user_id=user_id,
        event_type="run_queued",
        source=source,
        message_id=user_message.message_id,
        payload={
            "status": AgentTaskStatus.QUEUED,
            "mode": mode,
            "outputStyle": output_style,
            "parentRunId": getattr(run_record, "run_id", ""),
            "agentConfigId": agent_id,
            "agentSnapshot": runtime["agentSnapshot"],
            "selectedTools": runtime["selectedTools"],
            "approvedTools": runtime["approvedTools"],
            "runEnvironment": runtime["runEnvironment"],
            "language": runtime["language"],
            "inputFiles": runtime["inputFiles"],
        },
    )
    parent_event = None
    if parent_event_type:
        parent_event = session_store.add_run_event(
            session_id=session_record.session_id,
            run_id=getattr(run_record, "run_id", ""),
            user_id=user_id,
            event_type=parent_event_type,
            source="api",
            payload={"retryRunId": retry_run_id},
        )
    session_store.update_session(
        session_record.session_id,
        status=AgentSessionStatus.RUNNING,
        current_run_id=retry_run_id,
        last_message_id=user_message.message_id,
        last_message_preview=input_text,
    )

    retry_req = GptQueryReq(
        trace_id=retry_run_id,
        user_id=user_id,
        agent_id=agent_id,
        conversation_id=session_record.session_id,
        session_message_id=user_message.message_id,
        outputStyle=output_style,
        mode=mode,
        selected_tools=runtime["selectedTools"],
        approved_tools=runtime["approvedTools"],
        run_environment=runtime["runEnvironment"],
        language=runtime["language"],
        messages=[
            AgentMessage(
                role=RoleType.USER.value,
                content=input_text,
                uploadFile=deps.deserialize_file_items(runtime["inputFiles"]),
            )
        ],
    )
    asyncio.create_task(deps.run_autoagent(retry_req, lambda _data: None))

    payload = deps.serialize_session_run_payload(session_record.session_id, run_record=retry_run)
    payload["message"] = serialize_message(user_message)
    payload["event"] = serialize_run_event(queued_event)
    if parent_event:
        payload["retryRequested"] = serialize_run_event(parent_event)
    return payload


async def resolve_session_run_record_approval(
    session_store: SessionStore,
    session_record: Any,
    run_record: Any,
    req: AgentRunApprovalReq,
    deps: ApprovalServiceDeps,
) -> Dict[str, Any]:
    run_id = str(getattr(run_record, "run_id", "") or "")
    all_events = session_store.list_run_events(
        session_record.session_id,
        run_id=run_id,
        limit=deps.event_replay_query_limit,
    )
    approval_events = [event for event in all_events if event.event_type == "approval_requested"]
    if not approval_events:
        raise HTTPException(status_code=409, detail="approval is not requested")
    approval_request_event = approval_events[-1]
    approval_request_payload = serialize_run_event(approval_request_event).get("payload") or {}

    for event in all_events:
        if event.event_type != "approval_resolved" or event.seq <= approval_request_event.seq:
            continue
        payload = serialize_run_event(event).get("payload") or {}
        if not isinstance(payload, dict):
            continue
        request_id = payload.get("approvalRequestEventId")
        request_event_id = payload.get("approvalRequestEventEventId")
        if request_id == approval_request_event.id or request_event_id == approval_request_event.event_id:
            raise HTTPException(status_code=409, detail="approval already resolved")

    requested_items = (
        approval_request_payload.get("requests")
        if isinstance(approval_request_payload, dict)
        else []
    )
    requested_tools = [
        str(item.get("tool") or "").strip()
        for item in (requested_items or [])
        if isinstance(item, dict) and str(item.get("tool") or "").strip()
    ]
    explicit_approved_tools = deps.normalize_tool_selection(
        req.approved_tools if req.approved_tools is not None else req.approvedTools
    )
    if req.approved and not explicit_approved_tools:
        explicit_approved_tools = requested_tools
    metadata = deps.run_record_metadata(run_record)
    selected_tools = deps.normalize_tool_selection(metadata.get("selectedTools"))
    approved_tools = deps.merge_tool_selection(metadata.get("approvedTools"), explicit_approved_tools)
    approval_type = req.approval_type or req.approvalType or "high_risk_tools"
    base_resolution_payload = {
        "approvalType": approval_type,
        "approvalRequestEventId": approval_request_event.id,
        "approvalRequestEventEventId": approval_request_event.event_id,
        "approved": bool(req.approved),
        "requestedTools": requested_tools,
        "approvedTools": approved_tools,
        "selectedTools": selected_tools,
        "reason": req.reason or "",
    }

    if not req.approved or not req.rerun:
        event = session_store.add_run_event(
            session_id=session_record.session_id,
            run_id=run_id,
            user_id=getattr(run_record, "user_id", None) or session_record.user_id,
            event_type="approval_resolved",
            source="user",
            payload=base_resolution_payload,
        )
        if getattr(run_record, "status", "") == AgentTaskStatus.WAITING_APPROVAL:
            terminal_status = AgentTaskStatus.CANCELLED if not req.approved else AgentTaskStatus.COMPLETED
            terminal_event_type = "run_cancelled" if not req.approved else "run_completed"
            terminal_reason = "approval_rejected" if not req.approved else "approval_resolved_without_rerun"
            session_store.update_run(
                run_id,
                status=terminal_status,
                error_message="approval rejected" if not req.approved else None,
                output_text="approval resolved without rerun" if req.approved else None,
            )
            session_store.add_run_event(
                session_id=session_record.session_id,
                run_id=run_id,
                user_id=getattr(run_record, "user_id", None) or session_record.user_id,
                event_type=terminal_event_type,
                source="approval",
                payload={
                    "status": terminal_status,
                    "reason": terminal_reason,
                    "approvalRequestEventId": approval_request_event.id,
                    "approvalResolutionEventId": event.id,
                },
            )
            if session_record.current_run_id == run_id:
                session_store.update_session(
                    session_record.session_id,
                    status=AgentSessionStatus.IDLE,
                    current_run_id="",
                    last_message_preview=(
                        req.reason or "approval rejected"
                        if not req.approved
                        else "approval resolved"
                    ),
                )
        return {
            "sessionId": session_record.session_id,
            "runId": run_id,
            "approved": bool(req.approved),
            "rerun": False,
            "event": serialize_run_event(event),
        }

    if not approved_tools:
        raise HTTPException(status_code=400, detail="approved_tools is required")
    if not (getattr(run_record, "input_text", None) or "").strip():
        raise HTTPException(status_code=400, detail="run has no input to rerun")
    active_run_id = str(session_record.current_run_id or "")
    if session_record.status in {AgentSessionStatus.RUNNING, AgentSessionStatus.WAITING_APPROVAL}:
        if active_run_id != run_id:
            raise HTTPException(status_code=409, detail="session is busy")
        if getattr(run_record, "status", "") not in {
            AgentTaskStatus.COMPLETED,
            AgentTaskStatus.FAILED,
            AgentTaskStatus.CANCELLED,
            AgentTaskStatus.WAITING_APPROVAL,
        }:
            raise HTTPException(status_code=409, detail="session is busy")

    payload = start_retry_from_session_run_record(
        session_store,
        session_record,
        run_record,
        deps,
        source="approval_retry",
        approved_tools_override=approved_tools,
        approval_type=approval_type,
    )
    retry_run_id = str(payload.get("runId") or "")
    resolution_event = session_store.add_run_event(
        session_id=session_record.session_id,
        run_id=run_id,
        user_id=getattr(run_record, "user_id", None) or session_record.user_id,
        event_type="approval_resolved",
        source="user",
        payload={
            **base_resolution_payload,
            "retryRunId": retry_run_id,
            "sessionMessageId": (payload.get("message") or {}).get("messageId"),
        },
    )
    if getattr(run_record, "status", "") == AgentTaskStatus.WAITING_APPROVAL:
        session_store.update_run(
            run_id,
            status=AgentTaskStatus.COMPLETED,
            output_text="approval resolved; retry started",
        )
        session_store.add_run_event(
            session_id=session_record.session_id,
            run_id=run_id,
            user_id=getattr(run_record, "user_id", None) or session_record.user_id,
            event_type="run_completed",
            source="approval",
            payload={
                "status": AgentTaskStatus.COMPLETED,
                "reason": "approval_retry_started",
                "retryRunId": retry_run_id,
                "approvalRequestEventId": approval_request_event.id,
                "approvalResolutionEventId": resolution_event.id,
            },
        )
    payload["approvalResolved"] = serialize_run_event(resolution_event)
    return payload


async def resolve_agent_session_run_approval(
    session_id: str,
    run_id: str,
    req: AgentRunApprovalReq,
    current_user: Any,
    deps: ApprovalServiceDeps,
) -> Dict[str, Any]:
    session_store = SessionStore()
    session_record = deps.load_owned_session(session_id, current_user)
    if session_record.status == AgentSessionStatus.ARCHIVED:
        raise HTTPException(status_code=409, detail="session is archived")

    task_store = TaskStore()
    run_record, task = deps.load_session_run_records(
        session_store,
        task_store,
        session_record,
        run_id,
        current_user,
    )
    if task is None:
        return await resolve_session_run_record_approval(
            session_store,
            session_record,
            run_record,
            req,
            deps,
        )
    return _resolve_task_approval(session_store, task_store, session_record, task, run_id, req, deps)


def _resolve_task_approval(
    session_store: SessionStore,
    task_store: TaskStore,
    session_record: Any,
    task: Any,
    run_id: str,
    req: AgentRunApprovalReq,
    deps: ApprovalServiceDeps,
) -> Dict[str, Any]:
    session_id = session_record.session_id
    all_events = task_store.list_events(run_id, limit=deps.event_replay_query_limit)
    approval_events = [event for event in all_events if event.event_type == "approval_requested"]
    if not approval_events:
        raise HTTPException(status_code=409, detail="approval is not requested")
    approval_request_event = approval_events[-1]
    already_resolved = [
        event
        for event in all_events
        if event.event_type == "approval_resolved" and event.id > approval_request_event.id
    ]
    if already_resolved:
        raise HTTPException(status_code=409, detail="approval already resolved")

    approval_request_payload = serialize_event(approval_request_event).get("payload") or {}
    requested_items = (
        approval_request_payload.get("requests")
        if isinstance(approval_request_payload, dict)
        else []
    )
    requested_tools = [
        str(item.get("tool") or "").strip()
        for item in (requested_items or [])
        if isinstance(item, dict) and str(item.get("tool") or "").strip()
    ]
    explicit_approved_tools = deps.normalize_tool_selection(
        req.approved_tools if req.approved_tools is not None else req.approvedTools
    )
    if req.approved and not explicit_approved_tools:
        explicit_approved_tools = requested_tools
    approval_type = req.approval_type or req.approvalType or "high_risk_tools"
    task_payload = serialize_task(task)
    metadata = task_payload.get("metadata") if isinstance(task_payload.get("metadata"), dict) else {}
    selected_tools = deps.normalize_tool_selection(metadata.get("selectedTools") if metadata else None)
    approved_tools = deps.merge_tool_selection(
        metadata.get("approvedTools") if metadata else None,
        explicit_approved_tools,
    )

    base_resolution_payload = {
        "approvalType": approval_type,
        "approvalRequestEventId": approval_request_event.id,
        "approved": bool(req.approved),
        "requestedTools": requested_tools,
        "approvedTools": approved_tools,
        "selectedTools": selected_tools,
        "reason": req.reason or "",
    }

    if not req.approved or not req.rerun:
        event = task_store.add_event(
            run_id,
            "approval_resolved",
            base_resolution_payload,
            trace_id=task.trace_id,
            source="user",
        )
        if task.status == AgentTaskStatus.WAITING_APPROVAL:
            terminal_status = AgentTaskStatus.CANCELLED if not req.approved else AgentTaskStatus.COMPLETED
            terminal_event_type = "task_cancelled" if not req.approved else "task_completed"
            terminal_reason = "approval_rejected" if not req.approved else "approval_resolved_without_rerun"
            task_store.update_status(
                run_id,
                terminal_status,
                error_message="approval rejected" if not req.approved else None,
                output_text="approval resolved without rerun" if req.approved else None,
            )
            deps.sync_session_run(
                session_store,
                run_id=run_id,
                status=terminal_status,
                error_message="approval rejected" if not req.approved else None,
                output_text="approval resolved without rerun" if req.approved else None,
            )
            task_store.add_event(
                run_id,
                terminal_event_type,
                {
                    "status": terminal_status,
                    "reason": terminal_reason,
                    "approvalRequestEventId": approval_request_event.id,
                    "approvalResolutionEventId": event.id,
                },
                trace_id=task.trace_id,
                source="approval",
            )
            if session_record.current_run_id == run_id:
                session_store.update_session(
                    session_id,
                    status=AgentSessionStatus.IDLE,
                    current_run_id="",
                    last_message_preview=(
                        req.reason or "approval rejected"
                        if not req.approved
                        else "approval resolved"
                    ),
                )
        return {
            "sessionId": session_id,
            "runId": run_id,
            "approved": bool(req.approved),
            "rerun": False,
            "event": serialize_event(event),
        }

    if not approved_tools:
        raise HTTPException(status_code=400, detail="approved_tools is required")
    if not task.input_text:
        raise HTTPException(status_code=400, detail="run has no input to rerun")
    active_run_id = str(session_record.current_run_id or "")
    if session_record.status in {AgentSessionStatus.RUNNING, AgentSessionStatus.WAITING_APPROVAL}:
        if active_run_id != run_id:
            raise HTTPException(status_code=409, detail="session is busy")
        if task.status not in {
            AgentTaskStatus.COMPLETED,
            AgentTaskStatus.FAILED,
            AgentTaskStatus.CANCELLED,
            AgentTaskStatus.WAITING_APPROVAL,
        }:
            raise HTTPException(status_code=409, detail="session is busy")

    retry_run_id = str(uuid.uuid4())
    run_environment = deps.normalize_run_environment(metadata.get("runEnvironment") if metadata else None)
    language = deps.normalize_language(metadata.get("language") if metadata else None)
    input_files = metadata.get("inputFiles") if metadata else None
    agent_config = deps.resolve_agent_config(task.agent_id)
    agent_snapshot = agent_config.to_runtime_snapshot(approved_tools=approved_tools) if agent_config else None
    user_message = session_store.add_message(
        session_id,
        run_id=retry_run_id,
        user_id=task.user_id,
        role=AgentMessageRole.USER,
        content=task.input_text,
        metadata={
            "source": "approval_retry",
            "parentRunId": run_id,
            "approvalType": approval_type,
            "approvedTools": approved_tools,
            "inputFiles": input_files,
        },
    )
    retry_task = task_store.create_task(
        task_id=retry_run_id,
        trace_id=retry_run_id,
        conversation_id=session_id,
        user_id=task.user_id,
        agent_id=task.agent_id,
        mode=task.mode,
        output_style=task.output_style,
        input_text=task.input_text,
        metadata={
            "source": "approval_retry",
            "parentTaskId": run_id,
            "agentSnapshot": agent_snapshot,
            "selectedTools": selected_tools,
            "approvedTools": approved_tools,
            "runEnvironment": run_environment,
            "language": language,
            "inputFiles": input_files,
            "sessionMessageId": user_message.message_id,
        },
    )
    task_store.add_event(
        retry_run_id,
        "task_queued",
        {
            "status": AgentTaskStatus.QUEUED,
            "mode": task.mode,
            "outputStyle": task.output_style,
            "parentTaskId": run_id,
            "approvalType": approval_type,
            "agentConfigId": task.agent_id,
            "agentSnapshot": agent_snapshot,
            "selectedTools": selected_tools,
            "approvedTools": approved_tools,
            "runEnvironment": run_environment,
            "language": language,
            "inputFiles": input_files,
        },
        trace_id=retry_run_id,
        source="approval",
    )
    resolution_event = task_store.add_event(
        run_id,
        "approval_resolved",
        {
            **base_resolution_payload,
            "retryRunId": retry_run_id,
            "sessionMessageId": user_message.message_id,
        },
        trace_id=task.trace_id,
        source="user",
    )
    if task.status == AgentTaskStatus.WAITING_APPROVAL:
        task_store.update_status(
            run_id,
            AgentTaskStatus.COMPLETED,
            output_text="approval resolved; retry started",
        )
        task_store.add_event(
            run_id,
            "task_completed",
            {
                "status": AgentTaskStatus.COMPLETED,
                "reason": "approval_retry_started",
                "retryRunId": retry_run_id,
                "approvalRequestEventId": approval_request_event.id,
                "approvalResolutionEventId": resolution_event.id,
            },
            trace_id=task.trace_id,
            source="approval",
        )
    session_store.update_session(
        session_id,
        status=AgentSessionStatus.RUNNING,
        current_run_id=retry_run_id,
        last_message_id=user_message.message_id,
        last_message_preview=task.input_text,
    )

    retry_req = GptQueryReq(
        trace_id=retry_run_id,
        user_id=task.user_id,
        agent_id=task.agent_id,
        conversation_id=session_id,
        session_message_id=user_message.message_id,
        outputStyle=task.output_style,
        mode=task.mode,
        selected_tools=selected_tools,
        approved_tools=approved_tools,
        run_environment=run_environment,
        language=language,
        messages=[
            AgentMessage(
                role=RoleType.USER.value,
                content=task.input_text,
                uploadFile=deps.deserialize_file_items(input_files),
            )
        ],
    )
    asyncio.create_task(deps.run_autoagent(retry_req, lambda _data: None))
    payload = serialize_task(retry_task)
    payload["sessionId"] = session_id
    payload["runId"] = retry_run_id
    payload["approvalResolved"] = serialize_event(resolution_event)
    payload["message"] = serialize_message(user_message)
    return payload
