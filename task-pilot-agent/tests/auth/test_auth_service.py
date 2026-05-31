from __future__ import annotations

import importlib
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

