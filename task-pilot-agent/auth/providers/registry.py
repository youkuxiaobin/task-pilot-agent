from __future__ import annotations

from typing import Dict

from auth.providers.base import IdentityProviderAdapter, normalize_provider_name
from auth.providers.google import GoogleIdentityProvider
from config.config import agentSettings


class ProviderRegistry:
    def enabled_provider_names(self) -> list[str]:
        return [
            name
            for name, config in agentSettings.auth.providers.items()
            if config.enabled
        ]

    def list_public_providers(self) -> list[Dict[str, str]]:
        items: list[Dict[str, str]] = []
        for name in self.enabled_provider_names():
            items.append({"provider": name, "label": _provider_label(name)})
        return items

    def get(self, provider: str) -> IdentityProviderAdapter:
        normalized = normalize_provider_name(provider)
        if normalized != "google":
            raise KeyError(f"unsupported provider: {provider}")
        config = agentSettings.auth.providers.get("google")
        if not config or not config.enabled:
            raise KeyError("google provider is not enabled")
        return GoogleIdentityProvider(
            client_id=config.resolved_client_id(),
            client_secret=config.resolved_client_secret(),
            authorization_url=config.authorize_url or "https://accounts.google.com/o/oauth2/v2/auth",
            token_url=config.token_url or "https://oauth2.googleapis.com/token",
            scopes=config.scopes or ["openid", "profile", "email"],
        )

    def redirect_uri(self, provider: str) -> str:
        normalized = normalize_provider_name(provider)
        if normalized != "google":
            raise KeyError(f"unsupported provider: {provider}")
        config = agentSettings.auth.providers.get("google")
        if not config:
            return ""
        return config.resolved_redirect_uri()


def _provider_label(provider: str) -> str:
    if provider == "google":
        return "Google"
    return provider.title()

