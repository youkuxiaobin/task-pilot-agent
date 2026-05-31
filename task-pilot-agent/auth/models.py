from __future__ import annotations

import json
import time
from typing import Any, Optional

from sqlalchemy import BigInteger, Boolean, Column, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import declarative_base

from file.db_engine import get_engine


Base = declarative_base()

ID_TYPE = BigInteger().with_variant(Integer, "sqlite")
LONG_TEXT = Text().with_variant(mysql.LONGTEXT, "mysql")


class UserStatus:
    ACTIVE = "active"
    DISABLED = "disabled"
    DELETED = "deleted"


class IdentityStatus:
    ACTIVE = "active"
    UNLINKED = "unlinked"
    DISABLED = "disabled"


class OAuthStatePurpose:
    LOGIN = "login"
    LINK = "link"


class TaskPilotUser(Base):
    __tablename__ = "task_pilot_user"

    id = Column(ID_TYPE, primary_key=True, autoincrement=True)
    user_id = Column(String(128), nullable=False, unique=True, index=True)
    primary_email = Column(String(320), nullable=True, index=True)
    display_name = Column(String(256), nullable=True)
    avatar_url = Column(String(2048), nullable=True)
    locale = Column(String(32), nullable=True)
    status = Column(String(32), nullable=False, index=True, default=UserStatus.ACTIVE)
    source = Column(String(32), nullable=False, default="google")
    metadata_json = Column("metadata", LONG_TEXT, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    last_login_at = Column(BigInteger, nullable=True)
    deleted_at = Column(BigInteger, nullable=True)


class TaskPilotUserIdentity(Base):
    __tablename__ = "task_pilot_user_identity"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subject", name="uq_task_pilot_identity_provider_subject"),
    )

    id = Column(ID_TYPE, primary_key=True, autoincrement=True)
    identity_id = Column(String(128), nullable=False, unique=True, index=True)
    user_id = Column(String(128), nullable=False, index=True)
    provider = Column(String(64), nullable=False, index=True)
    provider_subject = Column(String(255), nullable=False)
    provider_subject_type = Column(String(64), nullable=False, default="provider_user_id")
    provider_app_id = Column(String(255), nullable=True)
    provider_tenant_id = Column(String(255), nullable=True)
    email = Column(String(320), nullable=True, index=True)
    email_verified = Column(Boolean, nullable=False, default=False)
    display_name = Column(String(256), nullable=True)
    avatar_url = Column(String(2048), nullable=True)
    raw_profile_json = Column("raw_profile", LONG_TEXT, nullable=True)
    status = Column(String(32), nullable=False, index=True, default=IdentityStatus.ACTIVE)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    last_seen_at = Column(BigInteger, nullable=True)


class TaskPilotUserSession(Base):
    __tablename__ = "task_pilot_user_session"

    id = Column(ID_TYPE, primary_key=True, autoincrement=True)
    session_id = Column(String(128), nullable=False, unique=True, index=True)
    user_id = Column(String(128), nullable=False, index=True)
    session_token_hash = Column(String(128), nullable=False, unique=True, index=True)
    csrf_token_hash = Column(String(128), nullable=True)
    user_agent_hash = Column(String(128), nullable=True)
    ip_hash = Column(String(128), nullable=True)
    metadata_json = Column("metadata", LONG_TEXT, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    last_seen_at = Column(BigInteger, nullable=False)
    expires_at = Column(BigInteger, nullable=False, index=True)
    revoked_at = Column(BigInteger, nullable=True)


class TaskPilotOAuthState(Base):
    __tablename__ = "task_pilot_oauth_state"

    id = Column(ID_TYPE, primary_key=True, autoincrement=True)
    state_hash = Column(String(128), nullable=False, unique=True, index=True)
    nonce_hash = Column(String(128), nullable=False)
    purpose = Column(String(32), nullable=False, default=OAuthStatePurpose.LOGIN)
    provider = Column(String(64), nullable=False, default="google")
    user_id = Column(String(128), nullable=True, index=True)
    redirect_after = Column(String(2048), nullable=True)
    metadata_json = Column("metadata", LONG_TEXT, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    expires_at = Column(BigInteger, nullable=False, index=True)
    consumed_at = Column(BigInteger, nullable=True)


class TaskPilotExternalConnection(Base):
    __tablename__ = "task_pilot_external_connection"

    id = Column(ID_TYPE, primary_key=True, autoincrement=True)
    connection_id = Column(String(128), nullable=False, unique=True, index=True)
    user_id = Column(String(128), nullable=False, index=True)
    identity_id = Column(String(128), nullable=False, index=True)
    provider = Column(String(64), nullable=False, index=True)
    provider_subject = Column(String(255), nullable=False)
    scopes_json = Column("scopes", LONG_TEXT, nullable=True)
    access_token_encrypted = Column(LONG_TEXT, nullable=True)
    refresh_token_encrypted = Column(LONG_TEXT, nullable=True)
    expires_at = Column(BigInteger, nullable=True)
    revoked_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


def now_ms() -> int:
    return int(time.time() * 1000)


def json_dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def json_loads(value: Optional[str], default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def create_auth_tables() -> None:
    Base.metadata.create_all(get_engine())

