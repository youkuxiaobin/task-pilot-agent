from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from auth.dependencies import auth_service, get_optional_current_user, require_admin_user, require_current_user
from auth.models import OAuthStatePurpose, TaskPilotUser
from auth.providers.base import IdentityProviderError
from auth.providers.registry import ProviderRegistry
from auth.rate_limit import InMemoryRateLimiter, RateLimitError
from auth.security import hash_token
from auth.service import (
    AuthAuditEventType,
    AuthConflictError,
    AuthDisabledError,
    AuthError,
    AuthNotFoundError,
    AuthService,
)
from config.config import agentSettings


auth_router = APIRouter()
provider_registry = ProviderRegistry()
callback_rate_limiter = InMemoryRateLimiter(max_attempts=30, window_seconds=60)
session_validation_rate_limiter = InMemoryRateLimiter(max_attempts=120, window_seconds=60)


class UpdateUserReq(BaseModel):
    primary_email: Optional[str] = Field(default=None)
    display_name: Optional[str] = Field(default=None)
    avatar_url: Optional[str] = Field(default=None)
    locale: Optional[str] = Field(default=None)
    metadata: Optional[dict] = Field(default=None)


class CreateUserReq(UpdateUserReq):
    user_id: Optional[str] = Field(default=None)
    source: str = Field(default="manual")


class CreateLegacyUserReq(BaseModel):
    legacy_user_id: str
    primary_email: Optional[str] = None
    display_name: Optional[str] = None
    metadata: Optional[dict] = None


class MapLegacyUserReq(BaseModel):
    target_user_id: str
    trusted: bool = False


@auth_router.get("/providers")
async def list_auth_providers() -> dict:
    return {"items": provider_registry.list_public_providers()}


@auth_router.get("/me")
async def get_me(
    request: Request,
    service: AuthService = Depends(auth_service),
    current_user: Optional[TaskPilotUser] = Depends(get_optional_current_user),
) -> dict:
    _check_rate_limit(session_validation_rate_limiter, request, "session")
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
async def logout(
    request: Request,
    service: AuthService = Depends(auth_service),
) -> JSONResponse:
    token = request.cookies.get(agentSettings.auth.session_cookie_name)
    user = service.get_user_by_session_token(token) if token else None
    if token:
        service.revoke_session(token)
    if user:
        service.record_audit_event(
            AuthAuditEventType.LOGOUT,
            actor_user_id=user.user_id,
            target_user_id=user.user_id,
            request=request,
        )
    response = JSONResponse({"loggedOut": True})
    response.delete_cookie(agentSettings.auth.session_cookie_name, path="/")
    return response


@auth_router.post("/logout-all")
async def logout_all(
    request: Request,
    service: AuthService = Depends(auth_service),
    current_user: TaskPilotUser = Depends(require_current_user),
) -> JSONResponse:
    revoked_count = service.revoke_all_sessions(current_user.user_id)
    service.record_audit_event(
        AuthAuditEventType.LOGOUT_ALL,
        actor_user_id=current_user.user_id,
        target_user_id=current_user.user_id,
        request=request,
        metadata={"revokedSessions": revoked_count},
    )
    response = JSONResponse({"revokedSessions": revoked_count})
    response.delete_cookie(agentSettings.auth.session_cookie_name, path="/")
    return response


@auth_router.get("/users/me")
async def get_current_user_profile(
    service: AuthService = Depends(auth_service),
    current_user: TaskPilotUser = Depends(require_current_user),
) -> dict:
    return {
        "user": service.serialize_user(current_user),
        "identities": [service.serialize_identity(item) for item in service.list_identities(current_user.user_id)],
    }


@auth_router.patch("/users/me")
async def update_current_user_profile(
    request: Request,
    req: UpdateUserReq,
    service: AuthService = Depends(auth_service),
    current_user: TaskPilotUser = Depends(require_current_user),
) -> dict:
    try:
        user = service.update_user(current_user.user_id, _user_updates(req))
    except AuthError as exc:
        raise _auth_http_error(exc) from exc
    service.record_audit_event(
        AuthAuditEventType.USER_UPDATED,
        actor_user_id=current_user.user_id,
        target_user_id=current_user.user_id,
        request=request,
        metadata={"fields": sorted(_user_updates(req).keys())},
    )
    return {"user": service.serialize_user(user)}


