from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.authorization.permissions import ROLE_PERMISSIONS
from backend.identity.dependencies import (
    CurrentUser,
    get_current_user,
    get_db_session,
    load_user_with_profile,
)
from backend.identity.schemas import (
    LogoutRequest,
    MessageResponse,
    OtpRequest,
    OtpRequestResponse,
    OtpVerifyRequest,
    RefreshRequest,
    SessionResponse,
    TokenResponse,
    UserResponse,
)
from backend.identity.security import parse_refresh_token
from backend.identity.service import AuthService, to_user_response

router = APIRouter(tags=["authentication"])


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post(
    "/auth/request-otp",
    response_model=OtpRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_otp(
    body: OtpRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> OtpRequestResponse:
    retry_after = await service.request_otp(
        session,
        phone_input=body.phone,
        ip_address=client_ip(request),
        request_id=request.state.request_id,
    )
    return OtpRequestResponse(retry_after_seconds=retry_after)


@router.post("/auth/verify-otp", response_model=TokenResponse)
async def verify_otp(
    body: OtpVerifyRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenResponse:
    return await service.verify_otp(
        session,
        phone_input=body.phone,
        code=body.code,
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
