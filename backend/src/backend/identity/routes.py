from asyncio import to_thread
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit.service import add_audit_event
from backend.authorization.permissions import ROLE_PERMISSIONS
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.identity.dependencies import (
    CurrentUser,
    get_current_user,
    get_db_session,
    load_user_with_profile,
)
from backend.identity.models import AuthSession, User
from backend.identity.schemas import (
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    PasswordChangeRequest,
    RefreshRequest,
    SessionResponse,
    TokenResponse,
    UserResponse,
)
from backend.identity.security import hash_password, parse_refresh_token, verify_password
from backend.identity.service import AuthService, to_user_response

router = APIRouter(tags=["authentication"])


@router.post("/me/password", response_model=MessageResponse)
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    current: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MessageResponse:
    service = request.app.state.auth_service
    if not await service.rate_limiter.hit(f"password-change:{current.id}", 5, 900):
        raise ApiError(429, "password_rate_limited", "Try again later")
    user = await session.scalar(
        select(User)
        .where(User.id == current.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not await to_thread(verify_password, body.current_password, user.password_hash or ""):
        raise ApiError(400, "incorrect_password", "Current password is incorrect")
    if body.current_password == body.new_password:
        raise ApiError(422, "password_unchanged", "Choose a different password")
    user.password_hash = await to_thread(hash_password, body.new_password)
    user.updated_at = utc_now()
    await session.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == current.id,
            AuthSession.id != current.session_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=utc_now(), revoke_reason="password_changed")
    )
    add_audit_event(
        session,
        action="AUTH_PASSWORD_CHANGED",
        entity_type="user",
        entity_id=current.id,
        actor_user_id=current.id,
    )
    await session.commit()
    return MessageResponse(status="password_changed")


@router.delete("/me/other-sessions", response_model=MessageResponse)
async def revoke_other_sessions(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MessageResponse:
    await session.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == current.id,
            AuthSession.id != current.session_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=utc_now(), revoke_reason="user_revoked")
    )
    add_audit_event(
        session,
        action="AUTH_OTHER_SESSIONS_REVOKED",
        entity_type="user",
        entity_id=current.id,
        actor_user_id=current.id,
    )
    await session.commit()
    return MessageResponse(status="revoked")


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/auth/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenResponse:
    return await service.login(
        session,
        phone_input=body.phone,
        password=body.password,
        device_name=body.device_name,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        request_id=request.state.request_id,
    )


@router.post("/auth/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenResponse:
    return await service.refresh(
        session,
        refresh_token=body.refresh_token,
        ip_address=client_ip(request),
        request_id=request.state.request_id,
    )


@router.post("/auth/logout", response_model=MessageResponse)
async def logout(
    body: LogoutRequest,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> MessageResponse:
    target_session_id = current_user.session_id
    if body.refresh_token:
        parsed_session_id = parse_refresh_token(body.refresh_token)
        if parsed_session_id == current_user.session_id:
            target_session_id = parsed_session_id
    await service.revoke_session(
        session,
        user_id=current_user.id,
        session_id=target_session_id,
        reason="logout",
        request_id=request.state.request_id,
    )
    return MessageResponse(status="logged_out")


@router.get("/me", response_model=UserResponse)
async def me(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserResponse:
    user = await load_user_with_profile(session, current_user.id)
    return to_user_response(user)


@router.get("/me/permissions", response_model=list[str])
async def my_permissions(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> list[str]:
    return sorted(permission.value for permission in ROLE_PERMISSIONS[current_user.role])


@router.get("/me/sessions", response_model=list[SessionResponse])
async def sessions(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> list[SessionResponse]:
    return await service.list_sessions(
        session,
        user_id=current_user.id,
        current_session_id=current_user.session_id,
    )


@router.delete("/me/sessions/{session_id}", response_model=MessageResponse)
async def revoke_session(
    session_id: UUID,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> MessageResponse:
    await service.revoke_session(
        session,
        user_id=current_user.id,
        session_id=session_id,
        reason="user_revoked",
        request_id=request.state.request_id,
    )
    return MessageResponse(status="revoked")
