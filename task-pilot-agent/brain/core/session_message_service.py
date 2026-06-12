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
from brain.models.requests import AgentMessage, AgentSessionMessageReq, GptQueryReq
from llm.types import RoleType


@dataclass
class AgentSessionMessageDeps:
    default_agent_id: str
    ensure_session_owner: Callable[[Any, Any], None]
    current_user_id: Callable[[Any, Optional[str]], Optional[str]]
    serialize_file_items: Callable[[Any], List[Dict[str, Any]]]
    request_language: Callable[[Any], Optional[str]]
    request_agent_id: Callable[[Any], Optional[str]]
    request_selected_tools: Callable[[Any], Optional[List[str]]]
    request_approved_tools: Callable[[Any], Optional[List[str]]]
    request_run_environment: Callable[[Any], Optional[str]]
    request_output_style: Callable[[Any], Optional[str]]
    request_mode: Callable[[Any], Optional[str]]
    load_session_run_records: Callable[[SessionStore, TaskStore, Any, str, Any], tuple[Any, Any]]
    run_record_metadata: Callable[[Any], Dict[str, Any]]
    normalize_language: Callable[[Optional[str]], str]
    resume_session_run_after_input: Callable[..., Any]
    resume_task_after_input: Callable[..., Any]
    run_autoagent: Callable[[GptQueryReq, Callable[[str], None]], Any]


