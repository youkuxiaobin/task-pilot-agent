from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from auth.models import (
    IdentityStatus,
    OAuthStatePurpose,
    TaskPilotOAuthState,
    TaskPilotUser,
    TaskPilotUserIdentity,
    TaskPilotUserSession,
    UserStatus,
    create_auth_tables,
    json_dumps,
    json_loads,
    now_ms,
)
from auth.providers.base import ExternalIdentityProfile, normalize_provider_name
from auth.security import generate_token, hash_token
from brain.core.sanitization import sanitize_payload
from config.config import agentSettings
from file.db_engine import get_engine


class AuthError(RuntimeError):
    pass


class AuthConflictError(AuthError):
    pass


class AuthNotFoundError(AuthError):
    pass


class AuthDisabledError(AuthError):
    pass


@dataclass(frozen=True)
class CreatedSession:
    record: TaskPilotUserSession
    session_token: str
    csrf_token: str


@dataclass(frozen=True)
class CreatedOAuthState:
    record: TaskPilotOAuthState
    state: str
    nonce: str


class AuthService:
    def __init__(self) -> None:
        self._engine = get_engine()
        create_auth_tables()
        self._session_maker = sessionmaker(bind=self._engine, expire_on_commit=False, class_=Session)

    def create_or_update_external_user(
        self,
        profile: ExternalIdentityProfile,
        *,
        current_user_id: Optional[str] = None,
    ) -> TaskPilotUser:
        provider = normalize_provider_name(profile.provider)
        timestamp = now_ms()
        session = self._session_maker()
        try:
            identity = self._get_identity_for_update(session, provider, profile.provider_subject)
            if identity:
                if current_user_id and identity.user_id != current_user_id:
                    raise AuthConflictError("external identity is already bound to another user")
                user = self._get_user_for_update(session, identity.user_id)
                if not user:
                    raise AuthNotFoundError("identity owner not found")
                self._assert_user_can_login(user)
                self._apply_profile_to_identity(identity, profile, timestamp)
                self._apply_profile_to_user(user, profile, timestamp)
                user.last_login_at = timestamp
                session.commit()
                session.refresh(user)
                session.expunge(user)
                return user

            if current_user_id:
                user = self._get_user_for_update(session, current_user_id)
                if not user:
                    raise AuthNotFoundError("current user not found")
                self._assert_user_active(user)
            else:
                user = TaskPilotUser(
                    user_id=generate_token("usr", 18),
                    source=provider,
                    status=UserStatus.ACTIVE,
                    created_at=timestamp,
                    updated_at=timestamp,
                    last_login_at=timestamp,
                )
                self._apply_profile_to_user(user, profile, timestamp)
                session.add(user)
                session.flush()

            identity = TaskPilotUserIdentity(
                identity_id=generate_token("idn", 18),
                user_id=user.user_id,
                provider=provider,
                provider_subject=profile.provider_subject,
                created_at=timestamp,
                updated_at=timestamp,
                last_seen_at=timestamp,
            )
            self._apply_profile_to_identity(identity, profile, timestamp)
            session.add(identity)
            user.last_login_at = timestamp
            user.updated_at = timestamp
            session.commit()
            session.refresh(user)
            session.expunge(user)
            return user
        except IntegrityError as exc:
            session.rollback()
            raise AuthConflictError("external identity already exists") from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def bind_identity(self, user_id: str, profile: ExternalIdentityProfile) -> TaskPilotUserIdentity:
        provider = normalize_provider_name(profile.provider)
        timestamp = now_ms()
        session = self._session_maker()
        try:
            user = self._get_user_for_update(session, user_id)
            if not user:
                raise AuthNotFoundError("user not found")
            self._assert_user_active(user)
            existing = self._get_identity_for_update(session, provider, profile.provider_subject)
            if existing:
                if existing.user_id != user_id:
                    raise AuthConflictError("external identity is already bound to another user")
                self._apply_profile_to_identity(existing, profile, timestamp)
                session.commit()
                session.refresh(existing)
                session.expunge(existing)
                return existing
            identity = TaskPilotUserIdentity(
                identity_id=generate_token("idn", 18),
                user_id=user_id,
                provider=provider,
                provider_subject=profile.provider_subject,
                created_at=timestamp,
                updated_at=timestamp,
                last_seen_at=timestamp,
            )
            self._apply_profile_to_identity(identity, profile, timestamp)
            session.add(identity)
            user.updated_at = timestamp
            session.commit()
            session.refresh(identity)
            session.expunge(identity)
            return identity
        except IntegrityError as exc:
            session.rollback()
            raise AuthConflictError("external identity already exists") from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_session(
        self,
        user_id: str,
        *,
        ttl_seconds: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CreatedSession:
        timestamp = now_ms()
        ttl = int(ttl_seconds or agentSettings.auth.session_ttl_seconds)
        session_token = generate_token("ses", 32)
        csrf_token = generate_token("csrf", 24)
        record = TaskPilotUserSession(
            session_id=generate_token("sid", 18),
            user_id=user_id,
            session_token_hash=hash_token(session_token),
            csrf_token_hash=hash_token(csrf_token),
            metadata_json=json_dumps(sanitize_payload(metadata or {})),
            created_at=timestamp,
            last_seen_at=timestamp,
            expires_at=timestamp + ttl * 1000,
        )
        session = self._session_maker()
        try:
            user = self._get_user_for_update(session, user_id)
            if not user:
                raise AuthNotFoundError("user not found")
            self._assert_user_can_login(user)
            session.add(record)
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return CreatedSession(record=record, session_token=session_token, csrf_token=csrf_token)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_user_by_session_token(self, session_token: str) -> Optional[TaskPilotUser]:
        if not session_token:
            return None
        timestamp = now_ms()
        session = self._session_maker()
        try:
            record = (
                session.query(TaskPilotUserSession)
                .filter(TaskPilotUserSession.session_token_hash == hash_token(session_token))
                .one_or_none()
            )
            if not record or record.revoked_at is not None or record.expires_at <= timestamp:
                return None
            user = session.query(TaskPilotUser).filter(TaskPilotUser.user_id == record.user_id).one_or_none()
            if not user or user.status != UserStatus.ACTIVE:
                return None
            record.last_seen_at = timestamp
            user.last_login_at = user.last_login_at or timestamp
            session.commit()
            session.refresh(user)
            session.expunge(user)
            return user
        finally:
            session.close()

    def revoke_session(self, session_token: str) -> bool:
        if not session_token:
            return False
        session = self._session_maker()
        try:
            record = (
                session.query(TaskPilotUserSession)
                .filter(TaskPilotUserSession.session_token_hash == hash_token(session_token))
                .one_or_none()
            )
            if not record or record.revoked_at is not None:
                return False
            record.revoked_at = now_ms()
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_oauth_state(
        self,
        *,
        provider: str = "google",
        purpose: str = OAuthStatePurpose.LOGIN,
        user_id: Optional[str] = None,
        redirect_after: Optional[str] = None,
        ttl_seconds: int = 600,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CreatedOAuthState:
        timestamp = now_ms()
        state = generate_token("oauth", 32)
        nonce = generate_token("nonce", 24)
        record = TaskPilotOAuthState(
            state_hash=hash_token(state),
            nonce_hash=hash_token(nonce),
            purpose=purpose,
            provider=normalize_provider_name(provider),
            user_id=user_id,
            redirect_after=_safe_redirect_after(redirect_after),
            metadata_json=json_dumps(sanitize_payload(metadata or {})),
            created_at=timestamp,
            expires_at=timestamp + ttl_seconds * 1000,
        )
        session = self._session_maker()
        try:
            session.add(record)
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return CreatedOAuthState(record=record, state=state, nonce=nonce)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def consume_oauth_state(
        self,
        *,
        state: str,
        nonce: str,
        provider: str = "google",
        purpose: Optional[str] = None,
    ) -> TaskPilotOAuthState:
        timestamp = now_ms()
        session = self._session_maker()
        try:
            record = (
                session.query(TaskPilotOAuthState)
                .filter(TaskPilotOAuthState.state_hash == hash_token(state))
                .one_or_none()
            )
            if not record:
                raise AuthNotFoundError("oauth state not found")
            if record.provider != normalize_provider_name(provider):
                raise AuthError("oauth provider mismatch")
            if purpose and record.purpose != purpose:
                raise AuthError("oauth purpose mismatch")
            if record.consumed_at is not None:
                raise AuthError("oauth state already consumed")
            if record.expires_at <= timestamp:
                raise AuthError("oauth state expired")
            if record.nonce_hash != hash_token(nonce):
                raise AuthError("oauth nonce mismatch")
            record.consumed_at = timestamp
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_user(self, user_id: str) -> Optional[TaskPilotUser]:
        session = self._session_maker()
        try:
            user = session.query(TaskPilotUser).filter(TaskPilotUser.user_id == user_id).one_or_none()
            if user:
                session.expunge(user)
            return user
        finally:
            session.close()

    def get_identity(self, provider: str, provider_subject: str) -> Optional[TaskPilotUserIdentity]:
        session = self._session_maker()
        try:
            identity = (
                session.query(TaskPilotUserIdentity)
                .filter(
                    TaskPilotUserIdentity.provider == normalize_provider_name(provider),
                    TaskPilotUserIdentity.provider_subject == provider_subject,
                )
                .one_or_none()
            )
            if identity:
                session.expunge(identity)
            return identity
        finally:
            session.close()

    def disable_user(self, user_id: str) -> bool:
        session = self._session_maker()
        try:
            user = self._get_user_for_update(session, user_id)
            if not user:
                return False
            timestamp = now_ms()
            user.status = UserStatus.DISABLED
            user.updated_at = timestamp
            (
                session.query(TaskPilotUserSession)
                .filter(TaskPilotUserSession.user_id == user_id, TaskPilotUserSession.revoked_at.is_(None))
                .update({"revoked_at": timestamp}, synchronize_session=False)
            )
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def serialize_user(self, user: TaskPilotUser) -> Dict[str, Any]:
        return {
            "userId": user.user_id,
            "primaryEmail": user.primary_email,
            "displayName": user.display_name,
            "avatarUrl": user.avatar_url,
            "locale": user.locale,
            "status": user.status,
            "source": user.source,
            "metadata": json_loads(user.metadata_json, {}),
            "createdAt": user.created_at,
            "updatedAt": user.updated_at,
            "lastLoginAt": user.last_login_at,
        }

    def _get_identity_for_update(
        self,
        session: Session,
        provider: str,
        provider_subject: str,
    ) -> Optional[TaskPilotUserIdentity]:
        return (
            session.query(TaskPilotUserIdentity)
            .filter(
                TaskPilotUserIdentity.provider == normalize_provider_name(provider),
                TaskPilotUserIdentity.provider_subject == provider_subject,
                TaskPilotUserIdentity.status == IdentityStatus.ACTIVE,
            )
            .one_or_none()
        )

    def _get_user_for_update(self, session: Session, user_id: str) -> Optional[TaskPilotUser]:
        return session.query(TaskPilotUser).filter(TaskPilotUser.user_id == user_id).one_or_none()

    def _assert_user_active(self, user: TaskPilotUser) -> None:
        if user.status != UserStatus.ACTIVE:
            raise AuthDisabledError("user is not active")

    def _assert_user_can_login(self, user: TaskPilotUser) -> None:
        if user.status in {UserStatus.DISABLED, UserStatus.DELETED}:
            raise AuthDisabledError("user cannot log in")

    def _apply_profile_to_identity(
        self,
        identity: TaskPilotUserIdentity,
        profile: ExternalIdentityProfile,
        timestamp: int,
    ) -> None:
        identity.provider = normalize_provider_name(profile.provider)
        identity.provider_subject = profile.provider_subject
        identity.provider_subject_type = profile.provider_subject_type
        identity.provider_app_id = profile.provider_app_id
        identity.provider_tenant_id = profile.provider_tenant_id
        identity.email = profile.email
        identity.email_verified = bool(profile.email_verified)
        identity.display_name = profile.display_name
        identity.avatar_url = profile.avatar_url
        identity.raw_profile_json = json_dumps(sanitize_payload(profile.raw_profile))
        identity.status = IdentityStatus.ACTIVE
        identity.updated_at = timestamp
        identity.last_seen_at = timestamp

    def _apply_profile_to_user(
        self,
        user: TaskPilotUser,
        profile: ExternalIdentityProfile,
        timestamp: int,
    ) -> None:
        user.primary_email = profile.email or user.primary_email
        user.display_name = profile.display_name or user.display_name
        user.avatar_url = profile.avatar_url or user.avatar_url
        user.locale = profile.locale or user.locale
        user.status = user.status or UserStatus.ACTIVE
        user.source = user.source or normalize_provider_name(profile.provider)
        metadata = json_loads(user.metadata_json, {})
        metadata["lastProvider"] = normalize_provider_name(profile.provider)
        if profile.provider_tenant_id:
            metadata["providerTenantId"] = profile.provider_tenant_id
        user.metadata_json = json_dumps(sanitize_payload(metadata))
        user.updated_at = timestamp


def _safe_redirect_after(value: Optional[str]) -> str:
    if not value:
        return "/agent/web/autoagent"
    text = str(value).strip()
    if not text.startswith("/") or text.startswith("//"):
        return "/agent/web/autoagent"
    return text

