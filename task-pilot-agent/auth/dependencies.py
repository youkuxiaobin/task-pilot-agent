from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request, status

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

