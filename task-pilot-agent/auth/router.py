from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from auth.dependencies import auth_service, get_optional_current_user, require_current_user
from auth.models import OAuthStatePurpose, TaskPilotUser
from auth.providers.base import IdentityProviderError
from auth.providers.registry import ProviderRegistry
from auth.security import hash_token
from auth.service import AuthError, AuthService
from config.config import agentSettings


auth_router = APIRouter()
provider_registry = ProviderRegistry()


@auth_router.get("/providers")
async def list_auth_providers() -> dict:
    return {"items": provider_registry.list_public_providers()}


@auth_router.get("/me")
async def get_me(
    request: Request,
    service: AuthService = Depends(auth_service),
    current_user: Optional[TaskPilotUser] = Depends(get_optional_current_user),
) -> dict:
    if current_user:
        return {
            "authenticated": True,
            "authRequired": agentSettings.auth.required,
            "user": service.serialize_user(current_user),
            "providers": provider_registry.list_public_providers(),
        }
    if not agentSettings.auth.required:
        dev_user = service.ensure_dev_user()
        return {
            "authenticated": False,
            "authRequired": False,
            "user": service.serialize_user(dev_user),
            "providers": provider_registry.list_public_providers(),
        }
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")


@auth_router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    token = request.cookies.get(agentSettings.auth.session_cookie_name)
    if token:
        AuthService().revoke_session(token)
    response = JSONResponse({"loggedOut": True})
    response.delete_cookie(agentSettings.auth.session_cookie_name, path="/")
    return response


@auth_router.get("/{provider}/login", name="provider_login")
async def provider_login(
    provider: str,
    request: Request,
    redirect_after: Optional[str] = Query(default="/agent/web/autoagent"),
    service: AuthService = Depends(auth_service),
) -> RedirectResponse:
    provider_name = _normalize_provider_or_404(provider)
    identity_provider = _get_provider_or_503(provider_name)
    redirect_uri = _provider_redirect_uri(request, provider_name)
    oauth_state = service.create_oauth_state(
        provider=provider_name,
        purpose=OAuthStatePurpose.LOGIN,
        redirect_after=redirect_after,
    )
    try:
        url = identity_provider.authorization_url(
            state=oauth_state.state,
            nonce=oauth_state.nonce,
            redirect_uri=redirect_uri,
        )
    except IdentityProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response = RedirectResponse(url)
    _set_cookie(
        response,
        _oauth_nonce_cookie_name(oauth_state.state),
        oauth_state.nonce,
        max_age=600,
        http_only=True,
    )
    return response


@auth_router.get("/{provider}/callback", name="provider_callback")
async def provider_callback(
    provider: str,
    request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    service: AuthService = Depends(auth_service),
) -> RedirectResponse:
    provider_name = _normalize_provider_or_404(provider)
    if not code or not state:
        raise HTTPException(status_code=400, detail=f"missing {provider_name} callback parameters")
    nonce_cookie = _oauth_nonce_cookie_name(state)
    nonce = request.cookies.get(nonce_cookie)
    if not nonce:
        raise HTTPException(status_code=400, detail="oauth nonce missing")

    identity_provider = _get_provider_or_503(provider_name)
    try:
        oauth_state = service.consume_oauth_state(
            state=state,
            nonce=nonce,
            provider=provider_name,
            purpose=OAuthStatePurpose.LOGIN,
        )
        token_response = await identity_provider.exchange_code(
            code=code,
            redirect_uri=_provider_redirect_uri(request, provider_name),
        )
        profile = await identity_provider.normalize_identity(token_response=token_response, nonce=nonce)
        user = service.create_or_update_external_user(profile)
        created_session = service.create_session(user.user_id, metadata={"provider": provider_name})
    except (AuthError, IdentityProviderError) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    response = RedirectResponse(oauth_state.redirect_after or "/agent/web/autoagent")
    _set_cookie(
        response,
        agentSettings.auth.session_cookie_name,
        created_session.session_token,
        max_age=agentSettings.auth.session_ttl_seconds,
        http_only=True,
    )
    response.delete_cookie(nonce_cookie, path="/")
    return response


@auth_router.get("/whoami")
async def whoami(current_user: TaskPilotUser = Depends(require_current_user)) -> dict:
    return {"userId": current_user.user_id}


def _normalize_provider_or_404(provider: str) -> str:
    normalized = str(provider or "").strip().lower().replace("-", "_")
    if not normalized or normalized in {"providers", "me", "logout", "whoami"}:
        raise HTTPException(status_code=404, detail="provider not found")
    return normalized


def _get_provider_or_503(provider: str):
    try:
        return provider_registry.get(provider)
    except KeyError as exc:
        raise HTTPException(status_code=503, detail=f"{provider} provider is not enabled") from exc


def _provider_redirect_uri(request: Request, provider: str) -> str:
    configured = provider_registry.redirect_uri(provider)
    if configured:
        return configured
    return str(request.url_for("provider_callback", provider=provider))


def _oauth_nonce_cookie_name(state: str) -> str:
    return f"tpa_oauth_nonce_{hash_token(state)[:16]}"


def _set_cookie(
    response: Response,
    name: str,
    value: str,
    *,
    max_age: int,
    http_only: bool,
) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=http_only,
        secure=agentSettings.auth.cookie_secure,
        samesite="lax",
        path="/",
    )
