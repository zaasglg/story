from conftest import login
from sqlalchemy import select, update

from backend.audit.service import add_audit_event
from backend.identity.models import EmployeeProfile, User, UserRole


async def test_master_public_directory_is_team_scoped(context):
    headers = {}
    for role in UserRole:
        tokens = await login(context, role)
        headers[role] = {"Authorization": f"Bearer {tokens['access_token']}"}
    async with context.database.session_factory() as session:
        worker = await session.scalar(select(User.id).where(User.role == UserRole.WORKER))
        await session.execute(
            update(EmployeeProfile)
            .where(EmployeeProfile.user_id == worker)
            .values(master_user_id=None)
        )
        await session.commit()
    master = headers[UserRole.MASTER]
    assert (await context.client.get("/api/v1/workers/public", headers=master)).json() == []
    assert (
        await context.client.get(f"/api/v1/workers/{worker}/public", headers=master)
    ).status_code == 404
    assert (await context.client.get("/api/v1/admin/workers", headers=master)).status_code == 403
    for role in (UserRole.ADMIN, UserRole.MASTER, UserRole.WORKER):
        for path in (
            "/boss/expenses",
            "/admin/expenses",
            f"/admin/workers/{worker}/salary-account",
        ):
            assert (
                await context.client.get(f"/api/v1{path}", headers=headers[role])
            ).status_code == 403

    async with context.database.session_factory() as session:
        master_id = await session.scalar(select(User.id).where(User.role == UserRole.MASTER))
        await session.execute(
            update(EmployeeProfile)
            .where(EmployeeProfile.user_id == worker)
            .values(master_user_id=master_id)
        )
        await session.commit()
    own_team = await context.client.get("/api/v1/workers/public", headers=master)
    assert [row["id"] for row in own_team.json()] == [str(worker)]
    assert (
        await context.client.get(f"/api/v1/workers/{worker}/public", headers=master)
    ).status_code == 200


async def test_audit_role_scope_and_secret_redaction(context):
    headers = {}
    for role in UserRole:
        tokens = await login(context, role)
        headers[role] = {"Authorization": f"Bearer {tokens['access_token']}"}
    async with context.database.session_factory() as session:
        users = {user.role: user for user in (await session.scalars(select(User))).all()}
        for role in (UserRole.ADMIN, UserRole.BOSS):
            add_audit_event(
                session,
                actor_user_id=users[role].id,
                action=f"TEST_{role.value}",
                entity_type="test",
                old_value={"iban": "SECRET_BANK"},
                new_value={"salary": "SECRET_SALARY"},
            )
        await session.commit()
    for role in UserRole:
        for endpoint, allowed in (("boss", UserRole.BOSS), ("admin", UserRole.ADMIN)):
            result = await context.client.get(f"/api/v1/{endpoint}/audit", headers=headers[role])
            assert result.status_code == (200 if role == allowed else 403)
            if result.status_code == 200:
                assert "TEST_ADMIN" in result.text
                assert ("TEST_BOSS" in result.text) == (role == UserRole.BOSS)
                assert "SECRET_BANK" not in result.text and "SECRET_SALARY" not in result.text


async def test_master_cannot_bypass_team_scope_via_catalog(context):
    tokens = await login(context, UserRole.MASTER)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    for path in (
        "/admin/brigades",
        "/boss/brigades",
        "/admin/objects",
        "/admin/catalog/brigades",
        "/admin/catalog/objects",
    ):
        assert (await context.client.get("/api/v1" + path, headers=headers)).status_code == 403
    assert (await context.client.get("/api/v1/master/brigade", headers=headers)).status_code == 200
