from datetime import timedelta
from decimal import Decimal

from conftest import login
from sqlalchemy import select
from test_operations_api import auth, prepare_assignment

from backend.access import attendance_job, rating
from backend.access.attendance_job import reconcile_attendance
from backend.access.models import RatingEvent
from backend.core.time import utc_now
from backend.identity.models import UserRole
from backend.operations.models import AttendanceRecord, AttendanceStatus


async def prepare(context, days=1):
    admin = await login(context, UserRole.ADMIN)
    wid, _, bid, oid = await prepare_assignment(context, admin)
    today = utc_now().date()
    for i in range(days):
        result = await context.client.post(
            "/api/v1/admin/schedules",
            headers=auth(admin),
            json={
                "worker_user_id": wid,
                "brigade_id": bid,
                "object_id": oid,
                "work_date": (today - timedelta(days=days - i)).isoformat(),
                "shift_start": "08:00",
                "shift_end": "18:00",
                "approved": True,
                "is_working_day": True,
            },
        )
        assert result.status_code == 201, result.text
    return admin, wid, today


async def test_auto_absence_waiting_idempotence_and_correction(context, monkeypatch):
    admin, wid, today = await prepare(context)
    cfg = context.settings.model_copy(
        update={
            "attendance_auto_enabled": True,
            "attendance_auto_from": today - timedelta(days=1),
            "attendance_grace_minutes": 30,
        }
    )
    from datetime import UTC, datetime

    before = datetime.combine(today - timedelta(days=1), datetime.min.time(), UTC).replace(hour=15)
    monkeypatch.setattr(attendance_job, "utc_now", lambda: before)
    async with context.database.session_factory() as session:
        assert await reconcile_attendance(session, cfg) == 0
    monkeypatch.setattr(attendance_job, "utc_now", lambda: before + timedelta(hours=1))
    async with context.database.session_factory() as session:
        assert await reconcile_attendance(session, cfg) == 1
        assert await reconcile_attendance(session, cfg) == 0
        row = await session.scalar(select(AttendanceRecord))
        row_id = str(row.id)
        events = list((await session.scalars(select(RatingEvent))).all())
        assert len(events) == 1
        assert events[0].rating_before == Decimal("5")
        assert events[0].rating_after == Decimal("4.9")
        assert events[0].source == "AUTOMATIC" and events[0].created_by is None
    updated = await context.client.patch(
        "/api/v1/admin/attendance/" + row_id, headers=auth(admin), json={"status": "EXCUSED"}
    )
    assert updated.status_code == 200, updated.text
    async with context.database.session_factory() as session:
        assert await reconcile_attendance(session, cfg) == 0
        assert await rating.current_rating(session, events[0].worker_user_id) == Decimal("5")
        assert len(list((await session.scalars(select(RatingEvent))).all())) == 2


async def test_excused_and_nonworking_schedule_never_create_absence(context):
    admin, wid, today = await prepare(context, 2)
    response = await context.client.post(
        "/api/v1/admin/attendance",
        headers=auth(admin),
        json={
            "worker_user_id": wid,
            "work_date": (today - timedelta(days=2)).isoformat(),
            "status": "EXCUSED",
        },
    )
    assert response.status_code == 201, response.text
    schedules = (await context.client.get("/api/v1/admin/schedules", headers=auth(admin))).json()
    response = await context.client.patch(
        "/api/v1/admin/schedules/" + schedules[-1]["id"],
        headers=auth(admin),
        json={"is_working_day": False},
    )
    assert response.status_code == 200, response.text
    cfg = context.settings.model_copy(
        update={"attendance_auto_enabled": True, "attendance_auto_from": today - timedelta(days=2)}
    )
    async with context.database.session_factory() as session:
        assert await reconcile_attendance(session, cfg) == 0
        rows = list((await session.scalars(select(AttendanceRecord))).all())
        assert len(rows) == 1 and rows[0].status == AttendanceStatus.EXCUSED


async def test_fourteen_calendar_days_including_nonworking_and_repeat(context):
    admin, wid, today = await prepare(context, 14)
    schedules = (await context.client.get("/api/v1/admin/schedules", headers=auth(admin))).json()
    for schedule in schedules:
        response = await context.client.patch(
            "/api/v1/admin/schedules/" + schedule["id"],
            headers=auth(admin),
            json={"is_working_day": False},
        )
        assert response.status_code == 200, response.text
    # Worker had a previous safety violation so the reward is observable below the upper cap.
    response = await context.client.post(
        "/api/v1/admin/rating-events",
        headers=auth(admin),
        json={
            "worker_user_id": wid,
            "event_date": (today - timedelta(days=15)).isoformat(),
            "event_type": "NO_GLASSES",
            "reason": "Synthetic violation",
        },
    )
    assert response.status_code == 201, response.text
    from uuid import UUID

    async with context.database.session_factory() as session:
        for _ in range(2):
            await rating.sync_attendance_rating(session, UUID(wid), None)
            await session.commit()
        events = list((await session.scalars(select(RatingEvent))).all())
        assert len(events) == 2
        reward = next(e for e in events if e.event_type == "STREAK_14")
        assert reward.rating_before == Decimal("4.8") and reward.rating_after == Decimal("4.9")


