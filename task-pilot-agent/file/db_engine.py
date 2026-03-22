from __future__ import annotations

import os
from functools import lru_cache
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from config.config import agentSettings


def _resolve_dsn() -> str:
	return os.getenv("FILE_DB_URL") or agentSettings.db.dsn


def _engine_kwargs() -> dict:
	db_cfg = agentSettings.db
	return {
		"future": True,
		"pool_pre_ping": getattr(db_cfg, "pool_pre_ping", True),
		"pool_recycle": getattr(db_cfg, "pool_recycle", 1800),
	}


@lru_cache(maxsize=1)
def get_engine() -> Engine:
	return create_engine(_resolve_dsn(), **_engine_kwargs())


SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, class_=Session)


def get_session() -> Iterator[Session]:
	session = SessionLocal()
	try:
		yield session
	finally:
		session.close()
