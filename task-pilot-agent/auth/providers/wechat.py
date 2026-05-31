from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

from auth.providers.base import ExternalIdentityProfile, IdentityProviderError


WECHAT_AUTHORIZATION_URL = "https://open.weixin.qq.com/connect/qrconnect"
WECHAT_TOKEN_URL = "https://api.weixin.qq.com/sns/oauth2/access_token"
WECHAT_USERINFO_URL = "https://api.weixin.qq.com/sns/userinfo"
WECHAT_DEFAULT_SCOPES = ["snsapi_login"]


class WeChatIdentityProvider:
    provider = "wechat"

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        authorization_url: str = WECHAT_AUTHORIZATION_URL,
        token_url: str = WECHAT_TOKEN_URL,
        userinfo_url: str = WECHAT_USERINFO_URL,
        scopes: Optional[List[str]] = None,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.authorization_endpoint = authorization_url
        self.token_endpoint = token_url
        self.userinfo_endpoint = userinfo_url
        self.scopes = scopes or list(WECHAT_DEFAULT_SCOPES)

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        redirect_uri: str,
        scopes: Optional[List[str]] = None,
    ) -> str:
        if not self.app_id:
            raise IdentityProviderError("wechat app_id is not configured")
        params = {
            "appid": self.app_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": ",".join(scopes or self.scopes),
            "state": state,
        }
        return f"{self.authorization_endpoint}?{urlencode(params)}#wechat_redirect"

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
    ) -> Dict[str, Any]:
        if not self.app_id or not self.app_secret:
            raise IdentityProviderError("wechat app credentials are not configured")
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                self.token_endpoint,
                params={
                    "appid": self.app_id,
                    "secret": self.app_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
        if response.status_code >= 400:
            raise IdentityProviderError(f"wechat token exchange failed: {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise IdentityProviderError("wechat token exchange returned invalid payload")
        if payload.get("errcode"):
            raise IdentityProviderError(f"wechat token exchange failed: {payload.get('errcode')}")
        return payload

    async def normalize_identity(
        self,
        *,
        token_response: Dict[str, Any],
        nonce: Optional[str] = None,
    ) -> ExternalIdentityProfile:
        profile_payload = dict(token_response)
        access_token = str(token_response.get("access_token") or "")
        openid = str(token_response.get("openid") or "")
        if access_token and openid and self.userinfo_endpoint:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    self.userinfo_endpoint,
                    params={"access_token": access_token, "openid": openid, "lang": "en"},
                    headers={"Accept": "application/json"},
                )
            if response.status_code < 400:
                userinfo = response.json()
                if isinstance(userinfo, dict) and not userinfo.get("errcode"):
                    profile_payload.update(userinfo)
        return self.profile_from_payload(profile_payload)

    def profile_from_payload(self, payload: Dict[str, Any]) -> ExternalIdentityProfile:
        unionid = str(payload.get("unionid") or "").strip()
        openid = str(payload.get("openid") or "").strip()
        if unionid:
            subject = unionid
            subject_type = "unionid"
        elif openid and self.app_id:
            subject = f"{self.app_id}:{openid}"
            subject_type = "appid_openid"
        else:
            raise IdentityProviderError("wechat subject missing")
        return ExternalIdentityProfile(
            provider=self.provider,
            provider_subject=subject,
            provider_subject_type=subject_type,
            provider_app_id=self.app_id or None,
            email=None,
            email_verified=False,
            display_name=str(payload.get("nickname") or "") or None,
            avatar_url=str(payload.get("headimgurl") or "") or None,
            locale=str(payload.get("language") or "") or None,
            raw_profile=_json_safe_payload(payload),
        )


def _json_safe_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    except TypeError:
        return {str(key): str(value) for key, value in payload.items()}