async def add_session_message(
    session_id: str,
    req: AgentSessionMessageReq,
    current_user: Any,
    deps: AgentSessionMessageDeps,
) -> Dict[str, Any]:
    store = SessionStore()
    session_record = store.get_session(session_id)
    if not session_record:
        raise HTTPException(status_code=404, detail="session not found")
    deps.ensure_session_owner(session_record, current_user)
    if session_record.status == AgentSessionStatus.ARCHIVED:
        raise HTTPException(status_code=409, detail="session is archived")
    if session_record.status == AgentSessionStatus.RUNNING:
        raise HTTPException(status_code=409, detail="session is busy")
    if session_record.status == AgentSessionStatus.WAITING_APPROVAL:
        raise HTTPException(status_code=409, detail="session is waiting for approval")

    content = (req.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")

    effective_user_id = deps.current_user_id(current_user, session_record.user_id) or ""
    input_files = req.files or []
    serialized_files = deps.serialize_file_items(input_files)
    req_language = deps.request_language(req)

    if session_record.status == AgentSessionStatus.WAITING_INPUT:
        return await _resume_waiting_session_message(
            session_id,
            session_record,
            content,
            serialized_files,
            effective_user_id,
            req_language,
            current_user,
            store,
            deps,
        )

    run_id = str(uuid.uuid4())
    user_message = store.add_message(
        session_id,
        run_id=run_id,
        user_id=effective_user_id,
        role=AgentMessageRole.USER,
        content=content,
        metadata={
            "source": "session_api",
            "inputFiles": serialized_files,
        },
    )
    agent_id = deps.request_agent_id(req) or session_record.agent_id or deps.default_agent_id
    selected_tools = deps.request_selected_tools(req)
    approved_tools = deps.request_approved_tools(req)
    run_environment = deps.request_run_environment(req)
    output_style = deps.request_output_style(req)
    mode = deps.request_mode(req)
    store.update_session(
        session_id,
        agent_id=agent_id,
        status=AgentSessionStatus.RUNNING,
        current_run_id=run_id,
        last_message_id=user_message.message_id,
        last_message_preview=content,
    )

    request = GptQueryReq(
        trace_id=run_id,
        user_id=effective_user_id,
        agent_id=agent_id,
        conversation_id=session_id,
        session_message_id=user_message.message_id,
        language=req_language,
        outputStyle=output_style,
        mode=mode,
        selected_tools=selected_tools,
        approved_tools=approved_tools,
        run_environment=run_environment,
        messages=[
            AgentMessage(
                role=RoleType.USER.value,
                content=content,
                uploadFile=input_files,
            )
        ],
    )
    asyncio.create_task(deps.run_autoagent(request, lambda _data: None))
    return {
        "sessionId": session_id,
        "messageId": user_message.message_id,
        "runId": run_id,
        "status": AgentSessionStatus.RUNNING,
        "message": serialize_message(user_message),
    }


async def _resume_waiting_session_message(
    session_id: str,
    session_record: Any,
    content: str,
    serialized_files: List[Dict[str, Any]],
    effective_user_id: str,
    req_language: Optional[str],
    current_user: Any,
    store: SessionStore,
    deps: AgentSessionMessageDeps,
) -> Dict[str, Any]:
    run_id = session_record.current_run_id or ""
    if not run_id:
        raise HTTPException(status_code=409, detail="session is waiting but has no active run")
    task_store = TaskStore()
    run_record, task = deps.load_session_run_records(
        store,
        task_store,
        session_record,
        run_id,
        current_user,
    )
    if task is None:
        if run_record is None:
            raise HTTPException(status_code=404, detail="run not found")
        return _resume_session_run_record(
            session_id,
            run_id,
            run_record,
            content,
            serialized_files,
            effective_user_id,
            req_language,
            store,
            deps,
        )

    return _resume_task_run(
        session_id,
        run_id,
        task,
        content,
        serialized_files,
        effective_user_id,
        req_language,
        store,
        task_store,
        deps,
    )


def _resume_session_run_record(
    session_id: str,
    run_id: str,
    run_record: Any,
    content: str,
    serialized_files: List[Dict[str, Any]],
    effective_user_id: str,
    req_language: Optional[str],
    store: SessionStore,
    deps: AgentSessionMessageDeps,
) -> Dict[str, Any]:
    metadata = deps.run_record_metadata(run_record)
    language = deps.normalize_language(req_language or (metadata.get("language") if metadata else None))
    user_message = store.add_message(
        session_id,
        run_id=run_id,
        user_id=effective_user_id,
        role=AgentMessageRole.USER,
        content=content,
        metadata={
            "source": "session_api_resume",
            "inputFiles": serialized_files,
        },
    )
    event = store.add_run_event(
        session_id=session_id,
        run_id=run_id,
        user_id=effective_user_id,
        event_type="user_input",
        source="session_api",
        message_id=user_message.message_id,
        payload={
            "content": content,
            "messageId": user_message.message_id,
            "inputFiles": serialized_files,
        },
    )
    store.add_run_event(
        session_id=session_id,
        run_id=run_id,
        user_id=effective_user_id,
        event_type="task_queued",
        source="session_api",
        payload={
            "status": AgentTaskStatus.QUEUED,
            "reason": "session_user_input_received",
            "language": language,
        },
    )
    store.add_run_event(
        session_id=session_id,
        run_id=run_id,
        user_id=effective_user_id,
        event_type="task_resume_requested",
        source="session_api",
        payload={
            "userInputEventId": event.id,
            "userInputEventEventId": event.event_id,
            "language": language,
        },
    )
    store.update_run(run_id, status=AgentTaskStatus.QUEUED)
    store.update_session(
        session_id,
        status=AgentSessionStatus.RUNNING,
        current_run_id=run_id,
        last_message_id=user_message.message_id,
        last_message_preview=content,
    )
    asyncio.create_task(
        deps.resume_session_run_after_input(
            run_record,
            content,
            language_override=language,
            session_message_id=user_message.message_id,
        )
    )
    return {
        "sessionId": session_id,
        "messageId": user_message.message_id,
        "runId": run_id,
        "status": AgentSessionStatus.RUNNING,
        "message": serialize_message(user_message),
        "event": serialize_run_event(event),
    }


def _resume_task_run(
    session_id: str,
    run_id: str,
    task: Any,
    content: str,
    serialized_files: List[Dict[str, Any]],
    effective_user_id: str,
    req_language: Optional[str],
    store: SessionStore,
    task_store: TaskStore,
    deps: AgentSessionMessageDeps,
) -> Dict[str, Any]:
    task_payload = serialize_task(task)
    task_metadata = task_payload.get("metadata") if isinstance(task_payload.get("metadata"), dict) else {}
    language = deps.normalize_language(req_language or (task_metadata.get("language") if task_metadata else None))
    user_message = store.add_message(
        session_id,
        run_id=run_id,
        user_id=effective_user_id,
        role=AgentMessageRole.USER,
        content=content,
        metadata={
            "source": "session_api_resume",
            "inputFiles": serialized_files,
        },
    )
    event = task_store.add_user_input(run_id, content, user_id=effective_user_id)
    task_store.add_event(
        run_id,
        "task_queued",
        {
            "status": AgentTaskStatus.QUEUED,
            "reason": "session_user_input_received",
            "language": language,
        },
        trace_id=task.trace_id,
        source="session_api",
    )
    task_store.add_event(
        run_id,
        "task_resume_requested",
        {"userInputEventId": event.id, "language": language},
        trace_id=task.trace_id,
        source="session_api",
    )
    store.update_session(
        session_id,
        status=AgentSessionStatus.RUNNING,
        current_run_id=run_id,
        last_message_id=user_message.message_id,
        last_message_preview=content,
    )
    asyncio.create_task(
        deps.resume_task_after_input(
            run_id,
            content,
            language_override=language,
            session_message_id=user_message.message_id,
        )
    )
    return {
        "sessionId": session_id,
        "messageId": user_message.message_id,
        "runId": run_id,
        "status": AgentSessionStatus.RUNNING,
        "message": serialize_message(user_message),
        "event": serialize_event(event),
    }