@auth_router.get("/users/me/identities")
async def list_current_user_identities(
    service: AuthService = Depends(auth_service),
    current_user: TaskPilotUser = Depends(require_current_user),
) -> dict:
    return {"items": [service.serialize_identity(item) for item in service.list_identities(current_user.user_id)]}


@auth_router.delete("/users/me/identities/{identity_id}")
async def unbind_current_user_identity(
    request: Request,
    identity_id: str,
    service: AuthService = Depends(auth_service),
    current_user: TaskPilotUser = Depends(require_current_user),
) -> dict:
    existing = service.get_user_identity(current_user.user_id, identity_id)
    if not existing:
        raise HTTPException(status_code=404, detail="identity not found")
    try:
        identity = service.unbind_identity(current_user.user_id, identity_id)
    except AuthError as exc:
        raise _auth_http_error(exc) from exc
    service.record_audit_event(
        AuthAuditEventType.IDENTITY_UNBOUND,
        actor_user_id=current_user.user_id,
        target_user_id=current_user.user_id,
        provider=existing.provider,
        request=request,
        metadata={"identityId": identity.identity_id},
    )
    return {"identity": service.serialize_identity(identity)}


@auth_router.get("/admin/users")
async def list_users_admin(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    source: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: AuthService = Depends(auth_service),
    admin_user: TaskPilotUser = Depends(require_admin_user),
) -> dict:
    users = service.list_users(status=status_filter, source=source, keyword=keyword, limit=limit, offset=offset)
    return {"items": [service.serialize_user(user) for user in users]}


@auth_router.post("/admin/users")
async def create_user_admin(
    request: Request,
    req: CreateUserReq,
    service: AuthService = Depends(auth_service),
    admin_user: TaskPilotUser = Depends(require_admin_user),
) -> dict:
    try:
        user = service.create_user(
            user_id=req.user_id,
            primary_email=req.primary_email,
            display_name=req.display_name,
            avatar_url=req.avatar_url,
            locale=req.locale,
            source=req.source,
            metadata=req.metadata,
        )
    except AuthError as exc:
        raise _auth_http_error(exc) from exc
    service.record_audit_event(
        AuthAuditEventType.USER_CREATED,
        actor_user_id=admin_user.user_id,
        target_user_id=user.user_id,
        request=request,
        metadata={"source": user.source},
    )
    return {"user": service.serialize_user(user)}


@auth_router.patch("/admin/users/{user_id}")
async def update_user_admin(
    request: Request,
    user_id: str,
    req: UpdateUserReq,
    service: AuthService = Depends(auth_service),
    admin_user: TaskPilotUser = Depends(require_admin_user),
) -> dict:
    try:
        user = service.update_user(user_id, _user_updates(req))
    except AuthError as exc:
        raise _auth_http_error(exc) from exc
    service.record_audit_event(
        AuthAuditEventType.USER_UPDATED,
        actor_user_id=admin_user.user_id,
        target_user_id=user.user_id,
        request=request,
        metadata={"fields": sorted(_user_updates(req).keys())},
    )
    return {"user": service.serialize_user(user)}


@auth_router.post("/admin/users/{user_id}/disable")
async def disable_user_admin(
    request: Request,
    user_id: str,
    service: AuthService = Depends(auth_service),
    admin_user: TaskPilotUser = Depends(require_admin_user),
) -> dict:
    disabled = service.disable_user(user_id)
    if disabled:
        service.record_audit_event(
            AuthAuditEventType.USER_DISABLED,
            actor_user_id=admin_user.user_id,
            target_user_id=user_id,
            request=request,
        )
    return {"disabled": disabled}


