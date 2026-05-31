from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol


class IdentityProviderError(RuntimeError):
    """Raised when an external identity provider cannot return a trusted identity."""


@dataclass(frozen=True)
class ExternalIdentityProfile:
    provider: str
    provider_subject: str
    provider_subject_type: str
    provider_app_id: Optional[str] = None
    provider_tenant_id: Optional[str] = None
    email: Optional[str] = None
    email_verified: bool = False
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    locale: Optional[str] = None
    raw_profile: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        provider = normalize_provider_name(self.provider)
        subject = str(self.provider_subject or "").strip()
        if not provider:
            raise ValueError("provider is required")
        if not subject:
            raise ValueError("provider_subject is required")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "provider_subject", subject)
        object.__setattr__(
            self,
            "provider_subject_type",
            str(self.provider_subject_type or "provider_user_id").strip() or "provider_user_id",
        )


class IdentityProviderAdapter(Protocol):
    provider: str

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        redirect_uri: str,
        scopes: Optional[List[str]] = None,
    ) -> str:
        ...

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
    ) -> Dict[str, Any]:
        ...

    async def normalize_identity(
        self,
        *,
        token_response: Dict[str, Any],
        nonce: Optional[str] = None,
    ) -> ExternalIdentityProfile:
        ...


def normalize_provider_name(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_")

