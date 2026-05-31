from __future__ import annotations

from types import SimpleNamespace

import pytest

from auth.hardening import AuthConfigError, validate_auth_production_config
from auth.rate_limit import InMemoryRateLimiter, RateLimitError


class ProviderConfig:
    def __init__(self, *, enabled=True, client_id="client", client_secret="secret", redirect_uri="http://localhost/cb"):
        self.enabled = enabled
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri

    def resolved_client_id(self):
        return self._client_id

    def resolved_client_secret(self):
        return self._client_secret

    def resolved_redirect_uri(self):
        return self._redirect_uri


def settings(env="prod", *, required=True, secure=True, providers=None):
    return SimpleNamespace(
        env=env,
        auth=SimpleNamespace(
            required=required,
            cookie_secure=secure,
            providers=providers if providers is not None else {"google": ProviderConfig()},
        ),
    )


def test_production_auth_config_requires_auth_secure_cookie_and_provider_credentials():
    with pytest.raises(AuthConfigError) as exc_info:
        validate_auth_production_config(
            settings(
                required=False,
                secure=False,
                providers={"google": ProviderConfig(client_secret="", redirect_uri="")},
            )
        )

    message = str(exc_info.value)
    assert "auth.required" in message
    assert "auth.cookie_secure" in message
    assert "google client secret" in message
    assert "google redirect uri" in message


def test_production_auth_config_accepts_valid_enabled_provider():
    validate_auth_production_config(settings())


def test_rate_limiter_blocks_after_window_capacity():
    limiter = InMemoryRateLimiter(max_attempts=2, window_seconds=10)
    limiter.check("callback:127.0.0.1", now=100)
    limiter.check("callback:127.0.0.1", now=101)

    with pytest.raises(RateLimitError):
        limiter.check("callback:127.0.0.1", now=102)

    limiter.check("callback:127.0.0.1", now=112)
