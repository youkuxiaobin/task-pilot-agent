from __future__ import annotations

from typing import Dict

from auth.providers.base import IdentityProviderAdapter, normalize_provider_name
from auth.providers.google import GoogleIdentityProvider
from auth.providers.microsoft import MicrosoftIdentityProvider
from auth.providers.oidc import GenericOIDCIdentityProvider
from auth.providers.wechat import WeChatIdentityProvider
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
        config = agentSettings.auth.providers.get(normalized)
        if not config or not config.enabled:
            raise KeyError(f"{normalized} provider is not enabled")
        if normalized == "google":
            return GoogleIdentityProvider(
                client_id=config.resolved_client_id(),
                client_secret=config.resolved_client_secret(),
                authorization_url=config.authorize_url or "https://accounts.google.com/o/oauth2/v2/auth",
                token_url=config.token_url or "https://oauth2.googleapis.com/token",
                scopes=config.scopes or ["openid", "profile", "email"],
            )
        if normalized == "microsoft":
            return MicrosoftIdentityProvider(
                client_id=config.resolved_client_id(),
                client_secret=config.resolved_client_secret(),
                authorization_url=config.authorize_url or "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                token_url=config.token_url or "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                jwks_url=config.jwks_url or "https://login.microsoftonline.com/common/discovery/v2.0/keys",
                scopes=config.scopes or ["openid", "profile", "email"],
            )
        if normalized == "wechat":
            return WeChatIdentityProvider(
                app_id=config.resolved_client_id(),
                app_secret=config.resolved_client_secret(),
                authorization_url=config.authorize_url or "https://open.weixin.qq.com/connect/qrconnect",
                token_url=config.token_url or "https://api.weixin.qq.com/sns/oauth2/access_token",
                userinfo_url=config.userinfo_url or "https://api.weixin.qq.com/sns/userinfo",
                scopes=config.scopes or ["snsapi_login"],
            )
        if config.protocol == "oidc":
            return GenericOIDCIdentityProvider(
                provider=normalized,
                client_id=config.resolved_client_id(),
                client_secret=config.resolved_client_secret(),
                authorization_url=config.authorize_url or "",
                token_url=config.token_url or "",
                issuer=config.issuer,
                jwks_url=config.jwks_url,
                scopes=config.scopes or ["openid", "profile", "email"],
                subject_strategy=config.subject_strategy or "sub",
            )
        raise KeyError(f"unsupported provider: {provider}")

    def redirect_uri(self, provider: str) -> str:
        normalized = normalize_provider_name(provider)
        config = agentSettings.auth.providers.get(normalized)
        if not config:
            return ""
        return config.resolved_redirect_uri()


def _provider_label(provider: str) -> str:
    if provider == "google":
        return "Google"
    if provider == "microsoft":
        return "Microsoft"
    if provider == "wechat":
        return "WeChat"
    return provider.title()
