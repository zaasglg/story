from conftest import AppContext, login
from sqlalchemy import select

from backend.finance.models import CompanyBankAccount
from backend.identity.models import AuditLog, UserRole


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_boss_finance_approval_totals_and_forbidden_roles(context: AppContext) -> None:
    boss = auth(await login(context, UserRole.BOSS))
    admin = auth(await login(context, UserRole.ADMIN))
    worker = auth(await login(context, UserRole.WORKER))
    master = auth(await login(context, UserRole.MASTER))
    city = (await context.client.get("/api/v1/cities", headers=admin)).json()[0]["id"]
    obj = await context.client.post(
        "/api/v1/admin/objects",
        headers=admin,
        json={"name": "Profit Object", "city_id": city, "address": "Lipetsk"},
    )
    object_id = obj.json()["id"]
    revenue_payload = {
        "city_id": city,
        "object_id": object_id,
        "revenue_date": "2026-09-01",
        "amount": "350000.00",
        "currency": "RUB",
        "category": "contract",
    }
    revenue = await context.client.post("/api/v1/boss/revenues", headers=boss, json=revenue_payload)
    assert revenue.status_code == 201, revenue.text
    expense_payload = {
        "city_id": city,
        "object_id": object_id,
        "expense_date": "2026-09-01",
        "amount": "220000.00",
        "currency": "RUB",
        "category": "material",
    }
    expense = await context.client.post("/api/v1/boss/expenses", headers=boss, json=expense_payload)
    assert expense.status_code == 201
    draft = await context.client.get("/api/v1/boss/profit", headers=boss)
    assert draft.json() == []
    for headers in (admin, worker, master):
        for path in (
            "/boss/revenues",
            "/boss/finance",
            "/boss/profit",
            "/boss/bank-accounts",
            f"/boss/finance/object/{object_id}",
        ):
            denied = await context.client.get(f"/api/v1{path}", headers=headers)
            assert denied.status_code == 403
        denied = await context.client.post(
            "/api/v1/boss/revenues", headers=headers, json=revenue_payload
        )
        assert denied.status_code == 403
        denied = await context.client.post(
            f"/api/v1/boss/expenses/{expense.json()['id']}/approve", headers=headers
        )
        assert denied.status_code == 403
    approved_revenue = await context.client.post(
        f"/api/v1/boss/revenues/{revenue.json()['id']}/approve", headers=boss
    )
    assert approved_revenue.status_code == 200
    approved_expense = await context.client.post(
        f"/api/v1/boss/expenses/{expense.json()['id']}/approve",
        headers=boss,
        json={"expected_revision": expense.json()["revision"]},
    )
    assert approved_expense.status_code == 200
    profit = await context.client.get(f"/api/v1/boss/finance/object/{object_id}", headers=boss)
    assert profit.json() == [
        {
            "currency": "RUB",
            "approved_revenue": "350000.00",
            "approved_expenses": "220000.00",
            "expense_breakdown": [{"category": "material", "amount": "220000.00"}],
            "net_profit": "130000.00",
        }
    ]
    updated = await context.client.put(
        f"/api/v1/boss/expenses/{expense.json()['id']}",
        headers=boss,
        json={**expense_payload, "amount": "230000.00"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "DRAFT"
    profit = await context.client.get("/api/v1/boss/profit", headers=boss)
    assert profit.json()[0]["approved_expenses"] == "0.00"
    filtered = await context.client.get("/api/v1/boss/profit?date_from=2026-10-01", headers=boss)
    assert filtered.json() == []


async def test_bank_ciphertext_and_redacted_audit(context: AppContext) -> None:
    boss = auth(await login(context, UserRole.BOSS))
    iban = "GB82WEST12345698765432"
    created = await context.client.post(
        "/api/v1/boss/bank-accounts", headers=boss, json={"name": "Company", "iban": iban}
    )
    assert created.status_code == 201, created.text
    async with context.database.session_factory() as session:
        bank = await session.scalar(select(CompanyBankAccount))
        audits = (await session.scalars(select(AuditLog))).all()
    assert bank is not None and bank.iban_encrypted != iban
    assert iban not in repr([(row.new_value, row.old_value, row.metadata_json) for row in audits])
    read = await context.client.get("/api/v1/boss/bank-accounts", headers=boss)
    assert read.json()[0]["iban"] == iban
    archived = await context.client.delete(
        f"/api/v1/boss/bank-accounts/{created.json()['id']}", headers=boss
    )
    assert archived.status_code == 204
    read = await context.client.get("/api/v1/boss/bank-accounts", headers=boss)
    assert read.json() == []
