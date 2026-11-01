from decimal import Decimal

from conftest import AppContext, login
from test_operations_api import auth

from backend.identity.models import UserRole


async def test_worker_records_volume_and_reads_statistics(context: AppContext) -> None:
    worker = auth(await login(context, UserRole.WORKER))
    first = await context.client.post(
        "/api/v1/workers/me/production",
        headers=worker,
        json={"volume_m3": "4.50", "note": "Section 3"},
    )
    second = await context.client.post(
        "/api/v1/workers/me/production",
        headers=worker,
        json={"volume_m3": "3.20"},
    )
    assert first.status_code == second.status_code == 201

    journal = (
        await context.client.get("/api/v1/workers/me/production", headers=worker)
    ).json()
    assert Decimal(journal["today_total"]) == Decimal("7.70")
    assert [Decimal(row["volume_m3"]) for row in journal["entries"]] == [
        Decimal("3.20"),
        Decimal("4.50"),
    ]

    stats = (
        await context.client.get(
            "/api/v1/workers/me/production/stats?period=week", headers=worker
        )
    ).json()
    assert Decimal(stats["total_m3"]) == Decimal("7.70")
    assert stats["active_days"] == 1


async def test_production_is_worker_only_and_rejects_zero(context: AppContext) -> None:
    admin = auth(await login(context, UserRole.ADMIN))
    worker = auth(await login(context, UserRole.WORKER))
    denied = await context.client.get("/api/v1/workers/me/production", headers=admin)
    invalid = await context.client.post(
        "/api/v1/workers/me/production", headers=worker, json={"volume_m3": 0}
    )
    assert denied.status_code == 403
    assert invalid.status_code == 422
