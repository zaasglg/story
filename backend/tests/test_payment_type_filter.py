from conftest import login
from test_equipment_expenses import auth

from backend.identity.models import UserRole


async def test_catalog_payment_type_filters_before_count_and_pagination(context):
    admin = auth(await login(context, UserRole.ADMIN))
    workers = await context.client.get("/api/v1/admin/workers", headers=admin)
    for kind in ("BONUS", "PENALTY"):
        response = await context.client.post(
            "/api/v1/admin/financial-requests",
            headers={**admin, "Idempotency-Key": kind},
            json={
                "worker_user_id": workers.json()[0]["id"],
                "type": kind,
                "amount": "100",
                "period": "2026-11-01",
                "reason": "Test",
            },
        )
        assert response.status_code == 201
    page = await context.client.get(
        "/api/v1/admin/catalog/financial-requests?type=PENALTY&limit=1", headers=admin
    )
    assert page.status_code == 200
    assert page.json()["total"] == 1
    assert [row["type"] for row in page.json()["items"]] == ["PENALTY"]
    empty = await context.client.get(
        "/api/v1/admin/catalog/financial-requests?type=ADVANCE", headers=admin
    )
    assert empty.json()["total"] == 0
