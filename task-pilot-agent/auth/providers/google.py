from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from auth.providers.base import ExternalIdentityProfile, IdentityProviderError


GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
GOOGLE_DEFAULT_SCOPES = ["openid", "profile", "email"]


class GoogleIdentityProvider:
    provider = "google"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        authorization_url: str = GOOGLE_AUTHORIZATION_URL,
        token_url: str = GOOGLE_TOKEN_URL,
        scopes: Optional[List[str]] = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.authorization_endpoint = authorization_url
        self.token_endpoint = token_url
        self.scopes = scopes or list(GOOGLE_DEFAULT_SCOPES)

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        redirect_uri: str,
        scopes: Optional[List[str]] = None,
    ) -> str:
        if not self.client_id:
            raise IdentityProviderError("google client_id is not configured")
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes or self.scopes),
            "state": state,
            "nonce": nonce,
            "access_type": "online",
            "include_granted_scopes": "true",
            "prompt": "select_account",
        }
        return f"{self.authorization_endpoint}?{urlencode(params)}"

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
    ) -> Dict[str, Any]:
        if not self.client_id or not self.client_secret:
            raise IdentityProviderError("google client credentials are not configured")
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                self.token_endpoint,
                data={
                    "code": code,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
        if response.status_code >= 400:
            raise IdentityProviderError(f"google token exchange failed: {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise IdentityProviderError("google token exchange returned invalid payload")
        return payload

    async def normalize_identity(
        self,
        *,
        token_response: Dict[str, Any],
        nonce: Optional[str] = None,
    ) -> ExternalIdentityProfile:
        id_token_value = str(token_response.get("id_token") or "")
        if not id_token_value:
            raise IdentityProviderError("google id_token missing")
        claims = self.verify_id_token(id_token_value, nonce=nonce)
        return self.profile_from_claims(claims)

    def verify_id_token(self, id_token_value: str, *, nonce: Optional[str] = None) -> Dict[str, Any]:
        try:
            claims = id_token.verify_oauth2_token(
                id_token_value,
                google_requests.Request(),
                self.client_id,
            )
        except Exception as exc:  # pragma: no cover - network/cert path exercised in integration tests
            raise IdentityProviderError("google id_token verification failed") from exc
        if claims.get("iss") not in GOOGLE_ISSUERS:
            raise IdentityProviderError("google id_token issuer invalid")
        if nonce is not None and claims.get("nonce") != nonce:
            raise IdentityProviderError("google id_token nonce invalid")
        return dict(claims)

    def profile_from_claims(self, claims: Dict[str, Any]) -> ExternalIdentityProfile:
        subject = str(claims.get("sub") or "").strip()
        if not subject:
            raise IdentityProviderError("google subject missing")
        raw_profile = _json_safe_claims(claims)
        return ExternalIdentityProfile(
            provider=self.provider,
            provider_subject=subject,
            provider_subject_type="sub",
            provider_app_id=self.client_id or None,
            provider_tenant_id=str(claims.get("hd") or "") or None,
            email=str(claims.get("email") or "") or None,
            email_verified=bool(claims.get("email_verified") or False),
            display_name=str(claims.get("name") or "") or None,
            avatar_url=str(claims.get("picture") or "") or None,
            locale=str(claims.get("locale") or "") or None,
            raw_profile=raw_profile,
        )


def _json_safe_claims(claims: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(json.dumps(claims, ensure_ascii=False, default=str))
    except TypeError:
        return {str(key): str(value) for key, value in claims.items()}

