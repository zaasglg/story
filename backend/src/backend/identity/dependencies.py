from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.config import Settings
from backend.core.errors import ApiError
from backend.core.time import ensure_utc, utc_now
from backend.identity.models import AccountStatus, AuthSession, User, UserRole
from backend.identity.security import decode_access_token

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    role: UserRole
    session_id: UUID
    auth_version: int


async def get_db_session(request: Request):
    async with request.app.state.database.session_factory() as session:
        yield session


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ApiError(401, "authentication_required", "Authentication is required")
    payload = decode_access_token(credentials.credentials, settings)
    try:
        user_id = UUID(str(payload["sub"]))
        session_id = UUID(str(payload["sid"]))
        token_role = UserRole(str(payload["role"]))
        auth_version = int(str(payload["ver"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise ApiError(401, "invalid_token", "Authentication token is invalid") from exc

    result = await session.execute(
        select(User, AuthSession)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(User.id == user_id, AuthSession.id == session_id)
    )
    row = result.one_or_none()
    if row is None:
        raise ApiError(401, "invalid_token", "Authentication session does not exist")
    user, auth_session = row
    if (
        user.status is not AccountStatus.ACTIVE
        or user.auth_version != auth_version
        or user.role is not token_role
        or auth_session.revoked_at is not None
        or ensure_utc(auth_session.expires_at) <= utc_now()
    ):
        raise ApiError(401, "invalid_token", "Authentication session is no longer active")
    return CurrentUser(
        id=user.id,
        role=user.role,
        session_id=auth_session.id,
        auth_version=user.auth_version,
    )


async def load_user_with_profile(session: AsyncSession, user_id: UUID) -> User:
    result = await session.execute(
        select(User).where(User.id == user_id).options(selectinload(User.profile))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise ApiError(404, "user_not_found", "User was not found")
    return user
