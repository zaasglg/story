from conftest import login

from backend.identity.models import UserRole


async def test_boss_audit_has_all_roles_and_before_after_without_pii(context):
    admin = await login(context, UserRole.ADMIN)
    boss = await login(context, UserRole.BOSS)
    worker = await login(context, UserRole.WORKER)
    ah = {"Authorization": f"Bearer {admin['access_token']}"}
    bh = {"Authorization": f"Bearer {boss['access_token']}"}
    cities = (await context.client.get("/api/v1/cities", headers=bh)).json()
    revenue = await context.client.post(
        "/api/v1/boss/revenues",
        headers=bh,
        json={
            "city_id": cities[0]["id"],
            "revenue_date": "2026-10-01",
            "amount": "500",
            "currency": "RUB",
            "category": "Synthetic",
        },
    )
    assert revenue.status_code == 201
    updated = await context.client.put(
        f"/api/v1/boss/revenues/{revenue.json()['id']}",
        headers=bh,
        json={
            "city_id": cities[0]["id"],
            "revenue_date": "2026-10-01",
            "amount": "700",
            "currency": "RUB",
            "category": "Synthetic",
        },
    )
    assert updated.status_code == 200
    records = (await context.client.get("/api/v1/boss/audit?limit=100", headers=bh)).json()
    assert any(row["action"] == "REVENUE_CREATED" for row in records)
    diff = next(row for row in records if row.get("old_value", {}).get("amount") == "500.00")
    assert diff["new_value"]["amount"] in ("700", "700.00")
    assert diff["actor_user_id"] == boss["user"]["id"]
    for headers in [ah, {"Authorization": f"Bearer {worker['access_token']}"}]:
        assert (await context.client.get("/api/v1/boss/audit", headers=headers)).status_code == 403
    from sqlalchemy import select

    from backend.identity.models import AuditLog

    async with context.database.session_factory() as session:
        event = await session.scalar(select(AuditLog).where(AuditLog.action == "REVENUE_CREATED"))
        event.new_value = {
            "amount": "500",
            "nested": {
                "password": "SYNTHETIC-SECRET",
                "iban": "SYNTHETIC-SECRET",
                "passport_number": "SYNTHETIC-SECRET",
            },
        }
        await session.commit()
    assert (
        "SYNTHETIC-SECRET"
        not in (await context.client.get("/api/v1/boss/audit?limit=100", headers=bh)).text
    )
