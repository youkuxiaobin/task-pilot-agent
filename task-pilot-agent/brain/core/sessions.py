from __future__ import annotations

import json
import mimetypes
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from brain.core.run_events import event_contract_fields
from brain.core.sanitization import sanitize_payload
from sqlalchemy import BigInteger, Column, Integer, String, Text, and_, or_
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from file.db_engine import get_engine

Base = declarative_base()

ID_TYPE = BigInteger().with_variant(Integer, "sqlite")
LONG_TEXT = Text().with_variant(mysql.LONGTEXT, "mysql")
RUN_EVENT_QUERY_LIMIT = 10000

_RUN_EVENT_SEQ_LOCKS: Dict[str, threading.Lock] = {}
_RUN_EVENT_SEQ_LOCKS_GUARD = threading.Lock()


def _run_event_seq_lock(session_id: str) -> threading.Lock:
    lock_key = session_id or "__empty_session__"
    with _RUN_EVENT_SEQ_LOCKS_GUARD:
        lock = _RUN_EVENT_SEQ_LOCKS.get(lock_key)
        if lock is None:
            lock = threading.Lock()
            _RUN_EVENT_SEQ_LOCKS[lock_key] = lock
        return lock


class AgentSessionStatus:
    IDLE = "idle"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_APPROVAL = "waiting_approval"
    ARCHIVED = "archived"


