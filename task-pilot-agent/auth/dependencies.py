from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request, WebSocket, status

from auth.models import TaskPilotUser
from auth.service import AuthService
from config.config import agentSettings


def auth_service() -> AuthService:
    return AuthService()


async def get_optional_current_user(
    request: Request,
    service: AuthService = Depends(auth_service),
) -> Optional[TaskPilotUser]:
    token = request.cookies.get(agentSettings.auth.session_cookie_name)
    if not token:
        return None
    return service.get_user_by_session_token(token)


async def require_current_user(
    request: Request,
    service: AuthService = Depends(auth_service),
) -> TaskPilotUser:
    user = await get_optional_current_user(request, service)
    if user:
        return user
    if not agentSettings.auth.required:
        return service.ensure_dev_user()
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")


async def require_current_websocket_user(
    websocket: WebSocket,
    service: Optional[AuthService] = None,
) -> TaskPilotUser:
    resolved_service = service or auth_service()
    cookies = getattr(websocket, "cookies", {}) or {}
    token = cookies.get(agentSettings.auth.session_cookie_name)
    if token:
        user = resolved_service.get_user_by_session_token(token)
        if user:
            return user
    if not agentSettings.auth.required:
        return resolved_service.ensure_dev_user()
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
