from conftest import login
from sqlalchemy import select

from backend.identity.models import AuditLog, User, UserRole
from backend.workforce.models import EmployeePrivateData


async def test_salary_account_permissions_encryption_and_lifecycle(context):
    headers = {}
    for role in UserRole:
        tokens = await login(context, role)
        headers[role] = {"Authorization": f"Bearer {tokens['access_token']}"}
    async with context.database.session_factory() as session:
        worker_id = await session.scalar(select(User.id).where(User.role == UserRole.WORKER))
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
        path, headers=headers[UserRole.BOSS], json={**account, "iban": "KZ00125KZT5004100100"}
    )
    assert invalid.status_code == 422
    unchanged = await context.client.get(path, headers=headers[UserRole.BOSS])
    assert unchanged.json()["iban"] == normalized
    assert (await context.client.delete(path, headers=headers[UserRole.BOSS])).status_code == 204
    assert (await context.client.get(path, headers=headers[UserRole.BOSS])).json() is None
