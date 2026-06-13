from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from brain.core.context import FileItem
from brain.core.context_budget import (
    DEFAULT_MESSAGE_CONTEXT_MAX_CHARS,
    MAX_MESSAGE_CONTEXT_MAX_CHARS,
    fit_messages_to_char_budget,
    truncate_context_text,
)
from brain.core.sessions import AgentMessageRole, SessionStore, serialize_message, serialize_session
from brain.models.requests import AgentMessage
from llm.types import RoleType


DEFAULT_SESSION_CONTEXT_HISTORY_LIMIT = 20
MAX_SESSION_CONTEXT_HISTORY_LIMIT = 50
DEFAULT_SESSION_CONTEXT_MAX_CHARS = DEFAULT_MESSAGE_CONTEXT_MAX_CHARS
MAX_SESSION_CONTEXT_MAX_CHARS = MAX_MESSAGE_CONTEXT_MAX_CHARS
DEFAULT_SESSION_SUMMARY_TRIGGER_MESSAGE_COUNT = 30
DEFAULT_SESSION_SUMMARY_RECENT_MESSAGE_COUNT = 12
DEFAULT_SESSION_SUMMARY_MAX_MESSAGES = 200
DEFAULT_SESSION_SUMMARY_MAX_CHARS = 3000


def deserialize_context_file_items(files: Any) -> List[FileItem]:
    if not isinstance(files, list):
        return []
    restored: List[FileItem] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        restored.append(
            FileItem(
                fileName=str(item.get("fileName") or ""),
                description=item.get("description"),
                ossUrl=item.get("ossUrl"),
                domainUrl=item.get("domainUrl"),
                fileSize=item.get("fileSize"),
                isInternalFile=bool(item.get("isInternalFile") or False),
            )
        )
    return [item for item in restored if item.fileName]


def agent_message_from_session_message(record: Any) -> AgentMessage:
    payload = serialize_message(record)
    role = str(payload.get("role") or RoleType.USER.value).strip().lower()
    if role not in {
        RoleType.SYSTEM.value,
        RoleType.USER.value,
        RoleType.ASSISTANT.value,
        RoleType.TOOL.value,
    }:
        role = RoleType.USER.value
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    input_files = metadata.get("inputFiles") if isinstance(metadata, dict) else None
    files = deserialize_context_file_items(input_files)
    return AgentMessage(
        role=role,
        content=str(payload.get("content") or ""),
        uploadFile=files or None,
    )


def session_summary_text(session_record: Optional[Any]) -> str:
    if not session_record:
        return ""
    payload = serialize_session(session_record)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    summary = metadata.get("summary") if isinstance(metadata, dict) else None
    if isinstance(summary, dict):
        return str(summary.get("text") or "").strip()
    if isinstance(summary, str):
        return summary.strip()
    return ""


def session_summary_message(summary_text: str) -> AgentMessage:
    return AgentMessage(
        role=RoleType.SYSTEM.value,
        content=(
            "会话摘要（较早内容，供延续上下文使用）：\n"
            f"{summary_text.strip()}"
        ),
    )


def session_message_summary_line(record: Any) -> str:
    role = str(getattr(record, "role", "") or "").strip().lower()
    role_label = {
        AgentMessageRole.USER: "用户",
        AgentMessageRole.ASSISTANT: "助手",
        AgentMessageRole.SYSTEM: "系统",
        AgentMessageRole.TOOL: "工具",
    }.get(role, role or "消息")
    content = " ".join(str(getattr(record, "content", "") or "").split())
    if not content:
        return ""
    return f"{role_label}: {truncate_context_text(content, 220)}"


