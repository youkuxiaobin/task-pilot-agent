from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from auth.models import (
    IdentityStatus,
    OAuthStatePurpose,
    TaskPilotAuthAuditEvent,
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


class AuthAuditEventType:
    LOGIN = "login"
    LOGOUT = "logout"
    LOGOUT_ALL = "logout_all"
    IDENTITY_BOUND = "identity_bound"
    IDENTITY_UNBOUND = "identity_unbound"
    USER_CREATED = "user_created"
    USER_UPDATED = "user_updated"
    USER_DISABLED = "user_disabled"
    USER_DELETED = "user_deleted"
    LEGACY_USER_MAPPED = "legacy_user_mapped"


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

    def revoke_all_sessions(self, user_id: str) -> int:
        timestamp = now_ms()
        session = self._session_maker()
        try:
            count = (
                session.query(TaskPilotUserSession)
                .filter(TaskPilotUserSession.user_id == user_id, TaskPilotUserSession.revoked_at.is_(None))
                .update({"revoked_at": timestamp}, synchronize_session=False)
            )
            session.commit()
            return int(count or 0)
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

    def create_user(
        self,
        *,
        user_id: Optional[str] = None,
        primary_email: Optional[str] = None,
        display_name: Optional[str] = None,
        avatar_url: Optional[str] = None,
        locale: Optional[str] = None,
        source: str = "manual",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TaskPilotUser:
        timestamp = now_ms()
        record = TaskPilotUser(
            user_id=user_id or generate_token("usr", 18),
            primary_email=primary_email,
            display_name=display_name,
            avatar_url=avatar_url,
            locale=locale,
            status=UserStatus.ACTIVE,
            source=source or "manual",
            metadata_json=json_dumps(sanitize_payload(metadata or {})),
            created_at=timestamp,
            updated_at=timestamp,
        )
        session = self._session_maker()
        try:
            session.add(record)
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record
        except IntegrityError as exc:
            session.rollback()
            raise AuthConflictError("user already exists") from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_user(self, user_id: str, updates: Dict[str, Any]) -> TaskPilotUser:
        timestamp = now_ms()
        session = self._session_maker()
        try:
            user = self._get_user_for_update(session, user_id)
            if not user:
                raise AuthNotFoundError("user not found")
            self._assert_user_active(user)
            for field_name in ("primary_email", "display_name", "avatar_url", "locale"):
                if field_name in updates:
                    value = updates.get(field_name)
                    setattr(user, field_name, str(value).strip() if value not in (None, "") else None)
            if "metadata" in updates and isinstance(updates["metadata"], dict):
                metadata = json_loads(user.metadata_json, {})
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata.update(sanitize_payload(updates["metadata"]))
                user.metadata_json = json_dumps(metadata)
            user.updated_at = timestamp
            session.commit()
            session.refresh(user)
            session.expunge(user)
            return user
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_users(
        self,
        *,
        status: Optional[str] = None,
        source: Optional[str] = None,
        keyword: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[TaskPilotUser]:
        session = self._session_maker()
        try:
            query = session.query(TaskPilotUser)
            if status:
                query = query.filter(TaskPilotUser.status == status)
            if source:
                query = query.filter(TaskPilotUser.source == source)
            normalized_keyword = (keyword or "").strip()
            if normalized_keyword:
                pattern = f"%{normalized_keyword}%"
                query = query.filter(
                    (TaskPilotUser.user_id.like(pattern))
                    | (TaskPilotUser.primary_email.like(pattern))
                    | (TaskPilotUser.display_name.like(pattern))
                )
            users = (
                query.order_by(TaskPilotUser.created_at.desc())
                .offset(max(offset, 0))
                .limit(max(min(limit, 200), 1))
                .all()
            )
            for user in users:
                session.expunge(user)
            return users
        finally:
            session.close()

    def ensure_dev_user(self, user_id: Optional[str] = None) -> TaskPilotUser:
        resolved_user_id = user_id or agentSettings.auth.dev_user_id
        timestamp = now_ms()
        session = self._session_maker()
        try:
            user = session.query(TaskPilotUser).filter(TaskPilotUser.user_id == resolved_user_id).one_or_none()
            if user:
                if user.status != UserStatus.ACTIVE:
                    user.status = UserStatus.ACTIVE
                    user.updated_at = timestamp
                    session.commit()
                    session.refresh(user)
                session.expunge(user)
                return user
            user = TaskPilotUser(
                user_id=resolved_user_id,
                primary_email=f"{resolved_user_id}@local.taskpilot",
                display_name="TaskPilot Dev User",
                status=UserStatus.ACTIVE,
                source="dev",
                metadata_json=json_dumps({"devFallback": True}),
                created_at=timestamp,
                updated_at=timestamp,
                last_login_at=timestamp,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            session.expunge(user)
            return user
        except Exception:
            session.rollback()
            raise
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

    def list_identities(self, user_id: str) -> List[TaskPilotUserIdentity]:
        session = self._session_maker()
        try:
            identities = (
                session.query(TaskPilotUserIdentity)
                .filter(TaskPilotUserIdentity.user_id == user_id)
                .order_by(TaskPilotUserIdentity.created_at.asc())
                .all()
            )
            for identity in identities:
                session.expunge(identity)
            return identities
        finally:
            session.close()

    def get_user_identity(self, user_id: str, identity_id: str) -> Optional[TaskPilotUserIdentity]:
        session = self._session_maker()
        try:
            identity = (
                session.query(TaskPilotUserIdentity)
                .filter(TaskPilotUserIdentity.user_id == user_id, TaskPilotUserIdentity.identity_id == identity_id)
                .one_or_none()
            )
            if identity:
                session.expunge(identity)
            return identity
        finally:
            session.close()

    def unbind_identity(self, user_id: str, identity_id: str) -> TaskPilotUserIdentity:
        timestamp = now_ms()
        session = self._session_maker()
        try:
            user = self._get_user_for_update(session, user_id)
            if not user:
                raise AuthNotFoundError("user not found")
            self._assert_user_active(user)
            identity = (
                session.query(TaskPilotUserIdentity)
                .filter(
                    TaskPilotUserIdentity.user_id == user_id,
                    TaskPilotUserIdentity.identity_id == identity_id,
                    TaskPilotUserIdentity.status == IdentityStatus.ACTIVE,
                )
                .one_or_none()
            )
            if not identity:
                raise AuthNotFoundError("identity not found")
            active_count = (
                session.query(TaskPilotUserIdentity)
                .filter(
                    TaskPilotUserIdentity.user_id == user_id,
                    TaskPilotUserIdentity.status == IdentityStatus.ACTIVE,
                )
                .count()
            )
            if active_count <= 1:
                raise AuthConflictError("cannot unbind the last active identity")
            identity.status = IdentityStatus.UNLINKED
            identity.updated_at = timestamp
            user.updated_at = timestamp
            session.commit()
            session.refresh(identity)
            session.expunge(identity)
            return identity
        except Exception:
            session.rollback()
            raise
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

    def soft_delete_user(self, user_id: str) -> bool:
        session = self._session_maker()
        try:
            user = self._get_user_for_update(session, user_id)
            if not user:
                return False
            timestamp = now_ms()
            user.status = UserStatus.DELETED
            user.deleted_at = timestamp
            user.updated_at = timestamp
            (
                session.query(TaskPilotUserSession)
                .filter(TaskPilotUserSession.user_id == user_id, TaskPilotUserSession.revoked_at.is_(None))
                .update({"revoked_at": timestamp}, synchronize_session=False)
            )
            (
                session.query(TaskPilotUserIdentity)
                .filter(TaskPilotUserIdentity.user_id == user_id, TaskPilotUserIdentity.status == IdentityStatus.ACTIVE)
                .update({"status": IdentityStatus.DISABLED, "updated_at": timestamp}, synchronize_session=False)
            )
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def ensure_legacy_user(
        self,
        legacy_user_id: str,
        *,
        primary_email: Optional[str] = None,
        display_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TaskPilotUser:
        normalized_user_id = str(legacy_user_id or "").strip()
        if not normalized_user_id:
            raise ValueError("legacy_user_id is required")
        existing = self.get_user(normalized_user_id)
        if existing:
            return existing
        return self.create_user(
            user_id=normalized_user_id,
            primary_email=primary_email,
            display_name=display_name or normalized_user_id,
            source="legacy",
            metadata={"legacyUser": True, **(metadata or {})},
        )

    def map_legacy_user_records(
        self,
        *,
        legacy_user_id: str,
        target_user_id: str,
        trusted: bool = False,
    ) -> Dict[str, int]:
        if not trusted:
            raise AuthConflictError("legacy user mapping requires trusted admin confirmation")
        normalized_legacy_user_id = str(legacy_user_id or "").strip()
        normalized_target_user_id = str(target_user_id or "").strip()
        if not normalized_legacy_user_id or not normalized_target_user_id:
            raise ValueError("legacy_user_id and target_user_id are required")

        session = self._session_maker()
        try:
            target_user = self._get_user_for_update(session, normalized_target_user_id)
            if not target_user:
                raise AuthNotFoundError("target user not found")
            self._assert_user_active(target_user)

            import brain.core.tasks as task_models
            import file.file_table_op as file_models

            task_models.Base.metadata.create_all(self._engine)
            file_models.Base.metadata.create_all(self._engine)
            if hasattr(file_models, "_ensure_file_owner_columns"):
                file_models._ensure_file_owner_columns()

            task_count = (
                session.query(task_models.AgentTaskRecord)
                .filter(task_models.AgentTaskRecord.user_id == normalized_legacy_user_id)
                .update({"user_id": normalized_target_user_id}, synchronize_session=False)
            )
            file_count = (
                session.query(file_models.FileInfo)
                .filter(file_models.FileInfo.user_id == normalized_legacy_user_id)
                .update({"user_id": normalized_target_user_id}, synchronize_session=False)
            )
            target_user.updated_at = now_ms()
            session.commit()
            return {"tasks": int(task_count or 0), "files": int(file_count or 0)}
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

    def serialize_identity(self, identity: TaskPilotUserIdentity) -> Dict[str, Any]:
        return {
            "identityId": identity.identity_id,
            "userId": identity.user_id,
            "provider": identity.provider,
            "providerSubjectType": identity.provider_subject_type,
            "providerAppId": identity.provider_app_id,
            "providerTenantId": identity.provider_tenant_id,
            "email": identity.email,
            "emailVerified": identity.email_verified,
            "displayName": identity.display_name,
            "avatarUrl": identity.avatar_url,
            "status": identity.status,
            "createdAt": identity.created_at,
            "updatedAt": identity.updated_at,
            "lastSeenAt": identity.last_seen_at,
        }

    def record_audit_event(
        self,
        event_type: str,
        *,
        actor_user_id: Optional[str] = None,
        target_user_id: Optional[str] = None,
        provider: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        request: Any = None,
    ) -> TaskPilotAuthAuditEvent:
        record = TaskPilotAuthAuditEvent(
            event_id=generate_token("aud", 18),
            event_type=event_type,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            provider=normalize_provider_name(provider) if provider else None,
            ip_hash=_hash_request_ip(request),
            user_agent_hash=_hash_request_user_agent(request),
            metadata_json=json_dumps(sanitize_payload(metadata or {})),
            created_at=now_ms(),
        )
        session = self._session_maker()
        try:
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

    def list_audit_events(
        self,
        *,
        actor_user_id: Optional[str] = None,
        target_user_id: Optional[str] = None,
        event_type: Optional[str] = None,
        provider: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[TaskPilotAuthAuditEvent]:
        session = self._session_maker()
        try:
            query = session.query(TaskPilotAuthAuditEvent)
            if actor_user_id:
                query = query.filter(TaskPilotAuthAuditEvent.actor_user_id == actor_user_id)
            if target_user_id:
                query = query.filter(TaskPilotAuthAuditEvent.target_user_id == target_user_id)
            if event_type:
                query = query.filter(TaskPilotAuthAuditEvent.event_type == event_type)
            if provider:
                query = query.filter(TaskPilotAuthAuditEvent.provider == normalize_provider_name(provider))
            events = (
                query.order_by(TaskPilotAuthAuditEvent.created_at.desc())
                .offset(max(offset, 0))
                .limit(max(min(limit, 200), 1))
                .all()
            )
            for event in events:
                session.expunge(event)
            return events
        finally:
            session.close()

    def cleanup_expired_sessions(self, *, now: Optional[int] = None) -> int:
        timestamp = int(now if now is not None else now_ms())
        session = self._session_maker()
        try:
            count = (
                session.query(TaskPilotUserSession)
                .filter(TaskPilotUserSession.expires_at <= timestamp)
                .delete(synchronize_session=False)
            )
            session.commit()
            return int(count or 0)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def cleanup_expired_oauth_states(self, *, now: Optional[int] = None) -> int:
        timestamp = int(now if now is not None else now_ms())
        session = self._session_maker()
        try:
            count = (
                session.query(TaskPilotOAuthState)
                .filter(
                    (TaskPilotOAuthState.expires_at <= timestamp)
                    | (TaskPilotOAuthState.consumed_at.is_not(None))
                )
                .delete(synchronize_session=False)
            )
            session.commit()
            return int(count or 0)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def serialize_audit_event(self, event: TaskPilotAuthAuditEvent) -> Dict[str, Any]:
        return {
            "eventId": event.event_id,
            "eventType": event.event_type,
            "actorUserId": event.actor_user_id,
            "targetUserId": event.target_user_id,
            "provider": event.provider,
            "metadata": json_loads(event.metadata_json, {}),
            "createdAt": event.created_at,
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


def _hash_request_ip(request: Any) -> Optional[str]:
    if request is None:
        return None
    try:
        forwarded = request.headers.get("x-forwarded-for", "")
    except Exception:
        forwarded = ""
    if forwarded:
        raw_ip = forwarded.split(",", 1)[0].strip()
    else:
        client = getattr(request, "client", None)
        raw_ip = getattr(client, "host", None)
    return hash_token(raw_ip) if raw_ip else None


def _hash_request_user_agent(request: Any) -> Optional[str]:
    if request is None:
        return None
    try:
        user_agent = request.headers.get("user-agent")
    except Exception:
        user_agent = None
    return hash_token(user_agent) if user_agent else None
