from __future__ import annotations

from typing import Any

from config.config import AgentSettings


class AuthConfigError(RuntimeError):
    pass


def validate_auth_production_config(settings: AgentSettings) -> None:
    env = str(settings.env or "").strip().lower()
    if env not in {"prod", "production"}:
        return

    errors: list[str] = []
    if not settings.auth.required:
        errors.append("auth.required must be true in production")
    if not settings.auth.cookie_secure:
        errors.append("auth.cookie_secure must be true in production")

    enabled_providers = [
        (name, config)
        for name, config in settings.auth.providers.items()
        if config.enabled
    ]
    if not enabled_providers:
        errors.append("at least one auth provider must be enabled in production")

    for name, config in enabled_providers:
        if not config.resolved_client_id():
            errors.append(f"{name} client id is required")
        if not config.resolved_client_secret():
            errors.append(f"{name} client secret is required")
        if not config.resolved_redirect_uri():
            errors.append(f"{name} redirect uri is required")

    if errors:
        raise AuthConfigError("; ".join(errors))


def public_auth_config_summary(settings: AgentSettings) -> dict[str, Any]:
    return {
        "required": settings.auth.required,
        "cookieSecure": settings.auth.cookie_secure,
        "enabledProviders": [
            name
            for name, config in settings.auth.providers.items()
            if config.enabled
        ],
    }
