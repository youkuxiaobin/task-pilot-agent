from __future__ import annotations

import importlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.providers.base import ExternalIdentityProfile


@pytest.fixture()
def auth_app(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_DB_URL", f"sqlite:///{tmp_path / 'auth-router.db'}")
    monkeypatch.setenv(
        "APP_CONFIG_FILE",
        str(Path(__file__).resolve().parents[3] / "config" / "config.yaml"),
    )

    import file.db_engine as db_engine

    db_engine.get_engine.cache_clear()
    db_engine = importlib.reload(db_engine)

    import auth.models as models
    import auth.service as service
    import auth.router as router

    importlib.reload(models)
    importlib.reload(service)
    router = importlib.reload(router)

    router.agentSettings.auth.required = False
    router.agentSettings.auth.cookie_secure = False
    router.agentSettings.auth.admin_user_ids = []
    router.agentSettings.auth.providers["google"].enabled = False

    app = FastAPI()
    app.include_router(router.auth_router, prefix="/auth")
    yield app, router

    db_engine.get_engine.cache_clear()


def test_me_returns_dev_user_when_auth_is_not_required(auth_app):
    app, _ = auth_app
    client = TestClient(app)

    response = client.get("/auth/me")

    assert response.status_code == 200
    payload = response.json()
    assert payload["authenticated"] is False
    assert payload["authRequired"] is False
    assert payload["user"]["userId"] == "dev-user"


def test_google_login_requires_enabled_provider(auth_app):
    app, _ = auth_app
    client = TestClient(app)

    response = client.get("/auth/google/login", follow_redirects=False)

    assert response.status_code == 503


def test_google_login_callback_creates_session(auth_app, monkeypatch):
    app, router = auth_app

    class FakeGoogleProvider:
        def authorization_url(self, *, state, nonce, redirect_uri, scopes=None):
            return f"https://provider.example/login?state={state}&nonce={nonce}"

        async def exchange_code(self, *, code, redirect_uri):
            assert code == "ok"
            return {"id_token": "fake"}

        async def normalize_identity(self, *, token_response, nonce=None):
            assert token_response == {"id_token": "fake"}
            assert nonce
            return ExternalIdentityProfile(
                provider="google",
                provider_subject="router-google-sub",
                provider_subject_type="sub",
                email="router@example.com",
                email_verified=True,
                display_name="Router User",
                raw_profile={"sub": "router-google-sub"},
            )

    class FakeRegistry:
        def list_public_providers(self):
            return [{"provider": "google", "label": "Google"}]

        def get(self, provider):
            assert provider == "google"
            return FakeGoogleProvider()

        def redirect_uri(self, provider):
            return "http://testserver/auth/google/callback"

    monkeypatch.setattr(router, "provider_registry", FakeRegistry())
    client = TestClient(app)

    login_response = client.get("/auth/google/login", follow_redirects=False)
    assert login_response.status_code in {302, 307}
    location = login_response.headers["location"]
    state = parse_qs(urlparse(location).query)["state"][0]

    callback_response = client.get(
        f"/auth/google/callback?code=ok&state={state}",
        follow_redirects=False,
    )
    assert callback_response.status_code in {302, 307}
    assert "tpa_session=" in callback_response.headers.get("set-cookie", "")

    me_response = client.get("/auth/me")
    assert me_response.status_code == 200
    payload = me_response.json()
    assert payload["authenticated"] is True
    assert payload["user"]["primaryEmail"] == "router@example.com"

    router.agentSettings.auth.admin_user_ids = [payload["user"]["userId"]]
    audit_response = client.get("/auth/admin/audit-events", params={"event_type": "login"})
    assert audit_response.status_code == 200
    assert audit_response.json()["items"][0]["targetUserId"] == payload["user"]["userId"]

    logout_response = client.post("/auth/logout")
    assert logout_response.status_code == 200
    router.agentSettings.auth.admin_user_ids = []
    logout_audit_response = client.get("/auth/admin/audit-events", params={"event_type": "logout"})
    assert logout_audit_response.status_code == 200
    assert logout_audit_response.json()["items"][0]["targetUserId"] == payload["user"]["userId"]


def test_provider_link_binds_identity_to_current_user(auth_app, monkeypatch):
    app, router = auth_app

    class FakeMicrosoftProvider:
        def authorization_url(self, *, state, nonce, redirect_uri, scopes=None):
            return f"https://provider.example/link?state={state}&nonce={nonce}"

        async def exchange_code(self, *, code, redirect_uri):
            assert code == "ok"
            return {"id_token": "fake-link"}

        async def normalize_identity(self, *, token_response, nonce=None):
            return ExternalIdentityProfile(
                provider="microsoft",
                provider_subject="tenant-1:oid-1",
                provider_subject_type="tenant_oid",
                provider_tenant_id="tenant-1",
                email="linked@example.com",
                email_verified=True,
                display_name="Linked User",
                raw_profile={"tid": "tenant-1", "oid": "oid-1", "refresh_token": "must-redact"},
            )

    class FakeRegistry:
        def list_public_providers(self):
            return [{"provider": "microsoft", "label": "Microsoft"}]

        def get(self, provider):
            assert provider == "microsoft"
            return FakeMicrosoftProvider()

        def redirect_uri(self, provider):
            return "http://testserver/auth/microsoft/callback"

    monkeypatch.setattr(router, "provider_registry", FakeRegistry())
    client = TestClient(app)

    link_response = client.post("/auth/microsoft/link", follow_redirects=False)
    assert link_response.status_code in {302, 307}
    state = parse_qs(urlparse(link_response.headers["location"]).query)["state"][0]

    callback_response = client.get(
        f"/auth/microsoft/callback?code=ok&state={state}",
        follow_redirects=False,
    )
    assert callback_response.status_code in {302, 307}

    identities_response = client.get("/auth/users/me/identities")
    assert identities_response.status_code == 200
    identities = identities_response.json()["items"]
    assert identities[0]["provider"] == "microsoft"
    assert identities[0]["providerTenantId"] == "tenant-1"

    audit_response = client.get("/auth/admin/audit-events", params={"event_type": "identity_bound"})
    assert audit_response.status_code == 200
    assert audit_response.json()["items"][0]["provider"] == "microsoft"


def test_current_user_profile_routes_use_dev_user_when_auth_optional(auth_app):
    app, _ = auth_app
    client = TestClient(app)

    profile_response = client.get("/auth/users/me")
    assert profile_response.status_code == 200
    assert profile_response.json()["user"]["userId"] == "dev-user"

    update_response = client.patch("/auth/users/me", json={"display_name": "Local User", "locale": "zh-CN"})
    assert update_response.status_code == 200
    payload = update_response.json()
    assert payload["user"]["displayName"] == "Local User"
    assert payload["user"]["locale"] == "zh-CN"

    identities_response = client.get("/auth/users/me/identities")
    assert identities_response.status_code == 200
    assert identities_response.json()["items"] == []


def test_admin_user_crud_routes_use_dev_admin_when_auth_optional(auth_app):
    app, _ = auth_app
    client = TestClient(app)

    create_response = client.post(
        "/auth/admin/users",
        json={
            "user_id": "manual-admin-test",
            "primary_email": "manual-admin@example.com",
            "display_name": "Manual Admin Test",
            "source": "manual",
        },
    )
    assert create_response.status_code == 200
    assert create_response.json()["user"]["userId"] == "manual-admin-test"

    list_response = client.get("/auth/admin/users", params={"keyword": "manual-admin@example.com"})
    assert list_response.status_code == 200
    assert [item["userId"] for item in list_response.json()["items"]] == ["manual-admin-test"]

    update_response = client.patch(
        "/auth/admin/users/manual-admin-test",
        json={"display_name": "Renamed Admin Test"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["user"]["displayName"] == "Renamed Admin Test"

    disable_response = client.post("/auth/admin/users/manual-admin-test/disable")
    assert disable_response.status_code == 200
    assert disable_response.json() == {"disabled": True}

    delete_response = client.delete("/auth/admin/users/manual-admin-test")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": True}


def test_admin_legacy_mapping_route_requires_trusted_confirmation(auth_app):
    app, _ = auth_app
    client = TestClient(app)

    target_response = client.post("/auth/admin/users", json={"user_id": "legacy-target", "source": "manual"})
    assert target_response.status_code == 200

    legacy_response = client.post(
        "/auth/admin/legacy-users",
        json={"legacy_user_id": "legacy-route-user", "display_name": "Legacy Route User"},
    )
    assert legacy_response.status_code == 200
    assert legacy_response.json()["user"]["source"] == "legacy"

    rejected_response = client.post(
        "/auth/admin/legacy-users/legacy-route-user/map",
        json={"target_user_id": "legacy-target", "trusted": False},
    )
    assert rejected_response.status_code == 409

    mapped_response = client.post(
        "/auth/admin/legacy-users/legacy-route-user/map",
        json={"target_user_id": "legacy-target", "trusted": True},
    )
    assert mapped_response.status_code == 200
    assert mapped_response.json() == {"mapped": {"tasks": 0, "files": 0}}
