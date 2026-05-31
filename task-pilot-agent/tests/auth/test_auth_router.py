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

    import auth.models as models
    import auth.service as service
    import auth.router as router

    importlib.reload(models)
    importlib.reload(service)
    router = importlib.reload(router)

    router.agentSettings.auth.required = False
    router.agentSettings.auth.cookie_secure = False
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

