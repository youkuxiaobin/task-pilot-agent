from __future__ import annotations

import asyncio
import mimetypes
import os
from typing import Iterable, List, Optional, Sequence, Tuple, Union
from config.config import agentSettings
from fastapi import UploadFile
import aiofiles
from sqlalchemy import (
	BigInteger,
	Column,
	DateTime,
	Integer,
	LargeBinary,
	String,
	Text,
	inspect,
	text,
	func,
)
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.orm import Session, declarative_base, defer

from tools.mcp_local.util.log_util import timer

from .db_engine import SessionLocal, get_engine

DataT = Union[str, bytes, bytearray, memoryview]

Base = declarative_base()
class _FileDB(object):
    def __init__(self):
        self._work_dir = agentSettings.core.upload_path
        if not os.path.exists(self._work_dir):
            os.makedirs(self._work_dir)

    async def save(self, file_name, content, scope) -> str:
        if "." in file_name:
            file_name = os.path.basename(file_name)
        else:
            file_name = f"{file_name}.txt"

        save_path = os.path.join(self._work_dir, scope)
        if not os.path.exists(save_path):
            os.makedirs(save_path)
        async with aiofiles.open(f"{save_path}/{file_name}", "wb") as f:
            if isinstance(content, str):
                await f.write(content.encode("utf-8"))
            else:
                await f.write(content)
        return f"{save_path}/{file_name}"
    
    async def save_by_data(self, file: UploadFile) -> str:
        file_name = file.filename
        file_data = await file.read()
        save_path = os.path.join(self._work_dir, file_name)
        async with aiofiles.open(save_path, "wb") as f:
            await f.write(file_data)
        return save_path


FileDB = _FileDB()

class FileInfo(Base):
	__tablename__ = "meta_agent_file"

	id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
	file_id = Column(String(128), nullable=False, unique=True, index=True)
	request_id = Column(String(128), nullable=False, index=True, default="")
	user_id = Column(String(128), nullable=False, index=True, default="")
	task_id = Column(String(128), nullable=False, index=True, default="")
	conversation_id = Column(String(128), nullable=False, index=True, default="")
	filename = Column(String(512), nullable=False)
	description = Column(Text, nullable=True)
	mime_type = Column(String(128), nullable=True)
	encoding = Column(String(64), nullable=True)
	content = Column(
		LargeBinary().with_variant(mysql.LONGBLOB, "mysql").with_variant(
			postgresql.BYTEA, "postgresql"
		),
		nullable=False,
	)
	file_size = Column(BigInteger, nullable=False)
	file_path = Column(String(1024), nullable=False)
	status = Column(Integer, nullable=False, default=1)
	created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
	updated_at = Column(
		DateTime(timezone=True),
		nullable=False,
		server_default=func.now(),
		onupdate=func.now(),
	)


Base.metadata.create_all(get_engine())


def _ensure_file_owner_columns() -> None:
	engine = get_engine()
	existing = {column["name"] for column in inspect(engine).get_columns("meta_agent_file")}
	with engine.begin() as conn:
		for column_name in ("user_id", "task_id", "conversation_id"):
			if column_name not in existing:
				conn.execute(text(f"ALTER TABLE meta_agent_file ADD COLUMN {column_name} VARCHAR(128) NOT NULL DEFAULT ''"))


_ensure_file_owner_columns()


def _ensure_bytes(content: DataT, encoding: Optional[str] = None) -> Tuple[bytes, Optional[str]]:
	if isinstance(content, bytes):
		return content, encoding
	if isinstance(content, bytearray):
		return bytes(content), encoding
	if isinstance(content, memoryview):
		return content.tobytes(), encoding
	if isinstance(content, str):
		use_encoding = encoding or "utf-8"
		return content.encode(use_encoding), use_encoding
	raise TypeError(f"Unsupported content type: {type(content)!r}")


def _guess_mime_type(filename: str) -> Optional[str]:
	mime, _ = mimetypes.guess_type(filename)
	return mime or "application/octet-stream"


