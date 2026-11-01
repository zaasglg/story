from uuid import UUID, uuid4

from conftest import login
from sqlalchemy import select

from backend.identity.models import AuditLog, User, UserRole


async def role_headers(context):
    result = {}
    for role in UserRole:
        tokens = await login(context, role)
        result[role] = {"Authorization": f"Bearer {tokens['access_token']}"}
    return result


async def test_administrator_creation_role_matrix_and_secret_redaction(context):
    headers = await role_headers(context)
    path = "/api/v1/boss/administrators"
    payload = {
        "full_name": "  Новый Администратор  ",
        "phone": "+77075550222",
        "password": "Admin-secret-123",
    }
    for role in (UserRole.ADMIN, UserRole.MASTER, UserRole.WORKER):
        assert (await context.client.get(path, headers=headers[role])).status_code == 403
        assert (
            await context.client.post(path, headers=headers[role], json=payload)
        ).status_code == 403
        for suffix, method, body in (
            ("status", "put", {"status": "BLOCKED"}),
            ("password", "post", {"password": "New-password-123"}),
        ):
            response = await getattr(context.client, method)(
                f"{path}/{uuid4()}/{suffix}", headers=headers[role], json=body
            )
            assert response.status_code == 403
    assert (await context.client.get(path)).status_code == 401
    denied = await context.client.post(
        path, headers=headers[UserRole.BOSS], json={**payload, "role": "BOSS"}
    )
    assert denied.status_code == 422
    created = await context.client.post(path, headers=headers[UserRole.BOSS], json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["full_name"] == "Новый Администратор"
    assert created.json()["status"] == "ACTIVE"
    assert "password" not in created.text
    duplicate = await context.client.post(path, headers=headers[UserRole.BOSS], json=payload)
    assert duplicate.status_code == 409
    found = await context.client.get(
        path, headers=headers[UserRole.BOSS], params={"q": "+77075550222"}
    )
    assert [row["id"] for row in found.json()] == [created.json()["id"]]
    async with context.database.session_factory() as session:
        user = await session.get(User, UUID(created.json()["id"]))
        assert user.role == UserRole.ADMIN
        assert user.password_hash != payload["password"]
        logs = (await session.scalars(select(AuditLog))).all()
        assert payload["password"] not in repr(
            [(row.old_value, row.new_value, row.metadata_json) for row in logs]
        )
    journal = await context.client.get("/api/v1/boss/audit", headers=headers[UserRole.BOSS])
    assert any(row["action"] == "ADMINISTRATOR_CREATED" for row in journal.json())


async def test_block_unblock_and_password_reset_revoke_existing_sessions(context):
    headers = await role_headers(context)
    path = "/api/v1/boss/administrators"
    payload = {
        "full_name": "Администратор",
        "phone": "+77075550223",
        "password": "Original-pass-123",
    }
    created = await context.client.post(path, headers=headers[UserRole.BOSS], json=payload)
    assert created.status_code == 201, created.text
    item = f"{path}/{created.json()['id']}"

    async def sign_in(password):
        return await context.client.post(
            "/api/v1/auth/login", json={"phone": payload["phone"], "password": password}
        )

    signed = await sign_in(payload["password"])
    assert signed.status_code == 200
    tokens = signed.json()
    old_header = {"Authorization": f"Bearer {tokens['access_token']}"}
    blocked = await context.client.put(
        item + "/status", headers=headers[UserRole.BOSS], json={"status": "BLOCKED"}
    )
    assert blocked.status_code == 200
    assert (
        await context.client.get("/api/v1/admin/workers", headers=old_header)
    ).status_code == 401
    assert (
        await context.client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
    ).status_code == 401
    assert (await sign_in(payload["password"])).status_code == 401
    unblocked = await context.client.put(
        item + "/status", headers=headers[UserRole.BOSS], json={"status": "ACTIVE"}
    )
    assert unblocked.status_code == 200
    assert (
        await context.client.get("/api/v1/admin/workers", headers=old_header)
    ).status_code == 401
    second = await sign_in(payload["password"])
    assert second.status_code == 200
    second_tokens = second.json()
    reset = await context.client.post(
        item + "/password", headers=headers[UserRole.BOSS], json={"password": "Changed-pass-456"}
    )
    assert reset.status_code == 204, reset.text
    assert reset.content == b""
    assert (await sign_in(payload["password"])).status_code == 401
    assert (await sign_in("Changed-pass-456")).status_code == 200
    assert (
        await context.client.get(
            "/api/v1/admin/workers",
            headers={"Authorization": f"Bearer {second_tokens['access_token']}"},
        )
    ).status_code == 401
    assert (
        await context.client.post(
            "/api/v1/auth/refresh", json={"refresh_token": second_tokens["refresh_token"]}
        )
    ).status_code == 401


async def test_management_cannot_target_other_roles_and_validates_inputs(context):
    headers = await role_headers(context)
    async with context.database.session_factory() as session:
        others = (await session.scalars(select(User).where(User.role != UserRole.ADMIN))).all()
    for user in others:
        for suffix, method, body in (
            ("status", "put", {"status": "BLOCKED"}),
            ("password", "post", {"password": "New-password-123"}),
        ):
            response = await getattr(context.client, method)(
                f"/api/v1/boss/administrators/{user.id}/{suffix}",
                headers=headers[UserRole.BOSS],
                json=body,
            )
            assert response.status_code == 404
    for changes in ({"full_name": "  "}, {"phone": "invalid"}, {"password": "short"}):
        payload = {
            "full_name": "Администратор",
            "phone": "+77075550224",
            "password": "Valid-pass-123",
            **changes,
        }
        assert (
            await context.client.post(
                "/api/v1/boss/administrators", headers=headers[UserRole.BOSS], json=payload
            )
        ).status_code == 422
