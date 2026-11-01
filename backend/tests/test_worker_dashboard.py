from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from conftest import AppContext, login
from sqlalchemy import select
from test_operations_api import auth, prepare_assignment

from backend.identity.models import User, UserRole
from backend.operations import dashboard
from backend.operations.models import WorkDay


@pytest.mark.parametrize("role", list(UserRole))
async def test_dashboard_is_worker_only(context: AppContext, role):
    response = await context.client.get(
        "/api/v1/workers/me/dashboard", headers=auth(await login(context, role))
    )
    assert response.status_code == (200 if role == UserRole.WORKER else 403)


async def test_dashboard_city_date_overnight_plan_and_real_attendance(
    context: AppContext, monkeypatch
):
    now = datetime(2026, 10, 29, 22, 30, tzinfo=UTC)
    monkeypatch.setattr(dashboard, "utc_now", lambda: now)
    admin = await login(context, UserRole.ADMIN)
    worker = auth(await login(context, UserRole.WORKER))
    worker_id, _, brigade_id, object_id = await prepare_assignment(context, admin)
    path = "/api/v1/workers/me/dashboard"
    initial = (await context.client.get(path, headers=worker)).json()
    assert initial["work_date"] == "2026-10-30"
    assert initial["state"] == "NONE"
    assert initial["planned_hours"] is None
    scheduled = await context.client.post(
        "/api/v1/admin/schedules",
        headers=auth(admin),
        json={
            "worker_user_id": worker_id,
            "brigade_id": brigade_id,
            "object_id": object_id,
            "work_date": "2026-10-30",
            "shift_start": "22:00",
            "shift_end": "06:00",
        },
    )
    assert scheduled.status_code == 201, scheduled.text
    data = (await context.client.get(path, headers=worker)).json()
    assert data["state"] == "SCHEDULED"
    assert Decimal(str(data["planned_hours"])) == Decimal("8")
    assert data["utc_offset_minutes"] == 180
    recorded = await context.client.post(
        "/api/v1/admin/attendance",
        headers=auth(admin),
        json={
            "worker_user_id": worker_id,
            "work_date": "2026-10-30",
            "status": "PRESENT",
            "check_in": "2026-10-29T21:00:00Z",
        },
    )
    assert recorded.status_code == 201, recorded.text
    async with context.database.session_factory() as session:
        admin_id = await session.scalar(select(User.id).where(User.role == UserRole.ADMIN))
        session.add(
            WorkDay(
                worker_user_id=UUID(worker_id),
                object_id=UUID(object_id),
                brigade_id=UUID(brigade_id),
                work_date=datetime(2026, 10, 30).date(),
                hours=Decimal("2.50"),
                m3=Decimal("12.50"),
                rate_per_m3=Decimal("100"),
                daily_earnings=Decimal("1250"),
                currency="RUB",
                status="DRAFT",
                created_by=admin_id,
                updated_by=admin_id,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    data = (await context.client.get(path, headers=worker)).json()
    assert data["state"] == "WORKING"
    assert Decimal(str(data["m3"])) == Decimal("12.50")
    assert Decimal(str(data["hours"])) == Decimal("2.50")
    assert data["check_in"].endswith("+03:00")
    ended = await context.client.patch(
        f"/api/v1/admin/attendance/{recorded.json()['id']}",
        headers=auth(admin),
        json={"check_out": "2026-10-29T22:00:00Z"},
    )
    assert ended.status_code == 200, ended.text
    assert (await context.client.get(path, headers=worker)).json()["state"] == "FINISHED"
