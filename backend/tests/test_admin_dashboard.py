from datetime import time, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from conftest import AppContext, login
from sqlalchemy import select
from test_operations_api import auth, prepare_assignment

from backend.core.time import utc_now
from backend.identity.models import User, UserRole
from backend.management import dashboard
from backend.operations.models import (
    AttendanceRecord,
    AttendanceStatus,
    WorkDay,
    WorkDayStatus,
    WorkSchedule,
)


@pytest.mark.parametrize("role", list(UserRole))
async def test_operational_report_permission_matrix(context: AppContext, role) -> None:
    response = await context.client.get(
        "/api/v1/admin/dashboard?days=7", headers=auth(await login(context, role))
    )
    assert response.status_code == (200 if role == UserRole.ADMIN else 403)
    if role == UserRole.ADMIN:
        data = response.json()
        assert len(data["daily"]) == 7
        assert data["totals"]["arrived"] == 0
        assert data["totals"]["worked_minutes"] == 0
        assert all(row["worked_minutes"] == 0 for row in data["daily"])
        assert (
            await context.client.get(
                "/api/v1/admin/dashboard?days=91", headers=auth(await login(context, role))
            )
        ).status_code == 422


@pytest.mark.parametrize("role", list(UserRole))
async def test_boss_report_permission_matrix(context: AppContext, role) -> None:
    response = await context.client.get(
        "/api/v1/boss/dashboard?days=7", headers=auth(await login(context, role))
    )
    assert response.status_code == (200 if role == UserRole.BOSS else 403)
    if role == UserRole.BOSS:
        assert len(response.json()["daily"]) == 7
        invalid = await context.client.get(
            "/api/v1/boss/dashboard?days=91", headers=auth(await login(context, role))
        )
        assert invalid.status_code == 422


async def test_report_uses_persisted_attendance_and_only_approved_volume(
    context: AppContext, monkeypatch
) -> None:
    admin = await login(context, UserRole.ADMIN)
    worker_id, _, brigade_id, object_id = await prepare_assignment(context, admin)
    clock = utc_now().replace(hour=12, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(dashboard, "utc_now", lambda: clock)
    today = clock.date()
    async with context.database.session_factory() as session:
        actor = await session.scalar(select(User).where(User.role == UserRole.ADMIN))
        common = {"worker_user_id": UUID(worker_id), "created_by": actor.id, "created_at": clock}
        session.add(
            WorkSchedule(
                **common,
                brigade_id=UUID(brigade_id),
                object_id=UUID(object_id),
                work_date=today,
                shift_start=time(8),
                shift_end=time(20),
                updated_by=actor.id,
                updated_at=clock,
            )
        )
        for day, check_in, check_out, archived in [
            (today, clock - timedelta(hours=10, minutes=16), clock, False),
            (today - timedelta(days=2), clock - timedelta(days=2), None, False),
            (
                today - timedelta(days=3),
                clock - timedelta(days=3, hours=8),
                clock - timedelta(days=3),
                True,
            ),
            (
                today - timedelta(days=20),
                clock - timedelta(days=20, hours=8),
                clock - timedelta(days=20),
                False,
            ),
        ]:
            session.add(
                AttendanceRecord(
                    **common,
                    work_date=day,
                    check_in=check_in,
                    check_out=check_out,
                    status=AttendanceStatus.PRESENT,
                    updated_by=actor.id,
                    updated_at=clock,
                    archived_at=clock if archived else None,
                )
            )
        for offset, status, volume, archived in [
            (0, WorkDayStatus.APPROVED, "12.5", False),
            (1, WorkDayStatus.DRAFT, "999", False),
            (2, WorkDayStatus.REJECTED, "888", False),
            (3, WorkDayStatus.APPROVED, "777", True),
        ]:
            session.add(
                WorkDay(
                    **common,
                    object_id=UUID(object_id),
                    brigade_id=UUID(brigade_id),
                    work_date=today - timedelta(days=offset),
                    hours=Decimal(8),
                    m3=Decimal(volume),
                    rate_per_m3=Decimal(1),
                    daily_earnings=Decimal(0),
                    status=status,
                    updated_by=actor.id,
                    updated_at=clock,
                    archived_at=clock if archived else None,
                )
            )
        await session.commit()
    response = await context.client.get("/api/v1/admin/dashboard?days=7", headers=auth(admin))
    assert response.status_code == 200, response.text
    data = response.json()
    boss_response = await context.client.get(
        "/api/v1/boss/dashboard?days=7", headers=auth(await login(context, UserRole.BOSS))
    )
    assert boss_response.status_code == 200
    assert boss_response.json() == data
    assert data["active_workers"] == 1
    assert data["active_objects"] == 1
    assert data["totals"]["arrived"] == 2
    assert data["totals"]["worked_minutes"] == 616
    assert Decimal(str(data["totals"]["approved_volume_m3"])) == Decimal("12.5")
    assert data["needs_correction"] == 1
    assert data["today"]["arrived"] == data["today"]["finished"] == data["today"]["scheduled"] == 1
    assert data["daily"][-1]["worked_minutes"] == 616
    assert data["daily"][0]["worked_minutes"] == 0
    obj = next(row for row in data["objects"] if row["id"] == object_id)
    assert obj["worked_minutes"] == 616
    for field in ("phone", "password", "daily_earnings", "rate_per_m3"):
        assert field not in response.text


async def test_report_marks_absence_only_after_the_scheduled_shift_ends(
    context: AppContext, monkeypatch
) -> None:
    admin = await login(context, UserRole.ADMIN)
    worker_id, _, brigade_id, object_id = await prepare_assignment(context, admin)
    clock = utc_now().replace(hour=12, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(dashboard, "utc_now", lambda: clock)
    async with context.database.session_factory() as session:
        actor = await session.scalar(select(User).where(User.role == UserRole.ADMIN))
        session.add(
            WorkSchedule(
                worker_user_id=UUID(worker_id),
                brigade_id=UUID(brigade_id),
                object_id=UUID(object_id),
                work_date=clock.date(),
                shift_start=time(8),
                shift_end=time(20),
                created_by=actor.id,
                updated_by=actor.id,
                created_at=clock,
                updated_at=clock,
            )
        )
        await session.commit()
    during = (
        await context.client.get("/api/v1/admin/dashboard?days=7", headers=auth(admin))
    ).json()
    assert during["today"]["scheduled"] == during["today"]["waiting"] == 1
    assert during["today"]["absent"] == 0
    clock += timedelta(hours=6)
    after = (await context.client.get("/api/v1/admin/dashboard?days=7", headers=auth(admin))).json()
    assert after["today"]["waiting"] == 0
    assert after["today"]["absent"] == after["totals"]["absent"] == 1
