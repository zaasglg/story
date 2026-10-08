from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from conftest import AppContext, login
from sqlalchemy import select

from backend.core.errors import ApiError
from backend.identity.models import EmployeeProfile, User, UserRole
from backend.operations.models import TariffHistory, WorkDay
from backend.operations.routes import validate_context
from backend.workforce.models import WorkerAssignment


def auth(tokens: dict[str, object]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_closed_assignment_dates_and_master_transfer(context: AppContext) -> None:
    admin = await login(context, UserRole.ADMIN)
    master = await login(context, UserRole.MASTER)
    worker_id, _, brigade_id, object_id = await prepare_assignment(context, admin)
    async with context.database.session_factory() as session:
        assignment = await session.scalar(
            select(WorkerAssignment).where(WorkerAssignment.worker_user_id == UUID(worker_id))
        )
        assert assignment is not None
        assignment.effective_to = datetime(2026, 10, 2, 0, tzinfo=UTC)
        await session.commit()
        await validate_context(
            session, UUID(worker_id), UUID(brigade_id), UUID(object_id), date(2026, 10, 1)
        )
        with pytest.raises(ApiError) as error:
            await validate_context(
                session, UUID(worker_id), UUID(brigade_id), UUID(object_id), date(2026, 10, 3)
            )
        assert error.value.status_code == 422
        with pytest.raises(ApiError):
            await validate_context(
                session, UUID(worker_id), UUID(brigade_id), UUID(object_id), date(2025, 12, 31)
            )
        profile = await session.scalar(
            select(EmployeeProfile).where(EmployeeProfile.user_id == UUID(worker_id))
        )
        assert profile is not None
        profile.master_user_id = None
        await session.commit()
    result = await context.client.get(
        f"/api/v1/master/team/{worker_id}/work-days", headers=auth(master)
    )
    assert result.status_code == 404


async def prepare_assignment(
    context: AppContext, admin_tokens: dict[str, object]
) -> tuple[str, str, str, str]:
    city_id = (await context.client.get("/api/v1/cities", headers=auth(admin_tokens))).json()[0][
        "id"
    ]
    async with context.database.session_factory() as session:
        master_id = await session.scalar(select(User.id).where(User.role == UserRole.MASTER))
        worker_id = await session.scalar(select(User.id).where(User.role == UserRole.WORKER))
    assert master_id and worker_id
    brigade = await context.client.post(
        "/api/v1/admin/brigades",
        headers=auth(admin_tokens),
        json={
            "name": "Operations Brigade",
            "city_id": city_id,
            "master_user_id": str(master_id),
        },
    )
    assert brigade.status_code == 201, brigade.text
    obj = await context.client.post(
        "/api/v1/admin/objects",
        headers=auth(admin_tokens),
        json={
            "name": "Operations Object",
            "city_id": city_id,
            "address": "Test address",
            "status": "ACTIVE",
        },
    )
    assert obj.status_code == 201, obj.text
    assignment = await context.client.post(
        f"/api/v1/admin/workers/{worker_id}/assignment",
        headers=auth(admin_tokens),
        json={"brigade_id": brigade.json()["id"], "object_id": obj.json()["id"]},
    )
    assert assignment.status_code == 200, assignment.text
    authorized = await context.client.post(
        "/api/v1/admin/work-authorizations",
        headers=auth(admin_tokens),
        json={
            "worker_user_id": str(worker_id),
            "valid_from": "2026-01-01",
            "valid_until": "2030-12-31",
            "reference": "Test authorization",
        },
    )
    assert authorized.status_code == 201
    async with context.database.session_factory() as session:
        row = await session.scalar(
            select(WorkerAssignment).where(
                WorkerAssignment.worker_user_id == UUID(str(worker_id)),
                WorkerAssignment.effective_to.is_(None),
            )
        )
        assert row is not None
        row.effective_from = datetime(2026, 1, 1, tzinfo=UTC)
        await session.commit()
    return str(worker_id), str(master_id), brigade.json()["id"], obj.json()["id"]


async def test_historical_tariff_and_role_safe_work_days(context: AppContext) -> None:
    admin_tokens = await login(context, UserRole.ADMIN)
    worker_tokens = await login(context, UserRole.WORKER)
    master_tokens = await login(context, UserRole.MASTER)
    boss_tokens = await login(context, UserRole.BOSS)
    worker_id, _, brigade_id, object_id = await prepare_assignment(context, admin_tokens)

    tariff = await context.client.post(
        "/api/v1/admin/tariffs",
        headers=auth(admin_tokens),
        json={
            "object_id": object_id,
            "rate_per_m3": "100.00",
            "currency": "RUB",
            "effective_from": "2026-01-01",
        },
    )
    assert tariff.status_code == 201, tariff.text

    work_day = await context.client.post(
        "/api/v1/admin/work-days",
        headers=auth(admin_tokens),
        json={
            "worker_user_id": worker_id,
            "object_id": object_id,
            "brigade_id": brigade_id,
            "work_date": "2026-10-01",
            "hours": "8.00",
            "m3": "2.50",
            "status": "APPROVED",
        },
    )
    assert work_day.status_code == 201, work_day.text
    assert work_day.json()["rate_per_m3"] == "100.00"
    assert work_day.json()["daily_earnings"] == "250.00"
    work_day_id = work_day.json()["id"]

    new_tariff = await context.client.post(
        "/api/v1/admin/tariffs",
        headers=auth(admin_tokens),
        json={
            "object_id": object_id,
            "rate_per_m3": "120.00",
            "currency": "RUB",
            "effective_from": "2026-10-02",
        },
    )
    assert new_tariff.status_code == 201, new_tariff.text

    async with context.database.session_factory() as session:
        stored_day = await session.get(WorkDay, UUID(work_day_id))
        tariffs = (
            await session.scalars(select(TariffHistory).order_by(TariffHistory.effective_from))
        ).all()
    assert stored_day is not None
    assert str(stored_day.rate_per_m3) == "100.00"
    assert str(stored_day.daily_earnings) == "250.00"
    assert tariffs[0].effective_to.isoformat() == "2026-10-01"

    own = await context.client.get("/api/v1/workers/me/work-days", headers=auth(worker_tokens))
    assert own.status_code == 200
    assert own.json()[0]["daily_earnings"] == "250.00"

    team = await context.client.get(
        f"/api/v1/master/team/{worker_id}/work-days", headers=auth(master_tokens)
    )
    assert team.status_code == 200
    assert team.json()[0]["m3"] == "2.50"
    assert "rate_per_m3" not in team.json()[0]
    assert "daily_earnings" not in team.json()[0]
    assert "250.00" not in team.text

    master_edit = await context.client.patch(
        f"/api/v1/admin/work-days/{work_day_id}",
        headers=auth(master_tokens),
        json={"m3": "99.00"},
    )
    boss_edit = await context.client.patch(
        f"/api/v1/admin/work-days/{work_day_id}",
        headers=auth(boss_tokens),
        json={"m3": "99.00"},
    )
    assert master_edit.status_code == 403
    assert boss_edit.status_code == 403

    updated = await context.client.patch(
        f"/api/v1/admin/work-days/{work_day_id}",
        headers=auth(admin_tokens),
        json={"m3": "3.00"},
    )
    assert updated.status_code == 200
    assert updated.json()["rate_per_m3"] == "100.00"
    assert updated.json()["daily_earnings"] == "300.00"


async def test_schedule_and_attendance_admin_crud(context: AppContext) -> None:
    admin_tokens = await login(context, UserRole.ADMIN)
    worker_tokens = await login(context, UserRole.WORKER)
    worker_id, _, brigade_id, object_id = await prepare_assignment(context, admin_tokens)

    schedule = await context.client.post(
        "/api/v1/admin/schedules",
        headers=auth(admin_tokens),
        json={
            "worker_user_id": worker_id,
            "brigade_id": brigade_id,
            "object_id": object_id,
            "work_date": "2026-10-05",
            "shift_start": "08:00:00",
            "shift_end": "17:00:00",
        },
    )
    assert schedule.status_code == 201, schedule.text

    own_schedule = await context.client.get(
        "/api/v1/workers/me/schedules", headers=auth(worker_tokens)
    )
    assert own_schedule.status_code == 200
    assert own_schedule.json()[0]["object_name"] == "Operations Object"

    attendance = await context.client.post(
        "/api/v1/admin/attendance",
        headers=auth(admin_tokens),
        json={
            "worker_user_id": worker_id,
            "work_date": "2026-10-05",
            "check_in": "2026-10-05T08:03:00Z",
            "status": "LATE",
            "comment": "Three minutes late",
        },
    )
    assert attendance.status_code == 201, attendance.text
    attendance_id = attendance.json()["id"]

    corrected = await context.client.patch(
        f"/api/v1/admin/attendance/{attendance_id}",
        headers=auth(admin_tokens),
        json={"status": "PRESENT", "comment": "Approved by administrator"},
    )
    assert corrected.status_code == 200
    assert corrected.json()["status"] == "PRESENT"

    denied = await context.client.post(
        "/api/v1/admin/attendance",
        headers=auth(worker_tokens),
        json={
            "worker_user_id": worker_id,
            "work_date": "2026-10-06",
            "status": "PRESENT",
        },
    )
    assert denied.status_code == 403
