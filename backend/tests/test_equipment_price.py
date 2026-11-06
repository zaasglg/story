from decimal import Decimal

from conftest import login
from test_equipment_expenses import auth

from backend.identity.models import UserRole


async def test_equipment_price_persists_and_can_be_edited(context):
    admin = auth(await login(context, UserRole.ADMIN))
    boss = auth(await login(context, UserRole.BOSS))
    created = await context.client.post(
        "/api/v1/admin/equipment",
        headers=admin,
        json={"name": "Drill", "quantity": 2, "price": "12500.50", "currency": "KZT"},
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["price"] == "12500.50"
    listing = await context.client.get("/api/v1/boss/equipment", headers=boss)
    assert next(row for row in listing.json() if row["id"] == item["id"])["currency"] == "KZT"
    updated = await context.client.patch(
        f"/api/v1/admin/equipment/{item['id']}",
        headers=admin,
        json={"name": "Drill", "price": "13000", "currency": "KZT"},
    )
    assert updated.status_code == 200
    assert Decimal(updated.json()["price"]) == Decimal("13000.00")
    invalid = await context.client.post(
        "/api/v1/admin/equipment",
        headers=admin,
        json={"name": "Invalid", "quantity": 1, "price": "-1"},
    )
    assert invalid.status_code == 422
