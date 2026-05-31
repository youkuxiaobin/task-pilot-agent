from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx
from authlib.jose import JsonWebKey, jwt

from auth.providers.base import ExternalIdentityProfile, IdentityProviderError, normalize_provider_name


OIDC_DEFAULT_SCOPES = ["openid", "profile", "email"]


class GenericOIDCIdentityProvider:
    def __init__(
        self,
        *,
        provider: str,
        client_id: str,
        client_secret: str,
        authorization_url: str,
        token_url: str,
        issuer: Optional[str] = None,
        jwks_url: Optional[str] = None,
        scopes: Optional[List[str]] = None,
        subject_strategy: str = "sub",
    ) -> None:
        self.provider = normalize_provider_name(provider)
        self.client_id = client_id
        self.client_secret = client_secret
        self.authorization_endpoint = authorization_url
        self.token_endpoint = token_url
        self.issuer = issuer or ""
        self.jwks_url = jwks_url or ""
        self.scopes = scopes or list(OIDC_DEFAULT_SCOPES)
        self.subject_strategy = subject_strategy or "sub"

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        redirect_uri: str,
        scopes: Optional[List[str]] = None,
    ) -> str:
        if not self.client_id:
            raise IdentityProviderError(f"{self.provider} client_id is not configured")
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes or self.scopes),
            "state": state,
            "nonce": nonce,
        }
        return f"{self.authorization_endpoint}?{urlencode(params)}"

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
    ) -> Dict[str, Any]:
        if not self.client_id or not self.client_secret:
            raise IdentityProviderError(f"{self.provider} client credentials are not configured")
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
            raise IdentityProviderError(f"{self.provider} token exchange failed: {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise IdentityProviderError(f"{self.provider} token exchange returned invalid payload")
        return payload

    async def normalize_identity(
        self,
        *,
        token_response: Dict[str, Any],
        nonce: Optional[str] = None,
    ) -> ExternalIdentityProfile:
        id_token_value = str(token_response.get("id_token") or "")
        if not id_token_value:
            raise IdentityProviderError(f"{self.provider} id_token missing")
        claims = await self.verify_id_token(id_token_value, nonce=nonce)
        return self.profile_from_claims(claims)

    async def verify_id_token(self, id_token_value: str, *, nonce: Optional[str] = None) -> Dict[str, Any]:
        if not self.jwks_url:
            raise IdentityProviderError(f"{self.provider} jwks_url is not configured")
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(self.jwks_url, headers={"Accept": "application/json"})
        if response.status_code >= 400:
            raise IdentityProviderError(f"{self.provider} jwks fetch failed: {response.status_code}")
        jwks = response.json()
        claims_options = {
            "aud": {"essential": True, "value": self.client_id},
        }
        try:
            decoded = jwt.decode(id_token_value, JsonWebKey.import_key_set(jwks), claims_options=claims_options)
            decoded.validate()
        except Exception as exc:
            raise IdentityProviderError(f"{self.provider} id_token verification failed") from exc
        claims = dict(decoded)
        if nonce is not None and claims.get("nonce") != nonce:
            raise IdentityProviderError(f"{self.provider} id_token nonce invalid")
        self._validate_issuer(claims)
        return claims

    def profile_from_claims(self, claims: Dict[str, Any]) -> ExternalIdentityProfile:
        subject = _subject_from_claims(claims, self.subject_strategy)
        if not subject:
            raise IdentityProviderError(f"{self.provider} subject missing")
        return ExternalIdentityProfile(
            provider=self.provider,
            provider_subject=subject,
            provider_subject_type=self.subject_strategy or "sub",
            provider_app_id=self.client_id or None,
            provider_tenant_id=str(claims.get("tid") or claims.get("tenant") or "") or None,
            email=str(claims.get("email") or claims.get("preferred_username") or "") or None,
            email_verified=bool(claims.get("email_verified") or False),
            display_name=str(claims.get("name") or "") or None,
            avatar_url=str(claims.get("picture") or "") or None,
            locale=str(claims.get("locale") or "") or None,
            raw_profile=_json_safe_claims(claims),
        )

    def _validate_issuer(self, claims: Dict[str, Any]) -> None:
        if not self.issuer:
            return
        issuer = str(claims.get("iss") or "")
        if not issuer:
            raise IdentityProviderError(f"{self.provider} id_token issuer missing")
        if issuer == self.issuer or issuer.startswith(f"{self.issuer.rstrip('/')}/"):
            return
        raise IdentityProviderError(f"{self.provider} id_token issuer invalid")


def decode_unverified_claims(id_token_value: str) -> Dict[str, Any]:
    parts = str(id_token_value or "").split(".")
    if len(parts) < 2:
        raise IdentityProviderError("id_token is not a JWT")
    payload = parts[1]
    padded = payload + "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        claims = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise IdentityProviderError("id_token payload is invalid") from exc
    if not isinstance(claims, dict):
        raise IdentityProviderError("id_token payload is invalid")
    return claims


def _subject_from_claims(claims: Dict[str, Any], strategy: str) -> str:
    normalized = str(strategy or "sub").strip()
    if normalized == "tenant_oid":
        tenant_id = str(claims.get("tid") or "").strip()
        object_id = str(claims.get("oid") or "").strip()
        if tenant_id and object_id:
            return f"{tenant_id}:{object_id}"
    return str(claims.get("sub") or "").strip()


def _json_safe_claims(claims: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(json.dumps(claims, ensure_ascii=False, default=str))
    except TypeError:
        return {str(key): str(value) for key, value in claims.items()}