class FileStorageManager:
	def __init__(self):
		self._session_maker = SessionLocal

	@staticmethod
	def _detach_records(session: Session, records: Iterable[FileInfo]) -> None:
		for record in records:
			session.expunge(record)

	def add_or_update(
		self,
		file_id: str,
		filename: str,
		file_path: str,
		request_id: Optional[str],
		content: DataT,
		description: Optional[str] = "",
		mime_type: Optional[str] = None,
		encoding: Optional[str] = None,
		status: int = 1,
		user_id: Optional[str] = None,
		task_id: Optional[str] = None,
		conversation_id: Optional[str] = None,
	) -> FileInfo:
		content_bytes, encoding_used = _ensure_bytes(content, encoding)
		file_size = len(content_bytes)
		normalized_request_id = request_id or ""
		session = self._session_maker()
		try:
			record = (
				session.query(FileInfo)
				.filter(FileInfo.file_id == file_id)
				.one_or_none()
			)
			if record:
				record.filename = filename
				record.request_id = normalized_request_id
				record.user_id = user_id or record.user_id or ""
				record.task_id = task_id or record.task_id or ""
				record.conversation_id = conversation_id or record.conversation_id or ""
				record.description = description or ""
				record.content = content_bytes
				record.file_size = file_size
				record.mime_type = mime_type
				record.encoding = encoding_used
				record.status = status
				record.file_path = file_path
			else:
				record = FileInfo(
					file_id=file_id,
					request_id=normalized_request_id,
					user_id=user_id or "",
					task_id=task_id or "",
					conversation_id=conversation_id or "",
					filename=filename,
					file_path= file_path,
					description=description or "",
					content=content_bytes,
					file_size=file_size,
					mime_type=mime_type,
					encoding=encoding_used,
					status=status,
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

	def get_by_file_id(self, file_id: str, include_content: bool = True) -> Optional[FileInfo]:
		session = self._session_maker()
		try:
			query = session.query(FileInfo).filter(FileInfo.file_id == file_id, FileInfo.status == 1)
			if not include_content:
				query = query.options(defer(FileInfo.content))
			result = query.one_or_none()
			if result:
				session.expunge(result)
			return result
		finally:
			session.close()

	def get_by_file_ids(
		self, file_ids: Sequence[str], include_content: bool = False
	) -> List[FileInfo]:
		if not file_ids:
			return []
		session = self._session_maker()
		try:
			query = (
				session.query(FileInfo)
				.filter(FileInfo.file_id.in_(file_ids), FileInfo.status == 1)
				.order_by(FileInfo.id.asc())
			)
			if not include_content:
				query = query.options(defer(FileInfo.content))
			results = query.all()
			self._detach_records(session, results)
			return results
		finally:
			session.close()

	def get_by_request_id(
		self, request_id: str, include_content: bool = False
	) -> List[FileInfo]:
		session = self._session_maker()
		try:
			query = (
				session.query(FileInfo)
				.filter(FileInfo.request_id == request_id, FileInfo.status == 1)
				.order_by(FileInfo.id.asc())
			)
			if not include_content:
				query = query.options(defer(FileInfo.content))
			results = query.all()
			self._detach_records(session, results)
			return results
		finally:
			session.close()


class FileInfoOp:
	_storage_manager = FileStorageManager()

	@classmethod
	@timer()
	async def add_by_content(
		cls,
		filename: str,
		content: DataT,
		file_id: str,
		description: Optional[str] = None,
		request_id: Optional[str] = None,
		mime_type: Optional[str] = None,
		encoding: Optional[str] = None,
		user_id: Optional[str] = None,
		task_id: Optional[str] = None,
		conversation_id: Optional[str] = None,
	) -> FileInfo:
		file_path = await FileDB.save(filename, content, scope=request_id)
		if mime_type is None:
			mime_type = _guess_mime_type(filename)
		return await asyncio.to_thread(
			cls._storage_manager.add_or_update,
			file_id,
			filename,
			file_path,
			request_id,
			content,
			description or "",
			mime_type,
			encoding,
			1,
			user_id,
			task_id,
			conversation_id,
		)

	@classmethod
	@timer()
	async def add_by_file(
		cls,
		file: UploadFile,
		file_id: str,
		request_id: Optional[str] = None,
		description: Optional[str] = "",
		user_id: Optional[str] = None,
		task_id: Optional[str] = None,
		conversation_id: Optional[str] = None,
	) -> FileInfo:
		data = await file.read()
		return await cls.add_by_content(
			filename=file.filename,
			content=data,
			file_id=file_id,
			description=description or "",
			request_id=request_id,
			mime_type=file.content_type,
			user_id=user_id,
			task_id=task_id,
			conversation_id=conversation_id,
		)

	@classmethod
	@timer()
	async def get_by_file_id(cls, file_id: str) -> Optional[FileInfo]:
		return await asyncio.to_thread(cls._storage_manager.get_by_file_id, file_id, True)

	@classmethod
	@timer()
	async def get_by_file_ids(cls, file_ids: Sequence[str]) -> List[FileInfo]:
		return await asyncio.to_thread(cls._storage_manager.get_by_file_ids, file_ids, False)

	@classmethod
	@timer()
	async def get_by_request_id(cls, request_id: str) -> List[FileInfo]:
		return await asyncio.to_thread(cls._storage_manager.get_by_request_id, request_id, False)


def get_file_preview_url(request_id: str, file_name: str) -> str:
	base_url = f"http://{agentSettings.server.host}:{agentSettings.server.port}"
	return f"{base_url}/file/v1/preview_file/{request_id}/{file_name}"


def get_file_download_url(request_id: str, file_name: str) -> str:
	base_url = f"http://{agentSettings.server.host}:{agentSettings.server.port}"
	return f"{base_url}/file/v1/download_file/{request_id}/{file_name}"
