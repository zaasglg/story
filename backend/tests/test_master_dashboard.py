from datetime import timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from conftest import AppContext, login
from sqlalchemy import select
from test_operations_api import auth, prepare_assignment

from backend.access import master_dashboard, shifts
from backend.access import routes as pass_routes
from backend.core.time import utc_now
from backend.identity.models import AccountStatus, EmployeeProfile, User, UserRole
from backend.identity.security import hash_password
from backend.workforce.models import Brigade, WorkerAssignment


@pytest.mark.parametrize("role", list(UserRole))
async def test_today_dashboard_role_matrix(context: AppContext, role):
    response = await context.client.get(
        "/api/v1/master/attendance-summary", headers=auth(await login(context, role))
    )
    assert response.status_code == (200 if role == UserRole.MASTER else 403)
    if role == UserRole.MASTER:
        assert response.json()["items"] == []
        assert response.json()["total"] == 0
        assert (
            await context.client.get(
                "/api/v1/master/attendance-summary?limit=101",
                headers=auth(await login(context, role)),
            )
        ).status_code == 422


async def test_schedule_scan_statuses_and_full_counts_with_pagination(
    context: AppContext, monkeypatch
):
    admin = await login(context, UserRole.ADMIN)
    worker_id, _, brigade_id, object_id = await prepare_assignment(context, admin)
    master = auth(await login(context, UserRole.MASTER))
    worker = auth(await login(context, UserRole.WORKER))
    clock = utc_now().replace(hour=8, minute=0, second=0, microsecond=0)
    for module in (master_dashboard, shifts, pass_routes):
        monkeypatch.setattr(module, "utc_now", lambda: clock)
    day = clock.astimezone(ZoneInfo("Europe/Moscow")).date()
    plan = await context.client.post(
        "/api/v1/admin/schedules",
        headers=auth(admin),
        json={
            "worker_user_id": worker_id,
            "brigade_id": brigade_id,
            "object_id": object_id,
            "work_date": str(day),
            "shift_start": "08:00",
            "shift_end": "20:00",
        },
    )
    assert plan.status_code == 201, plan.text
    extra = await context.client.post(
        "/api/v1/admin/workers",
        headers=auth(admin),
        json={
            "phone": "+77075550901",
            "full_name": "Unscheduled colleague",
            "employee_id": "COLLEAGUE",
        },
    )
    assigned = await context.client.post(
        f"/api/v1/admin/workers/{extra.json()['id']}/assignment",
        headers=auth(admin),
        json={"brigade_id": brigade_id, "object_id": object_id},
    )
    assert assigned.status_code == 200
    async with context.database.session_factory() as session:
        extra_assignment = await session.scalar(
            select(WorkerAssignment).where(
                WorkerAssignment.worker_user_id == UUID(extra.json()["id"]),
                WorkerAssignment.effective_to.is_(None),
            )
        )
        extra_assignment.effective_from = clock - timedelta(days=1)
        await session.commit()

    async def view(**params):
        response = await context.client.get(
            "/api/v1/master/attendance-summary", headers=master, params=params
        )
        assert response.status_code == 200, response.text
        return response.json()

    data = await view(limit=1)
    assert data["total"] == 2 and len(data["items"]) == 1
    assert data["scheduled"] == 1
    assert data["counts"]["NOT_ARRIVED"] == 1
    assert data["counts"]["UNSCHEDULED"] == 1
    assert data["counts"]["ABSENT"] == 0
    assert len((await view(offset=1))["items"]) == 1

    async def scan():
        qr = (await context.client.get("/api/v1/workers/me/pass", headers=worker)).json()[
            "qr_payload"
        ]
        result = await context.client.post(
            "/api/v1/master/shift-scans", headers=master, json={"qr_payload": qr}
        )
        assert result.status_code == 200, result.text

    await scan()
    clock += timedelta(hours=8)
    active = await view()
    assert active["counts"]["WORKING"] == 1
    row = next(row for row in active["items"] if row["worker_user_id"] == worker_id)
    assert Decimal(str(row["hours"])) == 8
    assert row["worked_minutes"] == 480
    assert active["worked_minutes"] == 480
    assert row["brigade_name"] == "Operations Brigade"
    assert row["object_name"] == "Operations Object"
    assert row["check_in"].endswith("+03:00")
    await scan()
    closed = await view()
    assert closed["counts"]["FINISHED"] == 1
    assert closed["arrived"] == 1
    assert closed["counts"]["ABSENT"] == 0
    assert all(
        key not in row
        for key in ("phone", "passport_number", "daily_earnings", "rate_per_m3", "events")
    )
    assert "password" not in str(closed)


