import hashlib
import hmac
import secrets
from datetime import timedelta
from uuid import UUID

import jwt

from backend.core.config import Settings
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.identity.models import User


def secret_hash(value: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), value.encode(), hashlib.sha256).hexdigest()


def opaque_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def issue_access_token(user: User, session_id: UUID, settings: Settings) -> str:
    now = utc_now()
    expires_at = now + timedelta(minutes=settings.access_token_minutes)
    payload = {
        "sub": str(user.id),
        "sid": str(session_id),
        "role": user.role.value,
        "ver": user.auth_version,
        "type": "access",
        "iat": now,
        "exp": expires_at,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm="HS256")


def decode_access_token(token: str, settings: Settings) -> dict[str, object]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "sid", "role", "ver", "type", "iat", "exp"]},
        )
    except jwt.PyJWTError as exc:
        raise ApiError(401, "invalid_token", "Authentication token is invalid or expired") from exc
    if payload.get("type") != "access":
        raise ApiError(401, "invalid_token", "Authentication token has the wrong type")
    return payload


def generate_refresh_token(session_id: UUID) -> str:
    return f"{session_id}.{secrets.token_urlsafe(48)}"


def parse_refresh_token(token: str) -> UUID:
    try:
        session_id, secret = token.split(".", 1)
        if len(secret) < 40:
            raise ValueError
        return UUID(session_id)
    except (ValueError, AttributeError) as exc:
        raise ApiError(401, "invalid_refresh_token", "Refresh token is invalid") from exc
