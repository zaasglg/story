import hmac
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.audit.service import add_audit_event
from backend.core.config import Settings
from backend.core.errors import ApiError
from backend.core.time import ensure_utc, utc_now
from backend.identity.models import AccountStatus, AuthSession, OtpChallenge, User
from backend.identity.otp import OtpSender, RateLimiter
from backend.identity.phone import normalize_phone
from backend.identity.schemas import SessionResponse, TokenResponse, UserResponse
from backend.identity.security import (
    generate_otp,
    generate_refresh_token,
    issue_access_token,
    opaque_hash,
    parse_refresh_token,
    secret_hash,
)


def client_hash(value: str, settings: Settings) -> str:
    return secret_hash(value or "unknown", settings.otp_pepper.get_secret_value())


def to_user_response(user: User) -> UserResponse:
    profile = user.profile
    return UserResponse(
        id=user.id,
        phone=user.phone,
        role=user.role,
        status=user.status,
        full_name=profile.full_name if profile else None,
        employee_id=profile.employee_id if profile else None,
        city=profile.city if profile else None,
    )


class AuthService:
    def __init__(
        self,
        settings: Settings,
        otp_sender: OtpSender,
        rate_limiter: RateLimiter,
    ) -> None:
        self.settings = settings
        self.otp_sender = otp_sender
        self.rate_limiter = rate_limiter

    async def request_otp(
        self,
        session: AsyncSession,
        *,
        phone_input: str,
        ip_address: str,
        request_id: str | None,
    ) -> int:
        phone = normalize_phone(phone_input)
        phone_hash = client_hash(phone, self.settings)
        ip_hash = client_hash(ip_address, self.settings)

        allowed_phone = await self.rate_limiter.hit(
            f"otp:hour:phone:{phone_hash}", self.settings.otp_phone_hour_limit, 3600
        )
        allowed_ip = await self.rate_limiter.hit(
            f"otp:hour:ip:{ip_hash}", self.settings.otp_ip_hour_limit, 3600
        )
        cooldown_ok = await self.rate_limiter.hit(
            f"otp:cooldown:{phone_hash}", 1, self.settings.otp_resend_seconds
        )
        if not allowed_phone or not allowed_ip or not cooldown_ok:
            raise ApiError(429, "otp_rate_limited", "Please wait before requesting another code")

        result = await session.execute(
            select(User).where(User.phone == phone).options(selectinload(User.profile))
        )
        user = result.scalar_one_or_none()
        if user is None or user.status is not AccountStatus.ACTIVE:
            add_audit_event(
                session,
                action="AUTH_OTP_REQUEST_REJECTED",
                entity_type="auth",
                metadata={"phone_hash": phone_hash, "reason": "not_eligible"},
                request_id=request_id,
                ip_hash=ip_hash,
                succeeded=False,
            )
            await session.commit()
            return self.settings.otp_resend_seconds

        now = utc_now()
        await session.execute(
            update(OtpChallenge)
            .where(OtpChallenge.user_id == user.id, OtpChallenge.consumed_at.is_(None))
            .values(consumed_at=now)
        )
        code = generate_otp()
        challenge_id = uuid4()
        challenge = OtpChallenge(
            id=challenge_id,
            user_id=user.id,
            phone_hash=phone_hash,
            code_hash=secret_hash(
                f"{challenge_id}:{code}", self.settings.otp_pepper.get_secret_value()
            ),
            attempts=0,
            max_attempts=self.settings.otp_max_attempts,
            expires_at=now + timedelta(seconds=self.settings.otp_ttl_seconds),
            created_at=now,
            request_ip_hash=ip_hash,
        )
        session.add(challenge)
        add_audit_event(
            session,
            action="AUTH_OTP_REQUESTED",
            entity_type="user",
            entity_id=user.id,
            actor_user_id=user.id,
            request_id=request_id,
            ip_hash=ip_hash,
        )
        await self.otp_sender.send(phone, code)
        await session.commit()
        return self.settings.otp_resend_seconds

    async def verify_otp(
        self,
        session: AsyncSession,
        *,
        phone_input: str,
        code: str,
        device_name: str,
        ip_address: str,
        user_agent: str | None,
        request_id: str | None,
    ) -> TokenResponse:
        phone = normalize_phone(phone_input)
        ip_hash = client_hash(ip_address, self.settings)
        verify_allowed = await self.rate_limiter.hit(
            f"otp:verify:ip:{ip_hash}", self.settings.otp_ip_hour_limit * 2, 3600
        )
        if not verify_allowed:
            raise ApiError(429, "otp_rate_limited", "Too many verification attempts")

        result = await session.execute(
            select(User).where(User.phone == phone).options(selectinload(User.profile))
        )
        user = result.scalar_one_or_none()
        if user is None or user.status is not AccountStatus.ACTIVE:
            raise ApiError(400, "otp_invalid", "The verification code is invalid or expired")

        challenge_result = await session.execute(
            select(OtpChallenge)
            .where(OtpChallenge.user_id == user.id, OtpChallenge.consumed_at.is_(None))
            .order_by(OtpChallenge.created_at.desc())
            .with_for_update()
        )
        challenge = challenge_result.scalars().first()
        now = utc_now()
        if challenge is None or ensure_utc(challenge.expires_at) <= now:
            raise ApiError(400, "otp_invalid", "The verification code is invalid or expired")
        if challenge.attempts >= challenge.max_attempts:
            raise ApiError(400, "otp_locked", "The verification code can no longer be used")

        challenge.attempts += 1
        candidate_hash = secret_hash(
            f"{challenge.id}:{code}", self.settings.otp_pepper.get_secret_value()
        )
        if not hmac.compare_digest(candidate_hash, challenge.code_hash):
            add_audit_event(
                session,
                action="AUTH_OTP_VERIFY_FAILED",
                entity_type="user",
                entity_id=user.id,
                actor_user_id=user.id,
                request_id=request_id,
                ip_hash=ip_hash,
                succeeded=False,
            )
            await session.commit()
            raise ApiError(400, "otp_invalid", "The verification code is invalid or expired")

        challenge.consumed_at = now
        auth_session = AuthSession(
            user_id=user.id,
            refresh_token_hash="pending",
            device_name=device_name,
            created_at=now,
            last_used_at=now,
            expires_at=now + timedelta(days=self.settings.refresh_token_days),
            ip_hash=ip_hash,
            user_agent=(user_agent or "")[:512] or None,
        )
        session.add(auth_session)
        await session.flush()
        refresh_token = generate_refresh_token(auth_session.id)
        auth_session.refresh_token_hash = opaque_hash(refresh_token)
        access_token = issue_access_token(user, auth_session.id, self.settings)
        add_audit_event(
            session,
            action="AUTH_LOGIN_SUCCEEDED",
            entity_type="auth_session",
            entity_id=auth_session.id,
            actor_user_id=user.id,
            request_id=request_id,
            ip_hash=ip_hash,
        )
        await session.commit()
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=self.settings.access_token_minutes * 60,
            user=to_user_response(user),
        )

    async def refresh(
        self,
        session: AsyncSession,
        *,
        refresh_token: str,
        ip_address: str,
        request_id: str | None,
    ) -> TokenResponse:
        session_id = parse_refresh_token(refresh_token)
        result = await session.execute(
            select(AuthSession).where(AuthSession.id == session_id).with_for_update()
        )
        auth_session = result.scalar_one_or_none()
        now = utc_now()
        if (
            auth_session is None
            or auth_session.revoked_at
            or ensure_utc(auth_session.expires_at) <= now
        ):
            raise ApiError(401, "invalid_refresh_token", "Refresh token is invalid or expired")

        user_result = await session.execute(
            select(User).where(User.id == auth_session.user_id).options(selectinload(User.profile))
        )
        user = user_result.scalar_one()
        if user.status is not AccountStatus.ACTIVE:
            raise ApiError(403, "account_inactive", "Account is not active")

        if not hmac.compare_digest(opaque_hash(refresh_token), auth_session.refresh_token_hash):
            await session.execute(
                update(AuthSession)
                .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=now, revoke_reason="refresh_token_reuse")
            )
            user.auth_version += 1
            add_audit_event(
                session,
                action="AUTH_REFRESH_REUSE_DETECTED",
                entity_type="user",
                entity_id=user.id,
                actor_user_id=user.id,
                request_id=request_id,
                ip_hash=client_hash(ip_address, self.settings),
                succeeded=False,
            )
            await session.commit()
            raise ApiError(401, "refresh_token_reuse", "Session was revoked for security")

        new_refresh_token = generate_refresh_token(auth_session.id)
        auth_session.refresh_token_hash = opaque_hash(new_refresh_token)
        auth_session.last_used_at = now
        auth_session.ip_hash = client_hash(ip_address, self.settings)
        access_token = issue_access_token(user, auth_session.id, self.settings)
        add_audit_event(
            session,
            action="AUTH_TOKEN_REFRESHED",
            entity_type="auth_session",
            entity_id=auth_session.id,
            actor_user_id=user.id,
            request_id=request_id,
            ip_hash=auth_session.ip_hash,
        )
        await session.commit()
        return TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh_token,
            expires_in=self.settings.access_token_minutes * 60,
            user=to_user_response(user),
        )

    async def revoke_session(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        session_id: UUID,
        reason: str,
        request_id: str | None,
    ) -> None:
        result = await session.execute(
            select(AuthSession).where(
                AuthSession.id == session_id,
                AuthSession.user_id == user_id,
            )
        )
        auth_session = result.scalar_one_or_none()
        if auth_session and auth_session.revoked_at is None:
            auth_session.revoked_at = utc_now()
            auth_session.revoke_reason = reason
            add_audit_event(
                session,
                action="AUTH_SESSION_REVOKED",
                entity_type="auth_session",
                entity_id=auth_session.id,
                actor_user_id=user_id,
                request_id=request_id,
            )
            await session.commit()

    async def list_sessions(
        self, session: AsyncSession, *, user_id: UUID, current_session_id: UUID
    ) -> list[SessionResponse]:
        result = await session.execute(
            select(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > utc_now(),
            )
            .order_by(AuthSession.last_used_at.desc())
        )
        return [
            SessionResponse(
                id=item.id,
                device_name=item.device_name,
                created_at=item.created_at,
                last_used_at=item.last_used_at,
                expires_at=item.expires_at,
                current=item.id == current_session_id,
            )
            for item in result.scalars()
        ]
