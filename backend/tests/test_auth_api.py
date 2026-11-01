from conftest import AppContext, login
from sqlalchemy import select, update

from backend.identity.models import (
    AccountStatus,
    AuthSession,
    User,
    UserRole,
)


async def test_invalid_credentials_and_inactive_accounts(context: AppContext) -> None:
    for phone, password in [
        ("+77081234567", "Test-password-123"),
        (context.phones[UserRole.WORKER], "wrong-password"),
    ]:
        response = await context.client.post(
            "/api/v1/auth/login", json={"phone": phone, "password": password}
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_credentials"
    async with context.database.session_factory() as session:
        await session.execute(update(User).values(status=AccountStatus.BLOCKED))
        await session.commit()
    response = await context.client.post(
        "/api/v1/auth/login",
        json={"phone": context.phones[UserRole.WORKER], "password": "Test-password-123"},
    )
    assert response.status_code == 401


async def test_role_cannot_be_submitted_during_auth(context: AppContext) -> None:
    response = await context.client.post(
        "/api/v1/auth/login",
        json={
            "phone": context.phones[UserRole.WORKER],
            "password": "Test-password-123",
            "role": "BOSS",
        },
    )
    assert response.status_code == 422


async def test_openapi_auth_inputs_never_expose_role(context: AppContext) -> None:
    response = await context.client.get("/api/v1/openapi.json")
    schemas = response.json()["components"]["schemas"]
    for schema_name in ("LoginRequest", "RefreshRequest", "LogoutRequest"):
        assert "role" not in schemas[schema_name].get("properties", {})


async def test_login_is_rate_limited(context: AppContext) -> None:
    for _ in range(context.settings.login_phone_hour_limit):
        response = await context.client.post(
            "/api/v1/auth/login",
            json={"phone": context.phones[UserRole.WORKER], "password": "wrong"},
        )
        assert response.status_code == 401
    response = await context.client.post(
        "/api/v1/auth/login",
        json={"phone": context.phones[UserRole.WORKER], "password": "Test-password-123"},
    )
    assert response.status_code == 429


async def test_otp_endpoints_removed(context: AppContext) -> None:
    for endpoint in ("request-otp", "verify-otp"):
        response = await context.client.post(f"/api/v1/auth/{endpoint}", json={})
        assert response.status_code == 404


async def test_every_role_is_loaded_from_database(context: AppContext) -> None:
    for role in UserRole:
        tokens = await login(context, role)
        response = await context.client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert response.status_code == 200
        assert response.json()["role"] == role.value


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


async def test_password_reset_revokes_sessions_and_hash_is_private(context: AppContext) -> None:
    from unittest.mock import patch

    from backend.set_password import set_password

    tokens = await login(context, UserRole.WORKER)
    assert "password_hash" not in tokens["user"]
    with patch("backend.set_password.get_settings", return_value=context.settings):
        await set_password(context.phones[UserRole.WORKER], "New-password-123")
    response = await context.client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert response.status_code == 401
    for password, status in [("Test-password-123", 401), ("New-password-123", 200)]:
        response = await context.client.post(
            "/api/v1/auth/login",
            json={"phone": context.phones[UserRole.WORKER], "password": password},
        )
        assert response.status_code == status


async def test_account_without_password_cannot_login(context: AppContext) -> None:
    async with context.database.session_factory() as session:
        await session.execute(update(User).values(password_hash=None))
        await session.commit()
    response = await context.client.post(
        "/api/v1/auth/login",
        json={
            "phone": context.phones[UserRole.WORKER],
            "password": "invalid-account-dummy-password",
        },
    )
    assert response.status_code == 401
