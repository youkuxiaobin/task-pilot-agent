from __future__ import annotations

from typing import List, Optional

from auth.providers.oidc import GenericOIDCIdentityProvider


MICROSOFT_AUTHORIZATION_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
MICROSOFT_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
MICROSOFT_JWKS_URL = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
MICROSOFT_DEFAULT_SCOPES = ["openid", "profile", "email"]


class MicrosoftIdentityProvider(GenericOIDCIdentityProvider):
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        authorization_url: str = MICROSOFT_AUTHORIZATION_URL,
        token_url: str = MICROSOFT_TOKEN_URL,
        jwks_url: str = MICROSOFT_JWKS_URL,
        scopes: Optional[List[str]] = None,
    ) -> None:
        super().__init__(
            provider="microsoft",
            client_id=client_id,
            client_secret=client_secret,
            authorization_url=authorization_url,
            token_url=token_url,
            issuer="https://login.microsoftonline.com",
            jwks_url=jwks_url,
            scopes=scopes or list(MICROSOFT_DEFAULT_SCOPES),
            subject_strategy="tenant_oid",
        )
