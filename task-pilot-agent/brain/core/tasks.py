from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional

from brain.core.sanitization import sanitize_payload
from sqlalchemy import BigInteger, Column, Integer, String, Text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from file.db_engine import get_engine

Base = declarative_base()

ID_TYPE = BigInteger().with_variant(Integer, "sqlite")
LONG_TEXT = Text().with_variant(mysql.LONGTEXT, "mysql")


class AgentTaskStatus:
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


FINAL_STATUSES = {
    AgentTaskStatus.COMPLETED,
    AgentTaskStatus.FAILED,
    AgentTaskStatus.CANCELLED,
}

class AgentTaskRecord(Base):
    __tablename__ = "meta_agent_task"

    id = Column(ID_TYPE, primary_key=True, autoincrement=True)
    task_id = Column(String(128), nullable=False, unique=True, index=True)
    trace_id = Column(String(128), nullable=False, index=True)
    conversation_id = Column(String(128), nullable=False, index=True, default="")
    user_id = Column(String(128), nullable=False, index=True, default="")
    agent_id = Column(String(128), nullable=False, index=True, default="")
    mode = Column(String(64), nullable=False, index=True, default="")
    output_style = Column(String(64), nullable=False, default="")
    status = Column(String(32), nullable=False, index=True, default=AgentTaskStatus.QUEUED)
    input_text = Column(LONG_TEXT, nullable=True)
    output_text = Column(LONG_TEXT, nullable=True)
    error_message = Column(LONG_TEXT, nullable=True)
    metadata_json = Column("metadata", LONG_TEXT, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    started_at = Column(BigInteger, nullable=True)
    ended_at = Column(BigInteger, nullable=True)


class AgentTaskEventRecord(Base):
    __tablename__ = "meta_agent_task_event"

    id = Column(ID_TYPE, primary_key=True, autoincrement=True)
    task_id = Column(String(128), nullable=False, index=True)
    trace_id = Column(String(128), nullable=False, index=True, default="")
    event_type = Column(String(64), nullable=False, index=True)
    source = Column(String(64), nullable=False, default="")
    message_id = Column(String(128), nullable=True)
    payload_json = Column("payload", LONG_TEXT, nullable=False)
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


class TaskStore:
    def __init__(self) -> None:
        self._engine = get_engine()
        Base.metadata.create_all(self._engine)
        self._session_maker = sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            class_=Session,
        )

    def create_task(
        self,
        *,
        task_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        mode: Optional[str] = None,
        output_style: Optional[str] = None,
        input_text: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentTaskRecord:
        resolved_task_id = task_id or str(uuid.uuid4())
        resolved_trace_id = trace_id or resolved_task_id
        timestamp = now_ms()
        session = self._session_maker()
        try:
            existing = (
                session.query(AgentTaskRecord)
                .filter(AgentTaskRecord.task_id == resolved_task_id)
                .one_or_none()
            )
            if existing:
                session.expunge(existing)
                return existing

            record = AgentTaskRecord(
                task_id=resolved_task_id,
                trace_id=resolved_trace_id,
                conversation_id=conversation_id or "",
                user_id=user_id or "",
                agent_id=agent_id or "",
                mode=mode or "",
                output_style=output_style or "",
                status=AgentTaskStatus.QUEUED,
                input_text=input_text,
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

    def get_task(self, task_id: str) -> Optional[AgentTaskRecord]:
        session = self._session_maker()
        try:
            record = (
                session.query(AgentTaskRecord)
                .filter(AgentTaskRecord.task_id == task_id)
                .one_or_none()
            )
            if record:
                session.expunge(record)
            return record
        finally:
            session.close()

    def list_tasks(
        self,
        *,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[AgentTaskRecord]:
        session = self._session_maker()
        try:
            query = session.query(AgentTaskRecord)
            if user_id:
                query = query.filter(AgentTaskRecord.user_id == user_id)
            if status:
                query = query.filter(AgentTaskRecord.status == status)
            if agent_id:
                query = query.filter(AgentTaskRecord.agent_id == agent_id)
            records = (
                query.order_by(AgentTaskRecord.created_at.desc())
                .offset(max(offset, 0))
                .limit(max(min(limit, 200), 1))
                .all()
            )
            _detach_records(session, records)
            return records
        finally:
            session.close()

    def update_status(
        self,
        task_id: str,
        status: str,
        *,
        output_text: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Optional[AgentTaskRecord]:
        timestamp = now_ms()
        session = self._session_maker()
        try:
            record = (
                session.query(AgentTaskRecord)
                .filter(AgentTaskRecord.task_id == task_id)
                .one_or_none()
            )
            if not record:
                return None

            record.status = status
            record.updated_at = timestamp
            if status == AgentTaskStatus.RUNNING and record.started_at is None:
                record.started_at = timestamp
            if status in FINAL_STATUSES:
                record.ended_at = timestamp
            if output_text is not None:
                record.output_text = output_text
            if error_message is not None:
                record.error_message = error_message
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def add_event(
        self,
        task_id: str,
        event_type: str,
        payload: Any,
        *,
        trace_id: Optional[str] = None,
        source: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> AgentTaskEventRecord:
        timestamp = now_ms()
        session = self._session_maker()
        try:
            record = AgentTaskEventRecord(
                task_id=task_id,
                trace_id=trace_id or "",
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

    def list_events(self, task_id: str, *, limit: int = 500, offset: int = 0) -> List[AgentTaskEventRecord]:
        session = self._session_maker()
        try:
            records = (
                session.query(AgentTaskEventRecord)
                .filter(AgentTaskEventRecord.task_id == task_id)
                .order_by(AgentTaskEventRecord.id.asc())
                .offset(max(offset, 0))
                .limit(max(min(limit, 2000), 1))
                .all()
            )
            _detach_records(session, records)
            return records
        finally:
            session.close()


def serialize_task(record: AgentTaskRecord) -> Dict[str, Any]:
    return {
        "id": record.id,
        "task_id": record.task_id,
        "taskId": record.task_id,
        "trace_id": record.trace_id,
        "traceId": record.trace_id,
        "conversation_id": record.conversation_id,
        "conversationId": record.conversation_id,
        "user_id": record.user_id,
        "userId": record.user_id,
        "agent_id": record.agent_id,
        "agentId": record.agent_id,
        "mode": record.mode,
        "outputStyle": record.output_style,
        "status": record.status,
        "input": record.input_text,
        "output": record.output_text,
        "error": record.error_message,
        "metadata": _json_loads(record.metadata_json, {}),
        "createdAt": record.created_at,
        "updatedAt": record.updated_at,
        "startedAt": record.started_at,
        "endedAt": record.ended_at,
    }


def serialize_event(record: AgentTaskEventRecord) -> Dict[str, Any]:
    return {
        "id": record.id,
        "task_id": record.task_id,
        "taskId": record.task_id,
        "trace_id": record.trace_id,
        "traceId": record.trace_id,
        "eventType": record.event_type,
        "source": record.source,
        "messageId": record.message_id,
        "payload": _json_loads(record.payload_json, {}),
        "createdAt": record.created_at,
    }
