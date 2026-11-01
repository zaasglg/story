from conftest import login
from sqlalchemy import select

from backend.identity.models import User, UserRole


async def test_worker_reads_only_own_salary_account_and_cannot_write(context):
    headers = {}
    for role in UserRole:
        tokens = await login(context, role)
        headers[role] = {"Authorization": f"Bearer {tokens['access_token']}"}
    async with context.database.session_factory() as session:
        worker_id = await session.scalar(select(User.id).where(User.role == UserRole.WORKER))
    own_path = "/api/v1/workers/me/salary-account"
    assert (await context.client.get(own_path, headers=headers[UserRole.WORKER])).json() is None
    second = await context.client.post(
        "/api/v1/admin/workers",
        headers=headers[UserRole.ADMIN],
        json={
            "phone": "+77075550333",
            "full_name": "Другой работник",
            "employee_id": "OTHER-BANK-001",
        },
    )
    assert second.status_code == 201
    own = {"bank_name": "Halyk", "iban": "KZ86125KZT5004100100", "account_holder": "Оразғали Асан"}
    other = {
        "bank_name": "Other bank",
        "iban": "GB82WEST12345698765432",
        "account_holder": "Другой работник",
    }
    for user_id, account in ((str(worker_id), own), (second.json()["id"], other)):
        saved = await context.client.put(
            f"/api/v1/admin/workers/{user_id}/salary-account",
            headers=headers[UserRole.BOSS],
            json=account,
        )
        assert saved.status_code == 200
        denied = await context.client.get(
            f"/api/v1/admin/workers/{user_id}/salary-account", headers=headers[UserRole.WORKER]
        )
        assert denied.status_code == 403
    result = await context.client.get(
        own_path, headers=headers[UserRole.WORKER], params={"worker_id": second.json()["id"]}
    )
    assert result.json() == own
    assert "Other bank" not in result.text and other["iban"] not in result.text
    for role in (UserRole.ADMIN, UserRole.MASTER, UserRole.BOSS):
        assert (await context.client.get(own_path, headers=headers[role])).status_code == 403
    assert (await context.client.get(own_path)).status_code == 401
    for method in ("put", "delete"):
        result = await getattr(context.client, method)(
            own_path,
            headers=headers[UserRole.WORKER],
            **({"json": other} if method == "put" else {}),
        )
        assert result.status_code == 405
