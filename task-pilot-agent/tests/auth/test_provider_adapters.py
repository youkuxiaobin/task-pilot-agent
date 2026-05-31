from urllib.parse import parse_qs, urlparse

import pytest

from auth.providers.base import IdentityProviderError
from auth.providers.microsoft import MicrosoftIdentityProvider
from auth.providers.oidc import GenericOIDCIdentityProvider
from auth.providers.registry import ProviderRegistry
from auth.providers.wechat import WeChatIdentityProvider


def test_generic_oidc_authorization_url_contains_oidc_fields():
    provider = GenericOIDCIdentityProvider(
        provider="enterprise",
        client_id="client-id",
        client_secret="secret",
        authorization_url="https://idp.example/authorize",
        token_url="https://idp.example/token",
    )

    url = provider.authorization_url(
        state="state-value",
        nonce="nonce-value",
        redirect_uri="https://taskpilot.example/auth/enterprise/callback",
    )

    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert parsed.netloc == "idp.example"
    assert params["client_id"] == ["client-id"]
    assert params["response_type"] == ["code"]
    assert params["state"] == ["state-value"]
    assert params["nonce"] == ["nonce-value"]
    assert "openid" in params["scope"][0]


def test_microsoft_profile_uses_tenant_and_object_id_as_subject():
    provider = MicrosoftIdentityProvider(client_id="client-id", client_secret="secret")

    profile = provider.profile_from_claims(
        {
            "tid": "tenant-1",
            "oid": "object-1",
            "sub": "pairwise-sub",
            "preferred_username": "person@example.com",
            "name": "MS Person",
        }
    )

    assert profile.provider == "microsoft"
    assert profile.provider_subject == "tenant-1:object-1"
    assert profile.provider_subject_type == "tenant_oid"
    assert profile.provider_tenant_id == "tenant-1"
    assert profile.email == "person@example.com"


def test_wechat_profile_prefers_unionid():
    provider = WeChatIdentityProvider(app_id="wx-app", app_secret="secret")

    profile = provider.profile_from_payload(
        {
            "openid": "openid-1",
            "unionid": "union-1",
            "nickname": "WeChat User",
            "headimgurl": "https://example.com/a.png",
        }
    )

    assert profile.provider == "wechat"
    assert profile.provider_subject == "union-1"
    assert profile.provider_subject_type == "unionid"
    assert profile.provider_app_id == "wx-app"
    assert profile.display_name == "WeChat User"


def test_wechat_profile_falls_back_to_appid_openid():
    provider = WeChatIdentityProvider(app_id="wx-app", app_secret="secret")

    profile = provider.profile_from_payload({"openid": "openid-1"})

    assert profile.provider_subject == "wx-app:openid-1"
    assert profile.provider_subject_type == "appid_openid"


def test_wechat_profile_requires_stable_subject():
    provider = WeChatIdentityProvider(app_id="wx-app", app_secret="secret")
    with pytest.raises(IdentityProviderError):
        provider.profile_from_payload({})


def test_registry_only_lists_enabled_providers(monkeypatch):
    from auth.providers import registry as registry_module

    monkeypatch.setattr(registry_module.agentSettings.auth.providers["google"], "enabled", True)
    monkeypatch.setattr(registry_module.agentSettings.auth.providers["microsoft"], "enabled", False)
    monkeypatch.setattr(registry_module.agentSettings.auth.providers["wechat"], "enabled", True)

    providers = ProviderRegistry().list_public_providers()

    assert providers == [
        {"provider": "google", "label": "Google"},
        {"provider": "wechat", "label": "WeChat"},
    ]
