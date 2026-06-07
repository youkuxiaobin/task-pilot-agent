from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from brain.core.sanitization import sanitize_payload
from config.config import agentSettings
from sqlalchemy import (
    BigInteger,
    Column,
    Integer,
    String,
    Text,
    and_,
    func,
    inspect as sa_inspect,
    or_,
    text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from file.db_engine import get_engine

logger = logging.getLogger(__name__)

Base = declarative_base()

ID_TYPE = BigInteger().with_variant(Integer, "sqlite")
LONG_TEXT = Text().with_variant(mysql.LONGTEXT, "mysql")
LEGACY_TASK_TABLE_RENAMES = (
    ("meta_agent_task", "magent_task"),
    ("meta_agent_task_event", "magent_task_event"),
    ("meta_agent_task_artifact", "magent_task_artifact"),
)


class AgentTaskStatus:
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


FINAL_STATUSES = {
    AgentTaskStatus.COMPLETED,
    AgentTaskStatus.FAILED,
    AgentTaskStatus.CANCELLED,
}

TASK_ID_SAFE_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")

class AgentTaskRecord(Base):
    __tablename__ = "magent_task"

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
    __tablename__ = "magent_task_event"

    id = Column(ID_TYPE, primary_key=True, autoincrement=True)
    task_id = Column(String(128), nullable=False, index=True)
    trace_id = Column(String(128), nullable=False, index=True, default="")
    event_type = Column(String(64), nullable=False, index=True)
    source = Column(String(64), nullable=False, default="")
    message_id = Column(String(128), nullable=True)
    payload_json = Column("payload", LONG_TEXT, nullable=False)
    created_at = Column(BigInteger, nullable=False)


class AgentTaskArtifactRecord(Base):
    __tablename__ = "magent_task_artifact"

    id = Column(ID_TYPE, primary_key=True, autoincrement=True)
    artifact_id = Column(String(128), nullable=False, unique=True, index=True)
    task_id = Column(String(128), nullable=False, index=True)
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


def _merge_usage_metrics(metadata: Dict[str, Any], increments: Dict[str, int]) -> Dict[str, Any]:
    usage = metadata.get("usage") if isinstance(metadata.get("usage"), dict) else {}
    merged_usage = dict(usage)
    for key, value in increments.items():
        try:
            increment = int(value)
        except (TypeError, ValueError):
            continue
        merged_usage[key] = int(merged_usage.get(key) or 0) + increment
    metadata["usage"] = merged_usage
    return metadata


def _detach_records(session: Session, records: Iterable[Any]) -> None:
    for record in records:
        session.expunge(record)


def safe_task_dir_name(task_id: str) -> str:
    sanitized = TASK_ID_SAFE_CHARS.sub("_", task_id).strip("._")
    return sanitized or str(uuid.uuid4())


def task_workspace_root() -> Path:
    explicit = os.getenv("APP_TASK_WORKSPACE_ROOT") or os.getenv("TASK_WORKSPACE_ROOT")
    if explicit:
        root = Path(explicit).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root
    return (agentSettings.core.upload_path / "tasks").resolve()


def prepare_task_workspace(task_id: str) -> Path:
    root = task_workspace_root()
    work_dir = (root / safe_task_dir_name(task_id)).resolve()
    if not work_dir.is_relative_to(root):
        raise ValueError("task workspace must stay inside workspace root")
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def migrate_legacy_task_tables(engine: Any) -> None:
    existing_tables = set(sa_inspect(engine).get_table_names())
    pending_renames = [
        (legacy_name, current_name)
        for legacy_name, current_name in LEGACY_TASK_TABLE_RENAMES
        if legacy_name in existing_tables and current_name not in existing_tables
    ]
    if not pending_renames:
        return

    preparer = engine.dialect.identifier_preparer
    with engine.begin() as connection:
        for legacy_name, current_name in pending_renames:
            quoted_legacy_name = preparer.quote(legacy_name)
            quoted_current_name = preparer.quote(current_name)
            if engine.dialect.name == "mysql":
                sql = f"RENAME TABLE {quoted_legacy_name} TO {quoted_current_name}"
            else:
                sql = f"ALTER TABLE {quoted_legacy_name} RENAME TO {quoted_current_name}"
            connection.execute(text(sql))


class TaskStore:
    def __init__(self) -> None:
        self._engine = get_engine()
        migrate_legacy_task_tables(self._engine)
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

            metadata_payload = sanitize_payload(metadata or {})
            if not isinstance(metadata_payload, dict):
                metadata_payload = {}
            metadata_payload["workDir"] = str(prepare_task_workspace(resolved_task_id))

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
                metadata_json=_json_dumps(metadata_payload),
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

    def _query_tasks(
        self,
        session: Session,
        *,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        status: Optional[str] = None,
        agent_id: Optional[str] = None,
        keyword: Optional[str] = None,
        created_from_ms: Optional[int] = None,
        created_to_ms: Optional[int] = None,
        min_duration_ms: Optional[int] = None,
        max_duration_ms: Optional[int] = None,
        has_error: Optional[bool] = None,
    ) -> Any:
        query = session.query(AgentTaskRecord)
        if user_id:
            query = query.filter(AgentTaskRecord.user_id == user_id)
        if conversation_id:
            query = query.filter(AgentTaskRecord.conversation_id == conversation_id)
        if status:
            query = query.filter(AgentTaskRecord.status == status)
        if agent_id:
            query = query.filter(AgentTaskRecord.agent_id == agent_id)
        if created_from_ms is not None:
            query = query.filter(AgentTaskRecord.created_at >= int(created_from_ms))
        if created_to_ms is not None:
            query = query.filter(AgentTaskRecord.created_at <= int(created_to_ms))
        duration_expr = func.coalesce(AgentTaskRecord.ended_at, AgentTaskRecord.updated_at) - func.coalesce(
            AgentTaskRecord.started_at,
            AgentTaskRecord.created_at,
        )
        if min_duration_ms is not None or max_duration_ms is not None:
            query = query.filter(AgentTaskRecord.started_at.isnot(None))
        if min_duration_ms is not None:
            query = query.filter(duration_expr >= int(min_duration_ms))
        if max_duration_ms is not None:
            query = query.filter(duration_expr <= int(max_duration_ms))
        error_filter = or_(
            AgentTaskRecord.status == AgentTaskStatus.FAILED,
            and_(AgentTaskRecord.error_message.isnot(None), AgentTaskRecord.error_message != ""),
        )
        if has_error is True:
            query = query.filter(error_filter)
        elif has_error is False:
            query = query.filter(
                AgentTaskRecord.status != AgentTaskStatus.FAILED,
                or_(AgentTaskRecord.error_message.is_(None), AgentTaskRecord.error_message == ""),
            )
        normalized_keyword = keyword.strip() if keyword else ""
        if normalized_keyword:
            pattern = f"%{normalized_keyword}%"
            query = query.filter(
                or_(
                    AgentTaskRecord.task_id.like(pattern),
                    AgentTaskRecord.input_text.like(pattern),
                    AgentTaskRecord.output_text.like(pattern),
                    AgentTaskRecord.error_message.like(pattern),
                )
            )
        return query

    def list_tasks(
        self,
        *,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        status: Optional[str] = None,
        agent_id: Optional[str] = None,
        keyword: Optional[str] = None,
        created_from_ms: Optional[int] = None,
        created_to_ms: Optional[int] = None,
        min_duration_ms: Optional[int] = None,
        max_duration_ms: Optional[int] = None,
        has_error: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[AgentTaskRecord]:
        session = self._session_maker()
        try:
            query = self._query_tasks(
                session,
                user_id=user_id,
                conversation_id=conversation_id,
                status=status,
                agent_id=agent_id,
                keyword=keyword,
                created_from_ms=created_from_ms,
                created_to_ms=created_to_ms,
                min_duration_ms=min_duration_ms,
                max_duration_ms=max_duration_ms,
                has_error=has_error,
            )
            records = (
                query.order_by(AgentTaskRecord.created_at.desc())
                .offset(max(offset, 0))
                .limit(max(min(limit, 2000), 1))
                .all()
            )
            _detach_records(session, records)
            return records
        finally:
            session.close()

    def count_tasks(
        self,
        *,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        status: Optional[str] = None,
        agent_id: Optional[str] = None,
        keyword: Optional[str] = None,
        created_from_ms: Optional[int] = None,
        created_to_ms: Optional[int] = None,
        min_duration_ms: Optional[int] = None,
        max_duration_ms: Optional[int] = None,
        has_error: Optional[bool] = None,
    ) -> int:
        session = self._session_maker()
        try:
            query = self._query_tasks(
                session,
                user_id=user_id,
                conversation_id=conversation_id,
                status=status,
                agent_id=agent_id,
                keyword=keyword,
                created_from_ms=created_from_ms,
                created_to_ms=created_to_ms,
                min_duration_ms=min_duration_ms,
                max_duration_ms=max_duration_ms,
                has_error=has_error,
            )
            return int(query.count())
        finally:
            session.close()

    def delete_task(self, task_id: str) -> bool:
        session = self._session_maker()
        work_dir: Optional[Path] = None
        conversation_id = ""
        try:
            record = (
                session.query(AgentTaskRecord)
                .filter(AgentTaskRecord.task_id == task_id)
                .one_or_none()
            )
            if not record:
                return False

            work_dir = _task_work_dir_from_record(record)
            conversation_id = record.conversation_id or ""
            session.query(AgentTaskArtifactRecord).filter(AgentTaskArtifactRecord.task_id == task_id).delete(
                synchronize_session=False
            )
            session.query(AgentTaskEventRecord).filter(AgentTaskEventRecord.task_id == task_id).delete(
                synchronize_session=False
            )
            session.delete(record)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        if work_dir:
            root = task_workspace_root()
            resolved_work_dir = work_dir.expanduser().resolve()
            if resolved_work_dir.is_relative_to(root) and resolved_work_dir.is_dir():
                shutil.rmtree(resolved_work_dir, ignore_errors=True)
        self._delete_mirrored_run_artifacts(conversation_id, task_id)
        return True

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

    def request_user_input(
        self,
        task_id: str,
        prompt: str,
        *,
        trace_id: Optional[str] = None,
        source: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentTaskEventRecord:
        task = self.update_status(task_id, AgentTaskStatus.WAITING_INPUT)
        if not task:
            raise ValueError(f"task not found: {task_id}")
        return self.add_event(
            task_id,
            "waiting_input",
            {
                "prompt": prompt,
                "metadata": metadata or {},
                "status": AgentTaskStatus.WAITING_INPUT,
            },
            trace_id=trace_id or task.trace_id,
            source=source or "agent",
        )

    def add_user_input(
        self,
        task_id: str,
        content: str,
        *,
        user_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> AgentTaskEventRecord:
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"task not found: {task_id}")
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("user input is required")
        event = self.add_event(
            task_id,
            "user_input",
            {
                "content": normalized_content,
                "userId": user_id or task.user_id,
            },
            trace_id=trace_id or task.trace_id,
            source="user",
        )
        if task.status == AgentTaskStatus.WAITING_INPUT:
            self.update_status(task_id, AgentTaskStatus.QUEUED)
        return event

    def increment_usage_metrics(self, task_id: str, increments: Dict[str, int]) -> Optional[AgentTaskRecord]:
        if not increments:
            return self.get_task(task_id)
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
            metadata = _json_loads(record.metadata_json, {})
            if not isinstance(metadata, dict):
                metadata = {}
            record.metadata_json = _json_dumps(sanitize_payload(_merge_usage_metrics(metadata, increments)))
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
        task_record: Optional[AgentTaskRecord] = None
        try:
            task_record = (
                session.query(AgentTaskRecord)
                .filter(AgentTaskRecord.task_id == task_id)
                .one_or_none()
            )
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
            if task_record:
                session.expunge(task_record)
            self._mirror_run_event(record, task_record, payload)
            return record
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _mirror_run_event(
        self,
        event_record: AgentTaskEventRecord,
        task_record: Optional[AgentTaskRecord],
        raw_payload: Any,
    ) -> None:
        if not task_record or not task_record.conversation_id:
            return
        try:
            from brain.core.sessions import SessionStore

            payload = sanitize_payload(raw_payload)
            SessionStore().add_run_event(
                session_id=task_record.conversation_id,
                run_id=task_record.task_id,
                user_id=task_record.user_id,
                event_id=f"evt_{event_record.id}",
                event_type=event_record.event_type,
                source=event_record.source,
                message_id=event_record.message_id,
                payload=payload,
                created_at=event_record.created_at,
            )
        except Exception:
            logger.debug("failed to mirror task event %s into run event table", event_record.id, exc_info=True)

    def list_events(
        self,
        task_id: str,
        *,
        event_type: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> List[AgentTaskEventRecord]:
        session = self._session_maker()
        try:
            query = session.query(AgentTaskEventRecord).filter(AgentTaskEventRecord.task_id == task_id)
            event_types = [item.strip() for item in (event_type or "").split(",") if item.strip()]
            sources = [item.strip() for item in (source or "").split(",") if item.strip()]
            if event_types:
                query = query.filter(AgentTaskEventRecord.event_type.in_(event_types))
            if sources:
                query = query.filter(AgentTaskEventRecord.source.in_(sources))
            records = (
                query.order_by(AgentTaskEventRecord.id.asc())
                .offset(max(offset, 0))
                .limit(max(min(limit, 10000), 1))
                .all()
            )
            _detach_records(session, records)
            return records
        finally:
            session.close()

    def add_artifact(
        self,
        task_id: str,
        file_path: str,
        *,
        artifact_id: Optional[str] = None,
        filename: Optional[str] = None,
        description: Optional[str] = None,
        mime_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentTaskArtifactRecord:
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"task not found: {task_id}")

        resolved_path = Path(file_path).expanduser().resolve()
        work_dir = _task_work_dir_from_record(task)
        if not resolved_path.is_relative_to(work_dir):
            raise ValueError("artifact path must stay inside task workspace")
        if not resolved_path.is_file():
            raise ValueError(f"artifact file not found: {resolved_path}")

        resolved_artifact_id = artifact_id or str(uuid.uuid4())
        timestamp = now_ms()
        session = self._session_maker()
        try:
            record = AgentTaskArtifactRecord(
                artifact_id=resolved_artifact_id,
                task_id=task_id,
                filename=filename or resolved_path.name,
                file_path=str(resolved_path),
                mime_type=mime_type or mimetypes.guess_type(str(resolved_path))[0] or "application/octet-stream",
                file_size=resolved_path.stat().st_size,
                description=description,
                metadata_json=_json_dumps(sanitize_payload(metadata or {})),
                created_at=timestamp,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            session.expunge(record)
            self._mirror_run_artifact(record, task)
            return record
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def add_remote_artifact(
        self,
        task_id: str,
        remote_url: str,
        *,
        artifact_id: Optional[str] = None,
        filename: Optional[str] = None,
        description: Optional[str] = None,
        mime_type: Optional[str] = None,
        file_size: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentTaskArtifactRecord:
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"task not found: {task_id}")

        normalized_url = (remote_url or "").strip()
        if not normalized_url.startswith(("http://", "https://")):
            raise ValueError("remote artifact url must be http or https")

        parsed_name = Path(urlparse(normalized_url).path).name
        resolved_filename = filename or parsed_name or artifact_id or "artifact"
        resolved_artifact_id = artifact_id or str(uuid.uuid4())
        timestamp = now_ms()
        session = self._session_maker()
        try:
            record = AgentTaskArtifactRecord(
                artifact_id=resolved_artifact_id,
                task_id=task_id,
                filename=resolved_filename,
                file_path=normalized_url,
                mime_type=mime_type or mimetypes.guess_type(resolved_filename)[0] or "application/octet-stream",
                file_size=max(int(file_size or 0), 0),
                description=description,
                metadata_json=_json_dumps(sanitize_payload(metadata or {})),
                created_at=timestamp,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            session.expunge(record)
            self._mirror_run_artifact(record, task)
            return record
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_artifact(self, task_id: str, artifact_id: str) -> Optional[AgentTaskArtifactRecord]:
        session = self._session_maker()
        try:
            record = (
                session.query(AgentTaskArtifactRecord)
                .filter(
                    AgentTaskArtifactRecord.task_id == task_id,
                    AgentTaskArtifactRecord.artifact_id == artifact_id,
                )
                .one_or_none()
            )
            if record:
                session.expunge(record)
            return record
        finally:
            session.close()

    def list_artifacts(self, task_id: str) -> List[AgentTaskArtifactRecord]:
        session = self._session_maker()
        try:
            records = (
                session.query(AgentTaskArtifactRecord)
                .filter(AgentTaskArtifactRecord.task_id == task_id)
                .order_by(AgentTaskArtifactRecord.created_at.asc())
                .all()
            )
            _detach_records(session, records)
            return records
        finally:
            session.close()

    def _mirror_run_artifact(
        self,
        artifact_record: AgentTaskArtifactRecord,
        task_record: Optional[AgentTaskRecord],
    ) -> None:
        if not task_record or not task_record.conversation_id:
            return
        try:
            from brain.core.sessions import SessionStore

            SessionStore().add_artifact(
                session_id=task_record.conversation_id,
                run_id=task_record.task_id,
                user_id=task_record.user_id,
                artifact_id=artifact_record.artifact_id,
                file_path=artifact_record.file_path,
                filename=artifact_record.filename,
                description=artifact_record.description,
                mime_type=artifact_record.mime_type,
                file_size=artifact_record.file_size,
                metadata=_json_loads(artifact_record.metadata_json, {}),
                created_at=artifact_record.created_at,
            )
        except Exception:
            logger.debug(
                "failed to mirror task artifact %s into session artifact table",
                artifact_record.artifact_id,
                exc_info=True,
            )

    def _delete_mirrored_run_artifacts(self, session_id: str, run_id: str) -> None:
        if not session_id:
            return
        try:
            from brain.core.sessions import SessionStore

            SessionStore().delete_artifacts(session_id, run_id=run_id)
        except Exception:
            logger.debug("failed to delete mirrored session artifacts for run %s", run_id, exc_info=True)


def _task_work_dir_from_record(record: AgentTaskRecord) -> Path:
    expected_work_dir = prepare_task_workspace(record.task_id)
    metadata = _json_loads(record.metadata_json, {})
    work_dir = metadata.get("workDir") if isinstance(metadata, dict) else None
    if work_dir:
        resolved = Path(work_dir).expanduser().resolve()
        if resolved == expected_work_dir:
            return resolved
    return expected_work_dir


def serialize_task(record: AgentTaskRecord) -> Dict[str, Any]:
    metadata = _json_loads(record.metadata_json, {})
    usage = metadata.get("usage") if isinstance(metadata, dict) and isinstance(metadata.get("usage"), dict) else {}
    duration_ms = None
    if record.started_at is not None:
        duration_ms = (record.ended_at or record.updated_at or record.started_at) - record.started_at
    elif record.ended_at is not None:
        duration_ms = record.ended_at - record.created_at
    return {
        "id": record.id,
        "task_id": record.task_id,
        "taskId": record.task_id,
        "run_id": record.task_id,
        "runId": record.task_id,
        "trace_id": record.trace_id,
        "traceId": record.trace_id,
        "conversation_id": record.conversation_id,
        "conversationId": record.conversation_id,
        "session_id": record.conversation_id,
        "sessionId": record.conversation_id,
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
        "metadata": metadata,
        "usage": usage,
        "workDir": metadata.get("workDir") if isinstance(metadata, dict) else None,
        "createdAt": record.created_at,
        "updatedAt": record.updated_at,
        "startedAt": record.started_at,
        "endedAt": record.ended_at,
        "durationMs": duration_ms,
        "hasError": bool(record.status == AgentTaskStatus.FAILED or record.error_message),
    }


def serialize_event(record: AgentTaskEventRecord) -> Dict[str, Any]:
    event_id = f"evt_{record.id}"
    return {
        "id": record.id,
        "event_id": event_id,
        "eventId": event_id,
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


def serialize_artifact(record: AgentTaskArtifactRecord) -> Dict[str, Any]:
    is_remote = str(record.file_path).startswith(("http://", "https://"))
    public_file_path = sanitize_payload(record.file_path) if is_remote else record.file_path
    return {
        "id": record.id,
        "artifactId": record.artifact_id,
        "taskId": record.task_id,
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