def compose_session_summary(
    records: List[Any],
    *,
    existing_summary: str = "",
    max_chars: int = DEFAULT_SESSION_SUMMARY_MAX_CHARS,
) -> str:
    lines: List[str] = []
    if existing_summary:
        lines.append(truncate_context_text(existing_summary, max(max_chars // 3, 400)))
    for record in records:
        line = session_message_summary_line(record)
        if line:
            lines.append(line)
    summary = "\n".join(lines).strip()
    return truncate_context_text(summary, max_chars) if summary else ""


def merge_session_metadata(session_record: Any, patch: Dict[str, Any]) -> Dict[str, Any]:
    payload = serialize_session(session_record)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    merged = dict(metadata or {})
    merged.update(patch)
    return merged


def maybe_update_session_summary(
    session_store: Optional[SessionStore],
    session_id: Optional[str],
    *,
    task_store: Optional[Any] = None,
    task_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    trigger_message_count: int = DEFAULT_SESSION_SUMMARY_TRIGGER_MESSAGE_COUNT,
    recent_message_count: int = DEFAULT_SESSION_SUMMARY_RECENT_MESSAGE_COUNT,
    max_messages: int = DEFAULT_SESSION_SUMMARY_MAX_MESSAGES,
    max_chars: int = DEFAULT_SESSION_SUMMARY_MAX_CHARS,
    now_ms: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    if not session_store or not session_id:
        return None
    session_record = session_store.get_session(session_id)
    if not session_record:
        return None
    messages = session_store.list_messages(session_id, limit=max_messages)
    if len(messages) <= trigger_message_count:
        return None
    recent_count = max(min(recent_message_count, len(messages)), 1)
    summarized_records = messages[:-recent_count]
    if not summarized_records:
        return None
    existing_summary = session_summary_text(session_record)
    summary_text = compose_session_summary(
        summarized_records,
        existing_summary=existing_summary,
        max_chars=max_chars,
    )
    if not summary_text:
        return None
    summary_payload = {
        "text": summary_text,
        "messageCount": len(messages),
        "summarizedMessageCount": len(summarized_records),
        "recentMessageCount": recent_count,
        "lastMessageId": messages[-1].message_id,
        "updatedAt": now_ms if now_ms is not None else int(time.time() * 1000),
        "strategy": "deterministic_recent_window",
    }
    updated_metadata = merge_session_metadata(session_record, {"summary": summary_payload})
    session_store.update_session(session_id, metadata=updated_metadata)
    if task_store is not None and task_id:
        task_store.add_event(
            task_id,
            "session_summary_updated",
            summary_payload,
            trace_id=trace_id or task_id,
            source="memory",
        )
    return summary_payload


def build_session_model_messages(
    session_store: SessionStore,
    session_id: Optional[str],
    current_messages: List[AgentMessage],
    current_message_id: Optional[str],
    *,
    history_limit: Optional[int] = None,
    max_history_limit: int = MAX_SESSION_CONTEXT_HISTORY_LIMIT,
    max_context_chars: Optional[int] = DEFAULT_SESSION_CONTEXT_MAX_CHARS,
    logger: Optional[Any] = None,
) -> List[AgentMessage]:
    current = list(current_messages or [])
    if len(current) != 1 or not session_id or not current_message_id:
        return current
    try:
        session_record = session_store.get_session(session_id)
        history_records = session_store.list_messages(
            session_id,
            limit=500,
            before_message_id=current_message_id,
        )
    except Exception:
        if logger is not None:
            logger.exception("failed to load session history for %s", session_id)
        return current

    effective_limit = max(
        min(
            history_limit if history_limit is not None else DEFAULT_SESSION_CONTEXT_HISTORY_LIMIT,
            max_history_limit,
        ),
        0,
    )
    selected_records = history_records[-effective_limit:] if effective_limit else []
    history: List[AgentMessage] = []
    summary_text = session_summary_text(session_record)
    if summary_text:
        history.append(session_summary_message(summary_text))
    for record in selected_records:
        history.append(agent_message_from_session_message(record))
    requested_max_context_chars = (
        DEFAULT_SESSION_CONTEXT_MAX_CHARS
        if max_context_chars is None
        else max_context_chars
    )
    effective_max_context_chars = max(min(int(requested_max_context_chars), MAX_SESSION_CONTEXT_MAX_CHARS), 0)
    return fit_messages_to_char_budget(
        history + current,
        max_chars=effective_max_context_chars,
    )
