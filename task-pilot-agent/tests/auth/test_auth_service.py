from __future__ import annotations

import importlib
import asyncio
from pathlib import Path

import pytest

from auth.providers.base import ExternalIdentityProfile


@pytest.fixture()
def auth_service(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_DB_URL", f"sqlite:///{tmp_path / 'auth.db'}")
    monkeypatch.setenv(
        "APP_CONFIG_FILE",
        str(Path(__file__).resolve().parents[3] / "config" / "config.yaml"),
    )

    import file.db_engine as db_engine

    db_engine.get_engine.cache_clear()
    db_engine = importlib.reload(db_engine)

    import auth.models as models
    import auth.service as service

    importlib.reload(models)
    service = importlib.reload(service)
    yield service.AuthService(), service

    db_engine.get_engine.cache_clear()


def google_profile(subject: str = "google-sub-1", email: str = "user@example.com") -> ExternalIdentityProfile:
    return ExternalIdentityProfile(
        provider="google",
        provider_subject=subject,
        provider_subject_type="sub",
        provider_app_id="google-client",
        provider_tenant_id="example.com",
        email=email,
        email_verified=True,
        display_name="Example User",
        avatar_url="https://example.com/avatar.png",
        locale="en",
        raw_profile={"sub": subject, "email": email, "token": "should-redact"},
    )


def microsoft_profile(subject: str = "tenant-1:oid-1", email: str = "ms@example.com") -> ExternalIdentityProfile:
    return ExternalIdentityProfile(
        provider="microsoft",
        provider_subject=subject,
        provider_subject_type="tenant_oid",
        provider_app_id="microsoft-client",
        provider_tenant_id="tenant-1",
        email=email,
        email_verified=True,
        display_name="Microsoft User",
        raw_profile={"oid": "oid-1", "tid": "tenant-1"},
    )


def test_first_provider_login_creates_user_and_identity(auth_service):
    service, _ = auth_service
    user = service.create_or_update_external_user(google_profile())

    assert user.user_id.startswith("usr_")
    assert user.primary_email == "user@example.com"
    assert user.display_name == "Example User"

    identity = service.get_identity("google", "google-sub-1")
    assert identity is not None
    assert identity.user_id == user.user_id
    assert identity.provider_subject_type == "sub"
    assert "should-redact" not in (identity.raw_profile_json or "")


def test_returning_provider_login_reuses_same_user(auth_service):
    service, _ = auth_service
    first = service.create_or_update_external_user(google_profile(email="old@example.com"))
    second = service.create_or_update_external_user(google_profile(email="new@example.com"))

    assert second.user_id == first.user_id
    assert second.primary_email == "new@example.com"


def test_binding_existing_identity_to_another_user_is_rejected(auth_service):
    service, service_module = auth_service
    first = service.create_or_update_external_user(google_profile("same-sub", "first@example.com"))
    second = service.create_or_update_external_user(google_profile("other-sub", "second@example.com"))

    assert first.user_id != second.user_id
    with pytest.raises(service_module.AuthConflictError):
        service.create_or_update_external_user(google_profile("same-sub"), current_user_id=second.user_id)


def test_session_create_read_and_revoke(auth_service):
    service, _ = auth_service
    user = service.create_or_update_external_user(google_profile())
    created = service.create_session(user.user_id, ttl_seconds=60, metadata={"cookie": "secret"})

    fetched = service.get_user_by_session_token(created.session_token)
    assert fetched is not None
    assert fetched.user_id == user.user_id

    assert service.revoke_session(created.session_token) is True
    assert service.get_user_by_session_token(created.session_token) is None


def test_oauth_state_is_one_time_and_nonce_checked(auth_service):
    service, service_module = auth_service
    created = service.create_oauth_state(provider="google", redirect_after="/agent/web/autoagent")

    consumed = service.consume_oauth_state(state=created.state, nonce=created.nonce, provider="google")
    assert consumed.provider == "google"
    assert consumed.redirect_after == "/agent/web/autoagent"

    with pytest.raises(service_module.AuthError):
        service.consume_oauth_state(state=created.state, nonce=created.nonce, provider="google")

    next_state = service.create_oauth_state(provider="google")
    with pytest.raises(service_module.AuthError):
        service.consume_oauth_state(state=next_state.state, nonce="wrong", provider="google")


def test_disabled_user_cannot_login_again(auth_service):
    service, service_module = auth_service
    user = service.create_or_update_external_user(google_profile())
    assert service.disable_user(user.user_id) is True

    with pytest.raises(service_module.AuthDisabledError):
        service.create_or_update_external_user(google_profile())


def test_user_crud_list_and_soft_delete(auth_service):
    service, service_module = auth_service
    user = service.create_user(
        user_id="manual-user-1",
        primary_email="manual@example.com",
        display_name="Manual User",
        source="manual",
        metadata={"token": "secret-value", "department": "ops"},
    )

    assert user.user_id == "manual-user-1"
    assert service.list_users(source="manual", keyword="manual@example.com")[0].user_id == user.user_id
    assert "secret-value" not in service.serialize_user(user)["metadata"].values()

    updated = service.update_user(user.user_id, {"display_name": "Renamed", "locale": "zh-CN"})
    assert updated.display_name == "Renamed"
    assert updated.locale == "zh-CN"

    created_session = service.create_session(user.user_id)
    assert service.get_user_by_session_token(created_session.session_token) is not None

    assert service.soft_delete_user(user.user_id) is True
    deleted = service.get_user(user.user_id)
    assert deleted is not None
    assert deleted.status == service_module.UserStatus.DELETED
    assert service.get_user_by_session_token(created_session.session_token) is None


def test_identity_unbind_requires_another_active_identity(auth_service):
    service, service_module = auth_service
    user = service.create_or_update_external_user(google_profile())
    microsoft_identity = service.bind_identity(user.user_id, microsoft_profile())

    identities = service.list_identities(user.user_id)
    assert {item.provider for item in identities} == {"google", "microsoft"}

    unbound = service.unbind_identity(user.user_id, microsoft_identity.identity_id)
    assert unbound.status == service_module.IdentityStatus.UNLINKED

    google_identity = service.get_identity("google", "google-sub-1")
    assert google_identity is not None
    with pytest.raises(service_module.AuthConflictError):
        service.unbind_identity(user.user_id, google_identity.identity_id)


def test_legacy_user_mapping_moves_existing_tasks_and_files(auth_service, tmp_path, monkeypatch):
    service, service_module = auth_service
    monkeypatch.setenv("TASK_WORKSPACE_ROOT", str(tmp_path / "task-workspaces"))
    monkeypatch.setenv("APP_CORE__UPLOAD_DIR", str(tmp_path / "uploads"))

    import file.db_engine as db_engine
    import brain.core.tasks as tasks
    import file.file_table_op as file_table_op

    db_engine.get_engine.cache_clear()
    db_engine = importlib.reload(db_engine)
    tasks = importlib.reload(tasks)
    file_table_op = importlib.reload(file_table_op)

    target_user = service.create_user(user_id="target-user", primary_email="target@example.com")
    service.ensure_legacy_user("legacy-user", primary_email="legacy@example.com")
    tasks.TaskStore().create_task(task_id="legacy-task", trace_id="legacy-task", user_id="legacy-user")
    asyncio.run(
        file_table_op.FileInfoOp.add_by_content(
            filename="legacy.txt",
            content="legacy content",
            file_id="legacy-file",
            request_id="legacy-request",
            user_id="legacy-user",
        )
    )

    with pytest.raises(service_module.AuthConflictError):
        service.map_legacy_user_records(
            legacy_user_id="legacy-user",
            target_user_id=target_user.user_id,
            trusted=False,
        )

    result = service.map_legacy_user_records(
        legacy_user_id="legacy-user",
        target_user_id=target_user.user_id,
        trusted=True,
    )

    assert result == {"tasks": 1, "files": 1}
    assert tasks.TaskStore().get_task("legacy-task").user_id == target_user.user_id
    assert asyncio.run(file_table_op.FileInfoOp.get_by_file_id("legacy-file")).user_id == target_user.user_id


def test_audit_events_are_sanitized_and_expired_records_cleanup(auth_service):
    service, service_module = auth_service
    user = service.create_user(user_id="audit-user", primary_email="audit@example.com")
    audit_event = service.record_audit_event(
        service_module.AuthAuditEventType.LOGIN,
        target_user_id=user.user_id,
        provider="google",
        metadata={"access_token": "raw-token", "nested": {"cookie": "raw-cookie"}},
    )

    events = service.list_audit_events(target_user_id=user.user_id)
    assert [event.event_id for event in events] == [audit_event.event_id]
    payload = service.serialize_audit_event(events[0])
    assert payload["metadata"]["access_token"] == "***"
    assert payload["metadata"]["nested"]["cookie"] == "***"

    created_session = service.create_session(user.user_id, ttl_seconds=1)
    created_state = service.create_oauth_state(provider="google", ttl_seconds=1)

    assert service.cleanup_expired_sessions(now=created_session.record.expires_at + 1) == 1
    assert service.get_user_by_session_token(created_session.session_token) is None
    assert service.cleanup_expired_oauth_states(now=created_state.record.expires_at + 1) == 1
