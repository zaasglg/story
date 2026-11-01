from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from conftest import login
from sqlalchemy import select
from test_operations_api import auth, prepare_assignment

from backend.identity.models import EmployeeProfile, UserRole
from backend.workforce.models import City, WorkObject


@pytest.mark.parametrize(
    "clock", [datetime(2026, 1, 1, 22, 30, tzinfo=UTC), datetime(2026, 12, 31, 22, 30, tzinfo=UTC)]
)
async def test_local_midnight_month_year_and_object_timezone(context, monkeypatch, clock):
    admin = await login(context, UserRole.ADMIN)
    worker = await login(context, UserRole.WORKER)
    wid, _, _, oid = await prepare_assignment(context, admin)
    # Profile fallback is UTC, but the active object is in Moscow.
    async with context.database.session_factory() as session:
        obj = await session.get(WorkObject, UUID(oid))
        city = await session.get(City, obj.city_id)
        city.timezone = "Europe/Moscow"
        fallback = City(name="Synthetic UTC", timezone="UTC", created_at=clock)
        session.add(fallback)
        await session.flush()
        profile = await session.scalar(
            select(EmployeeProfile).where(EmployeeProfile.user_id == UUID(wid))
        )
        profile.city_id = fallback.id
        await session.commit()
    monkeypatch.setattr("backend.operations.production.utc_now", lambda: clock)
    response = await context.client.post(
        "/api/v1/workers/me/production", headers=auth(worker), json={"volume_m3": "7"}
    )
    assert response.status_code == 201
    journal = (
        await context.client.get("/api/v1/workers/me/production", headers=auth(worker))
    ).json()
    expected_day = (
        clock.astimezone(__import__("zoneinfo").ZoneInfo("Europe/Moscow")).date().isoformat()
    )
    assert journal["date"] == expected_day
    assert Decimal(journal["today_total"]) == 7
    stats = (
        await context.client.get(
            "/api/v1/workers/me/production/stats?period=month", headers=auth(worker)
        )
    ).json()
    assert Decimal(next(r["volume_m3"] for r in stats["series"] if r["date"] == expected_day)) == 7
    assert Decimal(stats["total_m3"]) == 7
