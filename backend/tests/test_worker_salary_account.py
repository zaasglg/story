from uuid import uuid4

import pytest
from conftest import login
from sqlalchemy import select

from backend.core.time import utc_now
from backend.identity.models import AuditLog, EmployeeProfile, User, UserRole
from backend.workforce.models import EmployeePrivateData


@pytest.mark.parametrize("owner_role", [UserRole.WORKER, UserRole.MASTER])
async def test_salary_account_permissions_encryption_and_lifecycle(context, owner_role):
    headers = {}
    for role in UserRole:
        tokens = await login(context, role)
        headers[role] = {"Authorization": f"Bearer {tokens['access_token']}"}
    async with context.database.session_factory() as session:
        worker_id = await session.scalar(select(User.id).where(User.role == owner_role))
    path = f"/api/v1/admin/workers/{worker_id}/salary-account"
    account = {
        "bank_name": "Halyk",
        "iban": "KZ86 125K ZT50 0410 0100",
        "account_holder": "Оразғали Асан",
    }
    normalized = "KZ86125KZT5004100100"
    assert (await context.client.get(path, headers=headers[UserRole.BOSS])).json() is None
    created = await context.client.put(path, headers=headers[UserRole.BOSS], json=account)
    assert created.status_code == 200, created.text
    assert created.json()["iban"] == normalized
    for role in UserRole:
        read = await context.client.get(path, headers=headers[role])
        assert read.status_code == (200 if role == UserRole.BOSS else 403)
        if read.status_code == 200:
            assert read.json()["account_holder"] == "Оразғали Асан"
        if role != UserRole.BOSS:
            assert (
                await context.client.put(path, headers=headers[role], json=account)
            ).status_code == 403
            assert (await context.client.delete(path, headers=headers[role])).status_code == 403
    async with context.database.session_factory() as session:
        private = await session.scalar(
            select(EmployeePrivateData).where(EmployeePrivateData.user_id == worker_id)
        )
        assert private.salary_account_encrypted
        assert normalized not in private.salary_account_encrypted
        assert "Halyk" not in private.salary_account_encrypted
        logs = (await session.scalars(select(AuditLog))).all()
        assert normalized not in repr([(x.new_value, x.old_value, x.metadata_json) for x in logs])
    public = await context.client.get("/api/v1/workers/public", headers=headers[UserRole.MASTER])
    assert normalized not in public.text and "iban" not in public.text
    invalid = await context.client.put(
        path, headers=headers[UserRole.BOSS], json={**account, "iban": "   "}
    )
    assert invalid.status_code == 422
    unchanged = await context.client.get(path, headers=headers[UserRole.BOSS])
    assert unchanged.json()["iban"] == normalized
    assert (await context.client.delete(path, headers=headers[UserRole.BOSS])).status_code == 204
    assert (await context.client.get(path, headers=headers[UserRole.BOSS])).json() is None


@pytest.mark.parametrize(
    "owner_state",
    ["admin", "boss", "archived_user", "archived_profile", "missing_profile", "missing"],
)
async def test_salary_account_rejects_ineligible_or_archived_owner(context, owner_state):
    tokens = await login(context, UserRole.BOSS)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    role = {"admin": UserRole.ADMIN, "boss": UserRole.BOSS}.get(owner_state, UserRole.MASTER)
    async with context.database.session_factory() as session:
        owner = await session.scalar(select(User).where(User.role == role))
        owner_id = owner.id
        profile = await session.scalar(
            select(EmployeeProfile).where(EmployeeProfile.user_id == owner_id)
        )
        if owner_state == "archived_user":
            owner.archived_at = utc_now()
        elif owner_state == "archived_profile":
            profile.archived_at = utc_now()
        elif owner_state == "missing_profile":
            await session.delete(profile)
        elif owner_state == "missing":
            owner_id = uuid4()
        await session.commit()
    path = f"/api/v1/admin/workers/{owner_id}/salary-account"
    account = {"bank_name": "Synthetic", "iban": "123", "account_holder": "Synthetic"}
    assert (await context.client.get(path, headers=headers)).status_code == 404
    assert (await context.client.put(path, headers=headers, json=account)).status_code == 404
    assert (await context.client.delete(path, headers=headers)).status_code == 404
