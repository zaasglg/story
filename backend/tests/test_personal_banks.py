from conftest import login
from sqlalchemy import select

from backend.core.time import utc_now
from backend.finance.models import PersonalBankAccount
from backend.identity.models import AccountStatus, AuditLog, EmployeeProfile, User, UserRole
from backend.identity.security import hash_password


async def test_personal_accounts_are_encrypted_and_isolated_between_roles_and_owners(context):
    headers = {}
    for role in UserRole:
        tokens = await login(context, role)
        headers[role] = {"Authorization": f"Bearer {tokens['access_token']}"}
    path = "/api/v1/boss/personal-bank-accounts"
    account = {
        "name": "Личный зарплатный",
        "bank_name": "Halyk",
        "iban": "KZ86 125K ZT50 0410 0100",
        "account_holder": "Оразғали Асан",
    }
    created = await context.client.post(path, json=account, headers=headers[UserRole.BOSS])
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]
    iban = created.json()["iban"]
    assert iban == "KZ86125KZT5004100100"
    for role in (UserRole.ADMIN, UserRole.MASTER, UserRole.WORKER):
        assert (await context.client.get(path, headers=headers[role])).status_code == 403
        assert (
            await context.client.post(path, headers=headers[role], json=account)
        ).status_code == 403
        assert (
            await context.client.put(f"{path}/{item_id}", headers=headers[role], json=account)
        ).status_code == 403
        assert (
            await context.client.delete(f"{path}/{item_id}", headers=headers[role])
        ).status_code == 403
    own = await context.client.get(path, headers=headers[UserRole.BOSS])
    assert own.json()[0]["iban"] == iban
    company = await context.client.get("/api/v1/boss/bank-accounts", headers=headers[UserRole.BOSS])
    assert company.json() == []
    async with context.database.session_factory() as session:
        record = await session.scalar(select(PersonalBankAccount))
        assert iban not in record.details_encrypted
        assert "Halyk" not in record.details_encrypted
        assert account["name"] not in record.details_encrypted
        logs = (await session.scalars(select(AuditLog))).all()
        audit_values = repr([(x.new_value, x.old_value, x.metadata_json) for x in logs])
        assert iban not in audit_values and "Halyk" not in audit_values
        now = utc_now()
        other = User(
            phone="+77001112233",
            password_hash=hash_password("Test-password-123"),
            role=UserRole.BOSS,
            status=AccountStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        session.add(other)
        await session.flush()
        session.add(
            EmployeeProfile(
                user_id=other.id,
                full_name="Другой руководитель",
                employee_id="BOSS-SECOND",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    auth = await context.client.post(
        "/api/v1/auth/login",
        json={"phone": "+77001112233", "password": "Test-password-123", "device_name": "pytest"},
    )
    assert auth.status_code == 200
    other_headers = {"Authorization": f"Bearer {auth.json()['access_token']}"}
    assert (await context.client.get(path, headers=other_headers)).json() == []
    assert (
        await context.client.put(f"{path}/{item_id}", headers=other_headers, json=account)
    ).status_code == 404
    assert (
        await context.client.delete(f"{path}/{item_id}", headers=other_headers)
    ).status_code == 404
    invalid = await context.client.post(
        path, headers=headers[UserRole.BOSS], json={**account, "owner_user_id": str(other.id)}
    )
    assert invalid.status_code == 422
    updated = await context.client.put(
        f"{path}/{item_id}",
        headers=headers[UserRole.BOSS],
        json={**account, "bank_name": "Другой банк"},
    )
    assert updated.status_code == 200 and updated.json()["bank_name"] == "Другой банк"
    assert (
        await context.client.delete(f"{path}/{item_id}", headers=headers[UserRole.BOSS])
    ).status_code == 204
    assert (await context.client.get(path, headers=headers[UserRole.BOSS])).json() == []