@auth_router.delete("/admin/users/{user_id}")
async def delete_user_admin(
    request: Request,
    user_id: str,
    service: AuthService = Depends(auth_service),
    admin_user: TaskPilotUser = Depends(require_admin_user),
) -> dict:
    deleted = service.soft_delete_user(user_id)
    if deleted:
        service.record_audit_event(
            AuthAuditEventType.USER_DELETED,
            actor_user_id=admin_user.user_id,
            target_user_id=user_id,
            request=request,
        )
    return {"deleted": deleted}


@auth_router.post("/admin/legacy-users")
async def create_legacy_user_admin(
    request: Request,
    req: CreateLegacyUserReq,
    service: AuthService = Depends(auth_service),
    admin_user: TaskPilotUser = Depends(require_admin_user),
) -> dict:
    try:
        user = service.ensure_legacy_user(
            req.legacy_user_id,
            primary_email=req.primary_email,
            display_name=req.display_name,
            metadata=req.metadata,
        )
    except (AuthError, ValueError) as exc:
        raise _auth_http_error(exc) from exc
    service.record_audit_event(
        AuthAuditEventType.USER_CREATED,
        actor_user_id=admin_user.user_id,
        target_user_id=user.user_id,
        request=request,
        metadata={"source": "legacy"},
    )
    return {"user": service.serialize_user(user)}


@auth_router.post("/admin/legacy-users/{legacy_user_id}/map")
async def map_legacy_user_admin(
    request: Request,
    legacy_user_id: str,
    req: MapLegacyUserReq,
    service: AuthService = Depends(auth_service),
    admin_user: TaskPilotUser = Depends(require_admin_user),
) -> dict:
    try:
        result = service.map_legacy_user_records(
            legacy_user_id=legacy_user_id,
            target_user_id=req.target_user_id,
            trusted=req.trusted,
        )
    except (AuthError, ValueError) as exc:
        raise _auth_http_error(exc) from exc
    service.record_audit_event(
        AuthAuditEventType.LEGACY_USER_MAPPED,
        actor_user_id=admin_user.user_id,
        target_user_id=req.target_user_id,
        request=request,
        metadata={"legacyUserId": legacy_user_id, **result},
    )
    return {"mapped": result}


@auth_router.get("/admin/audit-events")
async def list_audit_events_admin(
    actor_user_id: Optional[str] = Query(default=None),
    target_user_id: Optional[str] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
    provider: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: AuthService = Depends(auth_service),
    admin_user: TaskPilotUser = Depends(require_admin_user),
) -> dict:
    events = service.list_audit_events(
        actor_user_id=actor_user_id,
        target_user_id=target_user_id,
        event_type=event_type,
        provider=provider,
        limit=limit,
        offset=offset,
    )
    return {"items": [service.serialize_audit_event(event) for event in events]}


@auth_router.post("/admin/cleanup")
async def cleanup_auth_records_admin(
    service: AuthService = Depends(auth_service),
    admin_user: TaskPilotUser = Depends(require_admin_user),
) -> dict:
    return {
        "expiredSessions": service.cleanup_expired_sessions(),
        "expiredOAuthStates": service.cleanup_expired_oauth_states(),
    }