class AgentMessageRole:
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class AgentSessionRecord(Base):
    __tablename__ = "magent_session"

    id = Column(ID_TYPE, primary_key=True, autoincrement=True)
    session_id = Column(String(128), nullable=False, unique=True, index=True)
    user_id = Column(String(128), nullable=False, index=True, default="")
    title = Column(String(512), nullable=False, default="")
    agent_id = Column(String(128), nullable=False, index=True, default="")
    status = Column(String(32), nullable=False, index=True, default=AgentSessionStatus.IDLE)
    current_run_id = Column(String(128), nullable=True, index=True)
    last_message_id = Column(String(128), nullable=True)
    last_message_preview = Column(LONG_TEXT, nullable=True)
    pinned = Column(Integer, nullable=False, default=0)
    archived_at = Column(BigInteger, nullable=True)
    metadata_json = Column("metadata", LONG_TEXT, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class AgentMessageRecord(Base):
    __tablename__ = "magent_message"

    id = Column(ID_TYPE, primary_key=True, autoincrement=True)
    message_id = Column(String(128), nullable=False, unique=True, index=True)
    session_id = Column(String(128), nullable=False, index=True)
    run_id = Column(String(128), nullable=True, index=True)
    user_id = Column(String(128), nullable=False, index=True, default="")
    role = Column(String(32), nullable=False, index=True)
    content = Column(LONG_TEXT, nullable=True)
    content_format = Column(String(32), nullable=False, default="markdown")
    status = Column(String(32), nullable=False, default="created")
    metadata_json = Column("metadata", LONG_TEXT, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class AgentRunRecord(Base):
    __tablename__ = "magent_run"

    id = Column(ID_TYPE, primary_key=True, autoincrement=True)
    run_id = Column(String(128), nullable=False, unique=True, index=True)
    session_id = Column(String(128), nullable=False, index=True)
    user_id = Column(String(128), nullable=False, index=True, default="")
    user_message_id = Column(String(128), nullable=True, index=True)
    assistant_message_id = Column(String(128), nullable=True, index=True)
    trace_id = Column(String(128), nullable=False, index=True, default="")
    agent_id = Column(String(128), nullable=False, index=True, default="")
    mode = Column(String(64), nullable=False, index=True, default="")
    output_style = Column(String(64), nullable=False, default="")
    status = Column(String(32), nullable=False, index=True, default="queued")
    input_text = Column(LONG_TEXT, nullable=True)
    output_text = Column(LONG_TEXT, nullable=True)
    error_message = Column(LONG_TEXT, nullable=True)
    work_dir = Column(String(2048), nullable=True)
    metadata_json = Column("metadata", LONG_TEXT, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    started_at = Column(BigInteger, nullable=True)
    ended_at = Column(BigInteger, nullable=True)


class AgentRunEventRecord(Base):
    __tablename__ = "magent_run_event"

    id = Column(ID_TYPE, primary_key=True, autoincrement=True)
    event_id = Column(String(128), nullable=False, unique=True, index=True)
    session_id = Column(String(128), nullable=False, index=True)
    run_id = Column(String(128), nullable=False, index=True)
    user_id = Column(String(128), nullable=False, index=True, default="")
    seq = Column(Integer, nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    source = Column(String(64), nullable=False, default="")
    message_id = Column(String(128), nullable=True, index=True)
    payload_json = Column("payload", LONG_TEXT, nullable=False)
    created_at = Column(BigInteger, nullable=False)


class AgentArtifactRecord(Base):
    __tablename__ = "magent_artifact"

    id = Column(ID_TYPE, primary_key=True, autoincrement=True)
    artifact_id = Column(String(128), nullable=False, unique=True, index=True)
    session_id = Column(String(128), nullable=False, index=True)
    run_id = Column(String(128), nullable=False, index=True)
    message_id = Column(String(128), nullable=True, index=True)
    user_id = Column(String(128), nullable=False, index=True, default="")
    filename = Column(String(512), nullable=False)
    file_path = Column(String(2048), nullable=False)
    mime_type = Column(String(128), nullable=True)
    file_size = Column(BigInteger, nullable=False, default=0)
    description = Column(LONG_TEXT, nullable=True)
    metadata_json = Column("metadata", LONG_TEXT, nullable=True)
    created_at = Column(BigInteger, nullable=False)


def now_ms() -> int:
    return int(time.time() * 1000)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value: Optional[str], default: Any) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _detach_records(session: Session, records: Iterable[Any]) -> None:
    for record in records:
        session.expunge(record)


def _preview_text(value: str, limit: int = 160) -> str:
    text = " ".join((value or "").strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _title_from_content(value: str, fallback: str = "新会话") -> str:
    title = _preview_text(value, 40)
    return title or fallback


def _is_remote_artifact_path(value: str) -> bool:
    return str(value or "").startswith(("http://", "https://"))


def _artifact_filename(file_path: str, fallback: str = "artifact") -> str:
    if _is_remote_artifact_path(file_path):
        parsed_name = Path(urlparse(file_path).path).name
        return parsed_name or fallback
    return Path(file_path).name or fallback


def _normalize_status_filter(status: Optional[str]) -> Optional[str]:
    normalized = (status or "").strip().lower()
    if normalized in {"", "all", "active"}:
        return None
    return normalized


def _is_active_status_filter(status: Optional[str]) -> bool:
    return (status or "").strip().lower() == "active"


class SessionStore:
    def __init__(self) -> None:
        self._engine = get_engine()
        Base.metadata.create_all(self._engine)
        self._session_maker = sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            class_=Session,
        )

    def create_session(
        self,
        *,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        title: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentSessionRecord:
        resolved_session_id = session_id or str(uuid.uuid4())
        timestamp = now_ms()
        session = self._session_maker()
        try:
            existing = (
                session.query(AgentSessionRecord)
                .filter(AgentSessionRecord.session_id == resolved_session_id)
                .one_or_none()
            )
            if existing:
                changed = False
                if user_id and not existing.user_id:
                    existing.user_id = user_id
                    changed = True
                if agent_id and not existing.agent_id:
                    existing.agent_id = agent_id
                    changed = True
                if title and existing.title in {"", "新会话"}:
                    existing.title = title
                    changed = True
                if changed:
                    existing.updated_at = timestamp
                    session.commit()
                    session.refresh(existing)
                session.expunge(existing)
                return existing

            record = AgentSessionRecord(
                session_id=resolved_session_id,
                user_id=user_id or "",
                title=title or "新会话",
                agent_id=agent_id or "",
                status=AgentSessionStatus.IDLE,
                metadata_json=_json_dumps(sanitize_payload(metadata or {})),
                created_at=timestamp,
                updated_at=timestamp,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_session(self, session_id: str) -> Optional[AgentSessionRecord]:
        session = self._session_maker()
        try:
            record = (
                session.query(AgentSessionRecord)
                .filter(AgentSessionRecord.session_id == session_id)
                .one_or_none()
            )
            if record:
                session.expunge(record)
            return record
        finally:
            session.close()

    def list_sessions(
        self,
        *,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        keyword: Optional[str] = None,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> List[AgentSessionRecord]:
        session = self._session_maker()
        try:
            query = session.query(AgentSessionRecord)
            status_filter = _normalize_status_filter(status)
            active_filter = _is_active_status_filter(status)
            if user_id:
                query = query.filter(AgentSessionRecord.user_id == user_id)
            if status_filter:
                query = query.filter(AgentSessionRecord.status == status_filter)
            elif active_filter or not include_archived:
                query = query.filter(AgentSessionRecord.status != AgentSessionStatus.ARCHIVED)
            normalized_keyword = (keyword or "").strip()
            if normalized_keyword:
                pattern = f"%{normalized_keyword}%"
                query = query.filter(
                    or_(
                        AgentSessionRecord.title.like(pattern),
                        AgentSessionRecord.last_message_preview.like(pattern),
                    )
                )
            records = (
                query.order_by(AgentSessionRecord.updated_at.desc())
                .offset(max(offset, 0))
                .limit(max(min(limit, 200), 1))
                .all()
            )
            _detach_records(session, records)
            return records
        finally:
            session.close()

    def count_sessions(
        self,
        *,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        keyword: Optional[str] = None,
        include_archived: bool = False,
    ) -> int:
        session = self._session_maker()
        try:
            query = session.query(AgentSessionRecord)
            status_filter = _normalize_status_filter(status)
            active_filter = _is_active_status_filter(status)
            if user_id:
                query = query.filter(AgentSessionRecord.user_id == user_id)
            if status_filter:
                query = query.filter(AgentSessionRecord.status == status_filter)
            elif active_filter or not include_archived:
                query = query.filter(AgentSessionRecord.status != AgentSessionStatus.ARCHIVED)
            normalized_keyword = (keyword or "").strip()
            if normalized_keyword:
                pattern = f"%{normalized_keyword}%"
                query = query.filter(
                    or_(
                        AgentSessionRecord.title.like(pattern),
                        AgentSessionRecord.last_message_preview.like(pattern),
                    )
                )
            return int(query.count())
        finally:
            session.close()

    def update_session(
        self,
        session_id: str,
        *,
        title: Optional[str] = None,
        agent_id: Optional[str] = None,
        status: Optional[str] = None,
        current_run_id: Optional[str] = None,
        last_message_id: Optional[str] = None,
        last_message_preview: Optional[str] = None,
        pinned: Optional[bool] = None,
        archived: Optional[bool] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[AgentSessionRecord]:
        timestamp = now_ms()
        session = self._session_maker()
        try:
            record = (
                session.query(AgentSessionRecord)
                .filter(AgentSessionRecord.session_id == session_id)
                .one_or_none()
            )
            if not record:
                return None
            if title is not None:
                record.title = title or "新会话"
            if agent_id is not None:
                record.agent_id = agent_id or ""
            if status is not None:
                record.status = status
            if current_run_id is not None:
                record.current_run_id = current_run_id or None
            if last_message_id is not None:
                record.last_message_id = last_message_id or None
            if last_message_preview is not None:
                record.last_message_preview = _preview_text(last_message_preview)
            if pinned is not None:
                record.pinned = 1 if pinned else 0
            if archived is not None:
                record.status = AgentSessionStatus.ARCHIVED if archived else AgentSessionStatus.IDLE
                record.archived_at = timestamp if archived else None
            if metadata is not None:
                record.metadata_json = _json_dumps(sanitize_payload(metadata))
            record.updated_at = timestamp
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def archive_session(
        self,
        session_id: str,
        *,
        clear_current_run: bool = True,
        last_message_preview: Optional[str] = None,
    ) -> Optional[AgentSessionRecord]:
        return self.update_session(
            session_id,
            archived=True,
            current_run_id="" if clear_current_run else None,
            last_message_preview=last_message_preview,
        )

    def delete_session(self, session_id: str) -> Optional[AgentSessionRecord]:
        """Soft-delete a session by archiving it and keeping history intact."""
        return self.archive_session(session_id)

    def add_message(
        self,
        session_id: str,
        *,
        message_id: Optional[str] = None,
        run_id: Optional[str] = None,
        user_id: Optional[str] = None,
        role: str,
        content: Optional[str],
        content_format: str = "markdown",
        status: str = "created",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentMessageRecord:
        timestamp = now_ms()
        resolved_message_id = message_id or str(uuid.uuid4())
        normalized_role = (role or "").strip().lower() or AgentMessageRole.USER
        session = self._session_maker()
        try:
            parent = (
                session.query(AgentSessionRecord)
                .filter(AgentSessionRecord.session_id == session_id)
                .one_or_none()
            )
            if not parent:
                raise ValueError(f"session not found: {session_id}")
            existing = (
                session.query(AgentMessageRecord)
                .filter(AgentMessageRecord.message_id == resolved_message_id)
                .one_or_none()
            )
            if existing:
                session.expunge(existing)
                return existing
            record = AgentMessageRecord(
                message_id=resolved_message_id,
                session_id=session_id,
                run_id=run_id,
                user_id=user_id or parent.user_id,
                role=normalized_role,
                content=content,
                content_format=content_format or "markdown",
                status=status or "created",
                metadata_json=_json_dumps(sanitize_payload(metadata or {})),
                created_at=timestamp,
                updated_at=timestamp,
            )
            session.add(record)
            parent.last_message_id = resolved_message_id
            parent.last_message_preview = _preview_text(content or "")
            if normalized_role == AgentMessageRole.USER and parent.title in {"", "新会话"}:
                parent.title = _title_from_content(content or "")
            parent.updated_at = timestamp
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_messages(
        self,
        session_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
        before_message_id: Optional[str] = None,
    ) -> List[AgentMessageRecord]:
        session = self._session_maker()
        try:
            query = session.query(AgentMessageRecord).filter(AgentMessageRecord.session_id == session_id)
            if before_message_id:
                before_record = (
                    session.query(AgentMessageRecord)
                    .filter(
                        AgentMessageRecord.session_id == session_id,
                        AgentMessageRecord.message_id == before_message_id,
                    )
                    .one_or_none()
                )
                if not before_record:
                    return []
                query = query.filter(
                    or_(
                        AgentMessageRecord.created_at < before_record.created_at,
                        and_(
                            AgentMessageRecord.created_at == before_record.created_at,
                            AgentMessageRecord.id < before_record.id,
                        ),
                    )
                )
            resolved_limit = max(min(limit, 500), 1)
            resolved_offset = max(offset, 0)
            if before_message_id:
                records = (
                    query.order_by(AgentMessageRecord.created_at.desc(), AgentMessageRecord.id.desc())
                    .offset(resolved_offset)
                    .limit(resolved_limit)
                    .all()
                )
                records = list(reversed(records))
            else:
                records = (
                    query.order_by(AgentMessageRecord.created_at.asc(), AgentMessageRecord.id.asc())
                    .offset(resolved_offset)
                    .limit(resolved_limit)
                    .all()
                )
            _detach_records(session, records)
            return records
        finally:
            session.close()

    def count_messages(
        self,
        session_id: str,
        *,
        before_message_id: Optional[str] = None,
    ) -> int:
        session = self._session_maker()
        try:
            query = session.query(AgentMessageRecord).filter(AgentMessageRecord.session_id == session_id)
            if before_message_id:
                before_record = (
                    session.query(AgentMessageRecord)
                    .filter(
                        AgentMessageRecord.session_id == session_id,
                        AgentMessageRecord.message_id == before_message_id,
                    )
                    .one_or_none()
                )
                if not before_record:
                    return 0
                query = query.filter(
                    or_(
                        AgentMessageRecord.created_at < before_record.created_at,
                        and_(
                            AgentMessageRecord.created_at == before_record.created_at,
                            AgentMessageRecord.id < before_record.id,
                        ),
                    )
                )
            return int(query.count())
        finally:
            session.close()

    def create_run(
        self,
        *,
        run_id: str,
        session_id: str,
        user_id: Optional[str] = None,
        user_message_id: Optional[str] = None,
        assistant_message_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        mode: Optional[str] = None,
        output_style: Optional[str] = None,
        status: str = "queued",
        input_text: Optional[str] = None,
        output_text: Optional[str] = None,
        error_message: Optional[str] = None,
        work_dir: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentRunRecord:
        timestamp = now_ms()
        normalized_status = status or "queued"
        session = self._session_maker()
        try:
            parent = (
                session.query(AgentSessionRecord)
                .filter(AgentSessionRecord.session_id == session_id)
                .one_or_none()
            )
            if not parent:
                raise ValueError(f"session not found: {session_id}")
            existing = (
                session.query(AgentRunRecord)
                .filter(AgentRunRecord.run_id == run_id)
                .one_or_none()
            )
            if existing:
                if existing.session_id != session_id:
                    raise ValueError(f"run already belongs to another session: {run_id}")
                changed = False
                for field, value in {
                    "user_message_id": user_message_id,
                    "assistant_message_id": assistant_message_id,
                    "trace_id": trace_id,
                    "agent_id": agent_id,
                    "mode": mode,
                    "output_style": output_style,
                    "input_text": input_text,
                    "output_text": output_text,
                    "error_message": error_message,
                    "work_dir": work_dir,
                }.items():
                    if value is not None:
                        setattr(existing, field, value)
                        changed = True
                existing.status = normalized_status
                if normalized_status == "running" and not existing.started_at:
                    existing.started_at = timestamp
                if normalized_status in {"completed", "failed", "cancelled"}:
                    existing.ended_at = timestamp
                if metadata is not None:
                    existing.metadata_json = _json_dumps(sanitize_payload(metadata))
                    changed = True
                if changed or normalized_status:
                    existing.updated_at = timestamp
                    session.commit()
                    session.refresh(existing)
                session.expunge(existing)
                return existing

            record = AgentRunRecord(
                run_id=run_id,
                session_id=session_id,
                user_id=user_id or parent.user_id,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                trace_id=trace_id or run_id,
                agent_id=agent_id or parent.agent_id,
                mode=mode or "",
                output_style=output_style or "",
                status=normalized_status,
                input_text=input_text,
                output_text=output_text,
                error_message=error_message,
                work_dir=work_dir,
                metadata_json=_json_dumps(sanitize_payload(metadata or {})),
                created_at=timestamp,
                updated_at=timestamp,
                started_at=timestamp if normalized_status == "running" else None,
                ended_at=timestamp if normalized_status in {"completed", "failed", "cancelled"} else None,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_run(
        self,
        run_id: str,
        *,
        status: Optional[str] = None,
        assistant_message_id: Optional[str] = None,
        output_text: Optional[str] = None,
        error_message: Optional[str] = None,
        work_dir: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[AgentRunRecord]:
        timestamp = now_ms()
        session = self._session_maker()
        try:
            record = (
                session.query(AgentRunRecord)
                .filter(AgentRunRecord.run_id == run_id)
                .one_or_none()
            )
            if not record:
                return None
            if status is not None:
                record.status = status
                if status == "running" and not record.started_at:
                    record.started_at = timestamp
                if status in {"completed", "failed", "cancelled"}:
                    record.ended_at = timestamp
            if assistant_message_id is not None:
                record.assistant_message_id = assistant_message_id or None
            if output_text is not None:
                record.output_text = output_text
            if error_message is not None:
                record.error_message = error_message
            if work_dir is not None:
                record.work_dir = work_dir or None
            if metadata is not None:
                current_metadata = _json_loads(record.metadata_json, {})
                merged_metadata = dict(current_metadata if isinstance(current_metadata, dict) else {})
                merged_metadata.update(metadata)
                record.metadata_json = _json_dumps(sanitize_payload(merged_metadata))
            record.updated_at = timestamp
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_run(self, run_id: str) -> Optional[AgentRunRecord]:
        session = self._session_maker()
        try:
            record = (
                session.query(AgentRunRecord)
                .filter(AgentRunRecord.run_id == run_id)
                .one_or_none()
            )
            if record:
                session.expunge(record)
            return record
        finally:
            session.close()

    def list_runs(
        self,
        session_id: str,
        *,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[AgentRunRecord]:
        session = self._session_maker()
        try:
            query = session.query(AgentRunRecord).filter(AgentRunRecord.session_id == session_id)
            normalized_status = (status or "").strip()
            if normalized_status:
                query = query.filter(AgentRunRecord.status == normalized_status)
            records = (
                query.order_by(AgentRunRecord.created_at.desc(), AgentRunRecord.id.desc())
                .offset(max(offset, 0))
                .limit(max(min(limit, 2000), 1))
                .all()
            )
            _detach_records(session, records)
            return records
        finally:
            session.close()

    def count_runs(self, session_id: str, *, status: Optional[str] = None) -> int:
        session = self._session_maker()
        try:
            query = session.query(AgentRunRecord).filter(AgentRunRecord.session_id == session_id)
            normalized_status = (status or "").strip()
            if normalized_status:
                query = query.filter(AgentRunRecord.status == normalized_status)
            return int(query.count())
        finally:
            session.close()

    def add_run_event(
        self,
        *,
        session_id: str,
        run_id: str,
        event_type: str,
        payload: Any,
        user_id: Optional[str] = None,
        seq: Optional[int] = None,
        source: Optional[str] = None,
        message_id: Optional[str] = None,
        event_id: Optional[str] = None,
        created_at: Optional[int] = None,
    ) -> AgentRunEventRecord:
        timestamp = created_at or now_ms()
        with _run_event_seq_lock(session_id):
            session = self._session_maker()
            try:
                parent = (
                    session.query(AgentSessionRecord)
                    .filter(AgentSessionRecord.session_id == session_id)
                    .one_or_none()
                )
                if not parent:
                    raise ValueError(f"session not found: {session_id}")
                resolved_event_id = event_id or f"evt_{uuid.uuid4()}"
                existing = (
                    session.query(AgentRunEventRecord)
                    .filter(AgentRunEventRecord.event_id == resolved_event_id)
                    .one_or_none()
                )
                if existing:
                    session.expunge(existing)
                    return existing
                if seq is None:
                    latest = (
                        session.query(AgentRunEventRecord)
                        .filter(AgentRunEventRecord.session_id == session_id)
                        .order_by(AgentRunEventRecord.seq.desc(), AgentRunEventRecord.id.desc())
                        .first()
                    )
                    seq = int(latest.seq if latest else 0) + 1
                record = AgentRunEventRecord(
                    event_id=resolved_event_id,
                    session_id=session_id,
                    run_id=run_id,
                    user_id=user_id or parent.user_id,
                    seq=max(int(seq or 1), 1),
                    event_type=event_type,
                    source=source or "",
                    message_id=message_id,
                    payload_json=_json_dumps(sanitize_payload(payload)),
                    created_at=timestamp,
                )
                session.add(record)
                session.commit()
                session.refresh(record)
                session.expunge(record)
                return record
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def list_run_events(
        self,
        session_id: str,
        *,
        run_id: Optional[str] = None,
        after_seq: int = 0,
        event_type: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> List[AgentRunEventRecord]:
        session = self._session_maker()
        try:
            query = session.query(AgentRunEventRecord).filter(AgentRunEventRecord.session_id == session_id)
            if run_id:
                query = query.filter(AgentRunEventRecord.run_id == run_id)
            if after_seq:
                query = query.filter(AgentRunEventRecord.seq > max(int(after_seq), 0))
            event_types = [item.strip() for item in (event_type or "").split(",") if item.strip()]
            sources = [item.strip() for item in (source or "").split(",") if item.strip()]
            if event_types:
                query = query.filter(AgentRunEventRecord.event_type.in_(event_types))
            if sources:
                query = query.filter(AgentRunEventRecord.source.in_(sources))
            records = (
                query.order_by(AgentRunEventRecord.seq.asc(), AgentRunEventRecord.id.asc())
                .offset(max(offset, 0))
                .limit(max(min(limit, RUN_EVENT_QUERY_LIMIT), 1))
                .all()
            )
            _detach_records(session, records)
            return records
        finally:
            session.close()

    def count_run_events(
        self,
        session_id: str,
        *,
        run_id: Optional[str] = None,
        after_seq: int = 0,
        event_type: Optional[str] = None,
        source: Optional[str] = None,
    ) -> int:
        session = self._session_maker()
        try:
            query = session.query(AgentRunEventRecord).filter(AgentRunEventRecord.session_id == session_id)
            if run_id:
                query = query.filter(AgentRunEventRecord.run_id == run_id)
            if after_seq:
                query = query.filter(AgentRunEventRecord.seq > max(int(after_seq), 0))
            event_types = [item.strip() for item in (event_type or "").split(",") if item.strip()]
            sources = [item.strip() for item in (source or "").split(",") if item.strip()]
            if event_types:
                query = query.filter(AgentRunEventRecord.event_type.in_(event_types))
            if sources:
                query = query.filter(AgentRunEventRecord.source.in_(sources))
            return int(query.count())
        finally:
            session.close()

    def add_artifact(
        self,
        *,
        session_id: str,
        run_id: str,
        file_path: str,
        artifact_id: Optional[str] = None,
        user_id: Optional[str] = None,
        message_id: Optional[str] = None,
        filename: Optional[str] = None,
        description: Optional[str] = None,
        mime_type: Optional[str] = None,
        file_size: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        created_at: Optional[int] = None,
    ) -> AgentArtifactRecord:
        timestamp = created_at or now_ms()
        resolved_artifact_id = artifact_id or str(uuid.uuid4())
        normalized_file_path = (file_path or "").strip()
        if not normalized_file_path:
            raise ValueError("artifact file path is required")

        is_remote = _is_remote_artifact_path(normalized_file_path)
        if is_remote:
            resolved_path = normalized_file_path
            resolved_file_size = max(int(file_size or 0), 0)
        else:
            local_path = Path(normalized_file_path).expanduser().resolve()
            if not local_path.is_file():
                raise ValueError(f"artifact file not found: {local_path}")
            resolved_path = str(local_path)
            resolved_file_size = local_path.stat().st_size

        resolved_filename = filename or _artifact_filename(resolved_path)
        session = self._session_maker()
        try:
            parent = (
                session.query(AgentSessionRecord)
                .filter(AgentSessionRecord.session_id == session_id)
                .one_or_none()
            )
            if not parent:
                raise ValueError(f"session not found: {session_id}")
            existing = (
                session.query(AgentArtifactRecord)
                .filter(AgentArtifactRecord.artifact_id == resolved_artifact_id)
                .one_or_none()
            )
            if existing:
                if existing.session_id != session_id:
                    raise ValueError(f"artifact already belongs to another session: {resolved_artifact_id}")
                session.expunge(existing)
                return existing
            record = AgentArtifactRecord(
                artifact_id=resolved_artifact_id,
                session_id=session_id,
                run_id=run_id,
                message_id=message_id,
                user_id=user_id or parent.user_id,
                filename=resolved_filename,
                file_path=resolved_path,
                mime_type=mime_type or mimetypes.guess_type(resolved_filename)[0] or "application/octet-stream",
                file_size=resolved_file_size,
                description=description,
                metadata_json=_json_dumps(sanitize_payload(metadata or {})),
                created_at=timestamp,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_artifact(self, session_id: str, artifact_id: str) -> Optional[AgentArtifactRecord]:
        session = self._session_maker()
        try:
            record = (
                session.query(AgentArtifactRecord)
                .filter(
                    AgentArtifactRecord.session_id == session_id,
                    AgentArtifactRecord.artifact_id == artifact_id,
                )
                .one_or_none()
            )
            if record:
                session.expunge(record)
            return record
        finally:
            session.close()

    def list_artifacts(
        self,
        session_id: str,
        *,
        run_id: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> List[AgentArtifactRecord]:
        session = self._session_maker()
        try:
            query = session.query(AgentArtifactRecord).filter(AgentArtifactRecord.session_id == session_id)
            if run_id:
                query = query.filter(AgentArtifactRecord.run_id == run_id)
            records = (
                query.order_by(AgentArtifactRecord.created_at.asc(), AgentArtifactRecord.id.asc())
                .offset(max(offset, 0))
                .limit(max(min(limit, 2000), 1))
                .all()
            )
            _detach_records(session, records)
            return records
        finally:
            session.close()

    def count_artifacts(self, session_id: str, *, run_id: Optional[str] = None) -> int:
        session = self._session_maker()
        try:
            query = session.query(AgentArtifactRecord).filter(AgentArtifactRecord.session_id == session_id)
            if run_id:
                query = query.filter(AgentArtifactRecord.run_id == run_id)
            return int(query.count())
        finally:
            session.close()

    def delete_artifacts(
        self,
        session_id: str,
        *,
        run_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
    ) -> int:
        session = self._session_maker()
        try:
            query = session.query(AgentArtifactRecord).filter(AgentArtifactRecord.session_id == session_id)
            if run_id:
                query = query.filter(AgentArtifactRecord.run_id == run_id)
            if artifact_id:
                query = query.filter(AgentArtifactRecord.artifact_id == artifact_id)
            deleted = query.delete(synchronize_session=False)
            session.commit()
            return int(deleted or 0)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def serialize_session(record: AgentSessionRecord) -> Dict[str, Any]:
    metadata = _json_loads(record.metadata_json, {})
    return {
        "id": record.id,
        "session_id": record.session_id,
        "sessionId": record.session_id,
        "user_id": record.user_id,
        "userId": record.user_id,
        "title": record.title,
        "agent_id": record.agent_id,
        "agentId": record.agent_id,
        "status": record.status,
        "current_run_id": record.current_run_id,
        "currentRunId": record.current_run_id,
        "lastMessageId": record.last_message_id,
        "lastMessagePreview": record.last_message_preview,
        "pinned": bool(record.pinned),
        "archivedAt": record.archived_at,
        "metadata": metadata,
        "createdAt": record.created_at,
        "updatedAt": record.updated_at,
    }


def serialize_message(record: AgentMessageRecord) -> Dict[str, Any]:
    metadata = _json_loads(record.metadata_json, {})
    return {
        "id": record.id,
        "message_id": record.message_id,
        "messageId": record.message_id,
        "session_id": record.session_id,
        "sessionId": record.session_id,
        "run_id": record.run_id,
        "runId": record.run_id,
        "user_id": record.user_id,
        "userId": record.user_id,
        "role": record.role,
        "content": record.content,
        "contentFormat": record.content_format,
        "status": record.status,
        "metadata": metadata,
        "createdAt": record.created_at,
        "updatedAt": record.updated_at,
    }


def serialize_run(record: AgentRunRecord) -> Dict[str, Any]:
    metadata = _json_loads(record.metadata_json, {})
    return {
        "id": record.id,
        "run_id": record.run_id,
        "runId": record.run_id,
        "session_id": record.session_id,
        "sessionId": record.session_id,
        "user_id": record.user_id,
        "userId": record.user_id,
        "userMessageId": record.user_message_id,
        "assistantMessageId": record.assistant_message_id,
        "traceId": record.trace_id,
        "agent_id": record.agent_id,
        "agentId": record.agent_id,
        "mode": record.mode,
        "outputStyle": record.output_style,
        "status": record.status,
        "input": record.input_text,
        "output": record.output_text,
        "error": record.error_message,
        "errorMessage": record.error_message,
        "workDir": record.work_dir,
        "metadata": metadata,
        "createdAt": record.created_at,
        "updatedAt": record.updated_at,
        "startedAt": record.started_at,
        "endedAt": record.ended_at,
    }


def serialize_run_event(record: AgentRunEventRecord) -> Dict[str, Any]:
    payload = _json_loads(record.payload_json, {})
    return {
        "id": record.id,
        "event_id": record.event_id,
        "eventId": record.event_id,
        "session_id": record.session_id,
        "sessionId": record.session_id,
        "run_id": record.run_id,
        "runId": record.run_id,
        "user_id": record.user_id,
        "userId": record.user_id,
        "seq": record.seq,
        "eventType": record.event_type,
        "type": record.event_type,
        "source": record.source,
        "messageId": record.message_id,
        "payload": payload,
        "createdAt": record.created_at,
        **event_contract_fields(record.event_type),
    }


def serialize_agent_artifact(record: AgentArtifactRecord) -> Dict[str, Any]:
    is_remote = _is_remote_artifact_path(record.file_path)
    public_file_path = sanitize_payload(record.file_path) if is_remote else record.file_path
    return {
        "id": record.id,
        "artifactId": record.artifact_id,
        "session_id": record.session_id,
        "sessionId": record.session_id,
        "run_id": record.run_id,
        "runId": record.run_id,
        "taskId": record.run_id,
        "messageId": record.message_id,
        "user_id": record.user_id,
        "userId": record.user_id,
        "filename": record.filename,
        "filePath": public_file_path,
        "remoteUrl": public_file_path if is_remote else None,
        "isRemote": is_remote,
        "mimeType": record.mime_type,
        "fileSize": record.file_size,
        "description": record.description,
        "metadata": _json_loads(record.metadata_json, {}),
        "createdAt": record.created_at,
    }