async def test_unscanned_worker_absence_after_shift_end_and_overnight_clock(
    context: AppContext, monkeypatch
):
    admin = await login(context, UserRole.ADMIN)
    worker_id, _, brigade_id, object_id = await prepare_assignment(context, admin)
    master = auth(await login(context, UserRole.MASTER))
    worker = auth(await login(context, UserRole.WORKER))
    clock = utc_now().replace(hour=20, minute=0, second=0, microsecond=0)
    for module in (master_dashboard, shifts, pass_routes):
        monkeypatch.setattr(module, "utc_now", lambda: clock)
    day = clock.astimezone(ZoneInfo("Europe/Moscow")).date()
    await context.client.post(
        "/api/v1/admin/schedules",
        headers=auth(admin),
        json={
            "worker_user_id": worker_id,
            "brigade_id": brigade_id,
            "object_id": object_id,
            "work_date": str(day),
            "shift_start": "08:00",
            "shift_end": "20:00",
        },
    )
    absent = await context.client.get("/api/v1/master/attendance-summary", headers=master)
    assert absent.json()["counts"]["ABSENT"] == 1
    qr = (await context.client.get("/api/v1/workers/me/pass", headers=worker)).json()["qr_payload"]
    assert (
        await context.client.post(
            "/api/v1/master/shift-scans", headers=master, json={"qr_payload": qr}
        )
    ).status_code == 200
    clock += timedelta(hours=8)
    after_midnight = await context.client.get("/api/v1/master/attendance-summary", headers=master)
    row = after_midnight.json()["items"][0]
    assert row["state"] == "WORKING" and row["work_date"] == str(day)
    assert Decimal(str(row["hours"])) == 8


async def test_foreign_brigade_and_transfer_hide_old_attendance(context: AppContext):
    admin = await login(context, UserRole.ADMIN)
    worker_id, _, _, object_id = await prepare_assignment(context, admin)
    master = auth(await login(context, UserRole.MASTER))
    worker = auth(await login(context, UserRole.WORKER))
    qr = (await context.client.get("/api/v1/workers/me/pass", headers=worker)).json()["qr_payload"]
    assert (
        await context.client.post(
            "/api/v1/master/shift-scans", headers=master, json={"qr_payload": qr}
        )
    ).status_code == 200
    async with context.database.session_factory() as session:
        profile = await session.scalar(
            select(EmployeeProfile).where(EmployeeProfile.user_id == UUID(worker_id))
        )
        city_id = profile.city_id
        now = utc_now()
        other = User(
            phone="+77055550999",
            role=UserRole.MASTER,
            status=AccountStatus.ACTIVE,
            password_hash=hash_password("Test-password-123"),
            created_at=now,
            updated_at=now,
        )
        session.add(other)
        await session.flush()
        other_id = other.id
        session.add(
            EmployeeProfile(
                user_id=other.id,
                full_name="Other Master",
                employee_id="OTHER-MASTER",
                city_id=city_id,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    other_tokens = (
        await context.client.post(
            "/api/v1/auth/login",
            json={"phone": "+77055550999", "password": "Test-password-123", "device_name": "test"},
        )
    ).json()
    other_headers = auth(other_tokens)
    before = await context.client.get("/api/v1/master/attendance-summary", headers=other_headers)
    assert before.json()["total"] == 0
    brigade = await context.client.post(
        "/api/v1/admin/brigades",
        headers=auth(admin),
        json={"name": "Foreign brigade", "city_id": str(city_id), "master_user_id": str(other_id)},
    )
    transferred = await context.client.post(
        f"/api/v1/admin/workers/{worker_id}/assignment",
        headers=auth(admin),
        json={"brigade_id": brigade.json()["id"], "object_id": object_id},
    )
    assert transferred.status_code == 409, transferred.text
    history = (
        await context.client.get("/api/v1/workers/me/attendance-history", headers=worker)
    ).json()
    record = history["items"][0]
    closed = await context.client.patch(
        f"/api/v1/admin/attendance/{record['id']}",
        headers=auth(admin),
        json={"check_out": (utc_now() + timedelta(minutes=3)).isoformat()},
    )
    assert closed.status_code == 200
    transferred = await context.client.post(
        f"/api/v1/admin/workers/{worker_id}/assignment",
        headers=auth(admin),
        json={"brigade_id": brigade.json()["id"], "object_id": object_id},
    )
    assert transferred.status_code == 200
    old = await context.client.get(
        "/api/v1/master/attendance-summary",
        headers=master,
        params={"master_user_id": str(other_id), "worker_user_id": worker_id},
    )
    assert old.json()["total"] == 0 and old.json()["arrived"] == 0
    new = await context.client.get("/api/v1/master/attendance-summary", headers=other_headers)
    assert new.json()["total"] == 1
    assert new.json()["items"][0]["check_in"] is None
    assert new.json()["items"][0]["brigade_name"] == "Foreign brigade"


@pytest.mark.parametrize(
    "change", ["ended", "future", "archived_brigade", "blocked", "profile_owner"]
)
async def test_inactive_and_inconsistent_assignments_are_excluded(context: AppContext, change):
    admin = await login(context, UserRole.ADMIN)
    worker_id, _, brigade_id, _ = await prepare_assignment(context, admin)
    async with context.database.session_factory() as session:
        assignment = await session.scalar(
            select(WorkerAssignment).where(WorkerAssignment.worker_user_id == UUID(worker_id))
        )
        if change == "ended":
            assignment.effective_to = utc_now()
        elif change == "future":
            assignment.effective_from = utc_now() + timedelta(days=1)
        elif change == "archived_brigade":
            brigade = await session.get(Brigade, UUID(brigade_id))
            brigade.archived_at = utc_now()
        elif change == "blocked":
            user = await session.get(User, UUID(worker_id))
            user.status = AccountStatus.BLOCKED
        else:
            profile = await session.scalar(
                select(EmployeeProfile).where(EmployeeProfile.user_id == UUID(worker_id))
            )
            profile.master_user_id = None
        await session.commit()
    result = await context.client.get(
        "/api/v1/master/attendance-summary", headers=auth(await login(context, UserRole.MASTER))
    )
    assert result.status_code == 200
    assert result.json()["total"] == 0
