from conftest import AppContext, login
from sqlalchemy import select

from backend.identity.models import User, UserRole


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_stock_issue_return_and_permissions(context: AppContext) -> None:
    admin = auth(await login(context, UserRole.ADMIN))
    worker = auth(await login(context, UserRole.WORKER))
    boss = auth(await login(context, UserRole.BOSS))
    master = auth(await login(context, UserRole.MASTER))
    async with context.database.session_factory() as session:
        worker_id = await session.scalar(select(User.id).where(User.role == UserRole.WORKER))
    result = await context.client.post(
        "/api/v1/admin/equipment", headers=admin, json={"name": "Helmet", "quantity": 2}
    )
    assert result.status_code == 201, result.text
    item_id = result.json()["id"]
    payload = {
        "equipment_id": item_id,
        "worker_user_id": str(worker_id),
        "quantity": 2,
        "issue_date": "2026-10-05",
    }
    issued = await context.client.post(
        "/api/v1/admin/equipment/issues", headers=admin, json=payload
    )
    assert issued.status_code == 201, issued.text
    issue_id = issued.json()["id"]
    shortage = await context.client.post(
        "/api/v1/admin/equipment/issues", headers=admin, json=payload
    )
    assert shortage.status_code == 409
    archive = await context.client.delete(f"/api/v1/admin/equipment/{item_id}", headers=admin)
    assert archive.status_code == 409
    own = await context.client.get("/api/v1/workers/me/equipment", headers=worker)
    assert own.status_code == 200
    assert len(own.json()) == 1
    for headers in (worker, boss, master):
        denied = await context.client.post(
            "/api/v1/admin/equipment/issues", headers=headers, json=payload
        )
        assert denied.status_code == 403
    returned = await context.client.post(
        f"/api/v1/admin/equipment/issues/{issue_id}/return",
        headers=admin,
        json={"return_date": "2026-10-06"},
    )
    assert returned.status_code == 200, returned.text
    duplicate = await context.client.post(
        f"/api/v1/admin/equipment/issues/{issue_id}/return",
        headers=admin,
        json={"return_date": "2026-10-06"},
    )
    assert duplicate.status_code == 409
    stock = await context.client.get("/api/v1/admin/equipment", headers=admin)
    assert stock.json()[0]["available_quantity"] == 2
    other = await context.client.post(
        "/api/v1/admin/workers",
        headers=admin,
        json={"phone": "+77075550123", "full_name": "Other Worker", "employee_id": "OTHER-ISSUE"},
    )
    assert other.status_code == 201
    payload["worker_user_id"] = other.json()["id"]
    payload["quantity"] = 1
    other_issue = await context.client.post(
        "/api/v1/admin/equipment/issues", headers=admin, json=payload
    )
    assert other_issue.status_code == 201
    own = await context.client.get("/api/v1/workers/me/equipment", headers=worker)
    assert len(own.json()) == 1
    assert own.json()[0]["worker_user_id"] == str(worker_id)
    movements = await context.client.get(
        f"/api/v1/admin/equipment/{item_id}/movements", headers=admin
    )
    assert movements.status_code == 200
    assert sum(row["delta"] for row in movements.json()) == 1


async def test_expense_reports_currency_archive_and_role_scope(context: AppContext) -> None:
    admin = auth(await login(context, UserRole.ADMIN))
    boss = auth(await login(context, UserRole.BOSS))
    worker = auth(await login(context, UserRole.WORKER))
    master = auth(await login(context, UserRole.MASTER))
    cities = await context.client.get("/api/v1/cities", headers=boss)
    city_id = cities.json()[0]["id"]
    ids = []
    for amount, currency in (("80.00", "RUB"), ("35.00", "RUB"), ("10.00", "KZT")):
        created = await context.client.post(
            "/api/v1/admin/expenses",
            headers=boss,
            json={
                "city_id": city_id,
                "expense_date": "2026-10-05",
                "amount": amount,
                "currency": currency,
                "category": "material",
            },
        )
        assert created.status_code == 201, created.text
        ids.append(created.json()["id"])
    report = await context.client.get(
        "/api/v1/admin/expenses/monthly?year=2026&month=10", headers=boss
    )
    assert report.status_code == 200, report.text
    totals = {row["currency"]: row["total"] for row in report.json()}
    assert totals == {"KZT": "10.00", "RUB": "115.00"}
    assert "revenue" not in report.text and "profit" not in report.text
    for headers in (admin, worker, master):
        denied = await context.client.get("/api/v1/admin/expenses", headers=headers)
        assert denied.status_code == 403
    removed = await context.client.delete(f"/api/v1/boss/expenses/{ids[0]}", headers=boss)
    assert removed.status_code == 204
    daily = await context.client.get("/api/v1/admin/expenses/daily?day=2026-10-05", headers=boss)
    assert {row["currency"]: row["total"] for row in daily.json()} == {
        "KZT": "10.00",
        "RUB": "35.00",
    }