@auth_router.post("/{provider}/link", name="provider_link")
async def provider_link(
    provider: str,
    request: Request,
    redirect_after: Optional[str] = Query(default="/agent/web/autoagent"),
    service: AuthService = Depends(auth_service),
    current_user: TaskPilotUser = Depends(require_current_user),
) -> RedirectResponse:
    provider_name = _normalize_provider_or_404(provider)
    identity_provider = _get_provider_or_503(provider_name)
    redirect_uri = _provider_redirect_uri(request, provider_name)
    oauth_state = service.create_oauth_state(
        provider=provider_name,
        purpose=OAuthStatePurpose.LINK,
        user_id=current_user.user_id,
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


@auth_router.delete("/{provider}/link/{identity_id}")
async def unlink_provider_identity(
    provider: str,
    identity_id: str,
    request: Request,
    service: AuthService = Depends(auth_service),
    current_user: TaskPilotUser = Depends(require_current_user),
) -> dict:
    provider_name = _normalize_provider_or_404(provider)
    existing = service.get_user_identity(current_user.user_id, identity_id)
    if not existing or existing.provider != provider_name:
        raise HTTPException(status_code=404, detail="identity not found")
    try:
        identity = service.unbind_identity(current_user.user_id, identity_id)
    except AuthError as exc:
        raise _auth_http_error(exc) from exc
    service.record_audit_event(
        AuthAuditEventType.IDENTITY_UNBOUND,
        actor_user_id=current_user.user_id,
        target_user_id=current_user.user_id,
        provider=provider_name,
        request=request,
        metadata={"identityId": identity.identity_id},
    )
    return {"identity": service.serialize_identity(identity)}


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
    current_user: Optional[TaskPilotUser] = Depends(get_optional_current_user),
) -> RedirectResponse:
    provider_name = _normalize_provider_or_404(provider)
    _check_rate_limit(callback_rate_limiter, request, f"callback:{provider_name}")
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
        )
        token_response = await identity_provider.exchange_code(
            code=code,
            redirect_uri=_provider_redirect_uri(request, provider_name),
        )
        profile = await identity_provider.normalize_identity(token_response=token_response, nonce=nonce)
    except (AuthError, IdentityProviderError) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    if oauth_state.purpose == OAuthStatePurpose.LINK:
        if not current_user and not agentSettings.auth.required:
            current_user = service.ensure_dev_user()
        if not current_user or current_user.user_id != oauth_state.user_id:
            raise HTTPException(status_code=401, detail="login session required for provider link")
        try:
            identity = service.bind_identity(current_user.user_id, profile)
        except AuthError as exc:
            raise _auth_http_error(exc) from exc
        service.record_audit_event(
            AuthAuditEventType.IDENTITY_BOUND,
            actor_user_id=current_user.user_id,
            target_user_id=current_user.user_id,
            provider=provider_name,
            request=request,
            metadata={"identityId": identity.identity_id},
        )
        response = RedirectResponse(oauth_state.redirect_after or "/agent/web/autoagent")
    elif oauth_state.purpose == OAuthStatePurpose.LOGIN:
        try:
            user = service.create_or_update_external_user(profile)
            created_session = service.create_session(user.user_id, metadata={"provider": provider_name})
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        service.record_audit_event(
            AuthAuditEventType.LOGIN,
            target_user_id=user.user_id,
            provider=provider_name,
            request=request,
            metadata={"sessionId": created_session.record.session_id},
        )
        response = RedirectResponse(oauth_state.redirect_after or "/agent/web/autoagent")
        _set_cookie(
            response,
            agentSettings.auth.session_cookie_name,
            created_session.session_token,
            max_age=agentSettings.auth.session_ttl_seconds,
            http_only=True,
        )
    else:
        raise HTTPException(status_code=400, detail="unsupported oauth state purpose")
    response.delete_cookie(nonce_cookie, path="/")
    return response


@auth_router.get("/whoami")
async def whoami(current_user: TaskPilotUser = Depends(require_current_user)) -> dict:
    return {"userId": current_user.user_id}


def _normalize_provider_or_404(provider: str) -> str:
    normalized = str(provider or "").strip().lower().replace("-", "_")
    if not normalized or normalized in {"admin", "providers", "me", "users", "logout", "logout_all", "whoami"}:
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


def _check_rate_limit(limiter: InMemoryRateLimiter, request: Request, scope: str) -> None:
    client = getattr(request, "client", None)
    client_host = getattr(client, "host", "unknown")
    try:
        limiter.check(f"{scope}:{client_host}")
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail="too many requests") from exc


def _user_updates(req: UpdateUserReq) -> dict:
    data = req.model_dump(exclude_unset=True)
    key_map = {
        "primary_email": "primary_email",
        "display_name": "display_name",
        "avatar_url": "avatar_url",
        "locale": "locale",
        "metadata": "metadata",
    }
    return {key_map[key]: value for key, value in data.items() if key in key_map}


def _auth_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, AuthConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, AuthDisabledError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))
