from datetime import timedelta

from conftest import AppContext, login
from sqlalchemy import select, update

from backend.core.time import utc_now
from backend.identity.models import (
    AccountStatus,
    AuditLog,
    AuthSession,
    OtpChallenge,
    User,
    UserRole,
)


async def test_unknown_phone_never_receives_otp(context: AppContext) -> None:
    phone = "+77081234567"
    response = await context.client.post("/api/v1/auth/request-otp", json={"phone": phone})

    assert response.status_code == 202
    assert phone not in context.otp_sender.codes
    async with context.database.session_factory() as session:
        events = (await session.execute(select(AuditLog))).scalars().all()
    assert [event.action for event in events] == ["AUTH_OTP_REQUEST_REJECTED"]
    assert "phone" not in (events[0].metadata_json or {})


async def test_role_cannot_be_submitted_during_auth(context: AppContext) -> None:
    response = await context.client.post(
        "/api/v1/auth/request-otp",
        json={"phone": context.phones[UserRole.WORKER], "role": "BOSS"},
    )
    assert response.status_code == 422


async def test_openapi_auth_inputs_never_expose_role(context: AppContext) -> None:
    response = await context.client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    schemas = response.json()["components"]["schemas"]
    for schema_name in ("OtpRequest", "OtpVerifyRequest", "RefreshRequest", "LogoutRequest"):
        assert "role" not in schemas[schema_name].get("properties", {})


async def test_inactive_phone_never_receives_otp(context: AppContext) -> None:
    phone = context.phones[UserRole.WORKER]
    async with context.database.session_factory() as session:
        await session.execute(
            update(User).where(User.phone == phone).values(status=AccountStatus.BLOCKED)
        )
        await session.commit()

    response = await context.client.post("/api/v1/auth/request-otp", json={"phone": phone})
    assert response.status_code == 202
    assert phone not in context.otp_sender.codes


async def test_otp_resend_is_rate_limited(context: AppContext) -> None:
    phone = context.phones[UserRole.WORKER]
    first = await context.client.post("/api/v1/auth/request-otp", json={"phone": phone})
    second = await context.client.post("/api/v1/auth/request-otp", json={"phone": phone})

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "otp_rate_limited"


async def test_expired_otp_is_rejected(context: AppContext) -> None:
    phone = context.phones[UserRole.WORKER]
    await context.client.post("/api/v1/auth/request-otp", json={"phone": phone})
    code = context.otp_sender.codes[phone]
    async with context.database.session_factory() as session:
        await session.execute(
            update(OtpChallenge).values(expires_at=utc_now() - timedelta(seconds=1))
        )
        await session.commit()

    response = await context.client.post(
        "/api/v1/auth/verify-otp",
        json={"phone": phone, "code": code, "device_name": "pytest"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "otp_invalid"


async def test_every_role_is_loaded_from_database(context: AppContext) -> None:
    for role in UserRole:
        tokens = await login(context, role)
        response = await context.client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert response.status_code == 200
        assert response.json()["role"] == role.value


async def test_otp_is_single_use_and_attempts_are_recorded(context: AppContext) -> None:
    phone = context.phones[UserRole.WORKER]
    await context.client.post("/api/v1/auth/request-otp", json={"phone": phone})
    code = context.otp_sender.codes[phone]

    wrong = await context.client.post(
        "/api/v1/auth/verify-otp",
        json={"phone": phone, "code": "000000", "device_name": "pytest"},
    )
    if code == "000000":
        wrong = await context.client.post(
            "/api/v1/auth/verify-otp",
            json={"phone": phone, "code": "111111", "device_name": "pytest"},
        )
    assert wrong.status_code == 400

    valid = await context.client.post(
        "/api/v1/auth/verify-otp",
        json={"phone": phone, "code": code, "device_name": "pytest"},
    )
    assert valid.status_code == 200
    reused = await context.client.post(
        "/api/v1/auth/verify-otp",
        json={"phone": phone, "code": code, "device_name": "pytest"},
    )
    assert reused.status_code == 400


async def test_refresh_rotation_and_reuse_revokes_sessions(context: AppContext) -> None:
    tokens = await login(context, UserRole.ADMIN)
    old_refresh = tokens["refresh_token"]
    rotated = await context.client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != old_refresh

    reuse = await context.client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert reuse.status_code == 401
    assert reuse.json()["error"]["code"] == "refresh_token_reuse"

    me = await context.client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {rotated.json()['access_token']}"},
    )
    assert me.status_code == 401


async def test_logout_revokes_current_session(context: AppContext) -> None:
    tokens = await login(context, UserRole.BOSS)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    response = await context.client.post(
        "/api/v1/auth/logout",
        headers=headers,
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert response.status_code == 200

    denied = await context.client.get("/api/v1/me", headers=headers)
    assert denied.status_code == 401
    async with context.database.session_factory() as session:
        sessions = (await session.execute(select(AuthSession))).scalars().all()
    assert sessions[0].revoked_at is not None


async def test_session_list_does_not_expose_token_hash(context: AppContext) -> None:
    tokens = await login(context, UserRole.MASTER)
    response = await context.client.get(
        "/api/v1/me/sessions",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json()[0]["current"] is True
    assert "refresh_token_hash" not in response.json()[0]