async def test_object_timezone_wins_over_profile_and_nonworking_correction_keeps_history(
    context, monkeypatch
):
    from datetime import UTC, datetime

    from backend.workforce.models import City, WorkObject

    admin, wid, today = await prepare(context)
    now = datetime.combine(today - timedelta(days=1), datetime.min.time(), UTC).replace(hour=12)
    async with context.database.session_factory() as session:
        city = City(name="Synthetic Tokyo", timezone="Asia/Tokyo", created_at=utc_now())
        session.add(city)
        await session.flush()
        obj = await session.scalar(select(WorkObject))
        obj.city_id = city.id
        await session.commit()
    monkeypatch.setattr(attendance_job, "utc_now", lambda: now)
    cfg = context.settings.model_copy(
        update={"attendance_auto_enabled": True, "attendance_auto_from": today - timedelta(days=1)}
    )
    async with context.database.session_factory() as session:
        assert await reconcile_attendance(session, cfg) == 1
    schedule = (await context.client.get("/api/v1/admin/schedules", headers=auth(admin))).json()[0]
    changed = await context.client.patch(
        "/api/v1/admin/schedules/" + schedule["id"],
        headers=auth(admin),
        json={"is_working_day": False},
    )
    # Direct fixture creates deliberately historical inconsistent assignment cities;
    # update through ORM only here, since D07 correctly rejects public city mismatch.
    if changed.status_code != 200:
        from uuid import UUID

        from backend.operations.models import WorkSchedule

        async with context.database.session_factory() as session:
            row = await session.get(WorkSchedule, UUID(schedule["id"]))
            row.is_working_day = False
            await session.commit()
    async with context.database.session_factory() as session:
        assert await reconcile_attendance(session, cfg) == 0
        records = list((await session.scalars(select(AttendanceRecord))).all())
        assert len(records) == 1 and records[0].status == AttendanceStatus.EXCUSED
        assert len(list((await session.scalars(select(RatingEvent))).all())) == 2


async def test_concurrent_jobs_never_double_charge_postgres(context, monkeypatch):
    import asyncio

    import pytest

    if context.database.engine.dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL advisory locks")
    _, wid, today = await prepare(context)
    cfg = context.settings.model_copy(
        update={"attendance_auto_enabled": True, "attendance_auto_from": today - timedelta(days=1)}
    )
    entered, resume = asyncio.Event(), asyncio.Event()
    original = attendance_job.sync_attendance_rating

    async def delayed(*args):
        entered.set()
        await resume.wait()
        return await original(*args)

    monkeypatch.setattr(attendance_job, "sync_attendance_rating", delayed)

    async def run():
        async with context.database.session_factory() as session:
            return await reconcile_attendance(session, cfg)

    first = asyncio.create_task(run())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert await asyncio.wait_for(run(), 5) == 0
        resume.set()
        assert await asyncio.wait_for(first, 5) == 1
        async with context.database.session_factory() as session:
            assert len(list((await session.scalars(select(AttendanceRecord))).all())) == 1
            assert len(list((await session.scalars(select(RatingEvent))).all())) == 1
    finally:
        resume.set()
        await asyncio.gather(first, return_exceptions=True)


async def test_object_closed_day_applies_to_later_schedules_and_denies_other_roles(
    context, monkeypatch
):
    from uuid import UUID

    from backend.operations.models import WorkSchedule

    admin, wid, today = await prepare(context)
    schedule = (await context.client.get("/api/v1/admin/schedules", headers=auth(admin))).json()[0]
    payload = {
        "object_id": schedule["object_id"],
        "work_date": today.isoformat(),
        "is_working_day": False,
        "reason": "Synthetic object closure",
    }
    path = "/api/v1/admin/object-calendar"
    for role in (UserRole.BOSS, UserRole.MASTER, UserRole.WORKER):
        assert (
            await context.client.post(path, headers=auth(await login(context, role)), json=payload)
        ).status_code == 403
    closed = await context.client.post(path, headers=auth(admin), json=payload)
    assert closed.status_code == 201, closed.text
    catalog = await context.client.get("/api/v1/admin/catalog/object-calendar", headers=auth(admin))
    assert catalog.status_code == 200, catalog.text
    assert catalog.json()["items"][0]["id"] == closed.json()["id"]
    repeat = await context.client.post(path, headers=auth(admin), json=payload)
    assert repeat.json()["id"] == closed.json()["id"]
    response = await context.client.post(
        "/api/v1/admin/schedules",
        headers=auth(admin),
        json={
            "worker_user_id": wid,
            "brigade_id": schedule["brigade_id"],
            "object_id": schedule["object_id"],
            "work_date": today.isoformat(),
            "shift_start": "08:00",
            "shift_end": "18:00",
            "approved": True,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["is_working_day"] is False
    async with context.database.session_factory() as session:
        raw = await session.get(WorkSchedule, UUID(response.json()["id"]))
        assert raw.is_working_day is True  # Effective calendar override, not a rewritten schedule.
    now = utc_now() + timedelta(days=2)
    monkeypatch.setattr(attendance_job, "utc_now", lambda: now)
    cfg = context.settings.model_copy(
        update={"attendance_auto_enabled": True, "attendance_auto_from": today}
    )
    async with context.database.session_factory() as session:
        assert await reconcile_attendance(session, cfg) == 0
        assert list((await session.scalars(select(AttendanceRecord))).all()) == []
