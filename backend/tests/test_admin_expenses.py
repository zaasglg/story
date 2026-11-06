from conftest import login
from test_equipment_expenses import auth

from backend.identity.models import UserRole


async def test_admin_can_add_and_read_expenses_but_not_income_or_approve(context):
    admin = auth(await login(context, UserRole.ADMIN))
    city = (await context.client.get("/api/v1/cities", headers=admin)).json()[0]["id"]
    created = await context.client.post(
        "/api/v1/admin/expenses",
        headers=admin,
        json={
            "city_id": city,
            "expense_date": "2026-11-06",
            "amount": "450",
            "currency": "RUB",
            "category": "material",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "DRAFT"
    summary = await context.client.get(
        "/api/v1/admin/expenses-summary?date_from=2026-11-01&date_to=2026-11-30", headers=admin
    )
    assert summary.status_code == 200
    assert summary.json() == [
        {"category": "material", "currency": "RUB", "total": "450.00", "count": 1}
    ]
    filtered = await context.client.get(
        "/api/v1/admin/catalog/expenses?category=transport", headers=admin
    )
    assert filtered.json()["total"] == 0
    assert (await context.client.get("/api/v1/boss/revenues", headers=admin)).status_code == 403
    assert (
        await context.client.post(
            "/api/v1/boss/revenues",
            headers=admin,
            json={
                "city_id": city,
                "revenue_date": "2026-11-06",
                "amount": "100",
                "currency": "RUB",
                "category": "other",
            },
        )
    ).status_code == 403
    assert (
        await context.client.post(
            f"/api/v1/boss/expenses/{created.json()['id']}/approve",
            headers=admin,
            json={"expected_revision": 0},
        )
    ).status_code == 403
