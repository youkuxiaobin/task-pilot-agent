from __future__ import annotations

from pathlib import Path

import yaml

from config.config import AgentSettings, _normalize_auth_config_aliases


def test_auth_config_accepts_env_style_keys_from_yaml():
    data = yaml.safe_load((Path(__file__).resolve().parents[3] / "config" / "config.local.yaml").read_text())
    data["auth"] = {
        "AUTH_REQUIRED": True,
        "AUTH_COOKIE_SECURE": False,
        "AUTH_SESSION_COOKIE_NAME": "local_session",
        "AUTH_SESSION_TTL_SECONDS": 120,
        "AUTH_DEV_USER_ID": "local-dev",
        "AUTH_ADMIN_USER_IDS": "admin-a,admin-b",
        "GOOGLE_ENABLED": True,
        "GOOGLE_CLIENT_ID": "yaml-google-client",
        "GOOGLE_CLIENT_SECRET": "yaml-google-secret",
        "GOOGLE_REDIRECT_URI": "http://127.0.0.1:9010/auth/google/callback",
        "GOOGLE_SCOPES": "openid,profile,email",
    }

    settings = AgentSettings(**_normalize_auth_config_aliases(data))

    assert settings.auth.required is True
    assert settings.auth.cookie_secure is False
    assert settings.auth.session_cookie_name == "local_session"
    assert settings.auth.session_ttl_seconds == 120
    assert settings.auth.dev_user_id == "local-dev"
    assert settings.auth.admin_user_ids == ["admin-a", "admin-b"]

    google = settings.auth.providers["google"]
    assert google.enabled is True
    assert google.resolved_client_id() == "yaml-google-client"
    assert google.resolved_client_secret() == "yaml-google-secret"
    assert google.resolved_redirect_uri() == "http://127.0.0.1:9010/auth/google/callback"
    assert google.scopes == ["openid", "profile", "email"]


def test_config_local_enables_google_login_with_env_backed_credentials():
    data = yaml.safe_load((Path(__file__).resolve().parents[3] / "config" / "config.local.yaml").read_text())
    settings = AgentSettings(**_normalize_auth_config_aliases(data))

    assert settings.auth.required is True
    assert settings.auth.cookie_secure is False

    google = settings.auth.providers["google"]
    assert google.enabled is True
    assert google.client_id_env == "GOOGLE_CLIENT_ID"
    assert google.client_secret_env == "GOOGLE_CLIENT_SECRET"
    assert google.redirect_uri_env == "GOOGLE_REDIRECT_URI"
    assert google.scopes == ["openid", "profile", "email"]
