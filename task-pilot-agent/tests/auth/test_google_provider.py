from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from auth.providers.base import IdentityProviderError
from auth.providers.google import GoogleIdentityProvider


def test_google_authorization_url_contains_required_oidc_fields():
    provider = GoogleIdentityProvider(client_id="client-id", client_secret="secret")
    url = provider.authorization_url(
        state="state-value",
        nonce="nonce-value",
        redirect_uri="https://taskpilot.example/auth/google/callback",
    )

    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert parsed.netloc == "accounts.google.com"
    assert params["client_id"] == ["client-id"]
    assert params["response_type"] == ["code"]
    assert params["state"] == ["state-value"]
    assert params["nonce"] == ["nonce-value"]
    assert "openid" in params["scope"][0]
    assert "profile" in params["scope"][0]
    assert "email" in params["scope"][0]


def test_google_profile_from_claims_uses_sub_as_provider_subject():
    provider = GoogleIdentityProvider(client_id="client-id", client_secret="secret")
    profile = provider.profile_from_claims(
        {
            "iss": "https://accounts.google.com",
            "sub": "stable-google-sub",
            "email": "person@example.com",
            "email_verified": True,
            "name": "Person",
            "picture": "https://example.com/person.png",
            "locale": "en",
            "hd": "example.com",
        }
    )

    assert profile.provider == "google"
    assert profile.provider_subject == "stable-google-sub"
    assert profile.provider_subject_type == "sub"
    assert profile.provider_tenant_id == "example.com"
    assert profile.email == "person@example.com"
    assert profile.email_verified is True


def test_google_profile_requires_sub():
    provider = GoogleIdentityProvider(client_id="client-id", client_secret="secret")
    with pytest.raises(IdentityProviderError):
        provider.profile_from_claims({"email": "person@example.com"})

