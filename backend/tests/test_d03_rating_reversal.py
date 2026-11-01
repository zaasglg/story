from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from conftest import login
from sqlalchemy import select

from backend.access.rating import sync_attendance_rating
from backend.core.time import utc_now
from backend.identity.models import UserRole
from backend.operations.models import AttendanceRecord


async def test_cancel_archive_and_repeat_absence_preserve_history(context):
    admin = await login(context, UserRole.ADMIN)
    worker = await login(context, UserRole.WORKER)
    ah = {"Authorization": f"Bearer {admin['access_token']}"}
    wh = {"Authorization": f"Bearer {worker['access_token']}"}
    response = await context.client.post(
        "/api/v1/admin/attendance",
        headers=ah,
        json={
            "worker_user_id": worker["user"]["id"],
            "work_date": (utc_now().date() - timedelta(days=2)).isoformat(),
            "status": "ABSENT",
        },
    )
    rid = response.json()["id"]

    async def rating():
        return (await context.client.get("/api/v1/workers/me/rating", headers=wh)).json()

    assert Decimal((await rating())["rating"]) == Decimal("4.9")
    await context.client.patch(
        f"/api/v1/admin/attendance/{rid}", headers=ah, json={"status": "PRESENT"}
    )
    assert Decimal((await rating())["rating"]) == Decimal("5")
    await context.client.patch(
        f"/api/v1/admin/attendance/{rid}", headers=ah, json={"status": "ABSENT"}
    )
    assert Decimal((await rating())["rating"]) == Decimal("4.9")
    await context.client.delete(f"/api/v1/admin/attendance/{rid}", headers=ah)
    result = await rating()
    assert Decimal(result["rating"]) == Decimal("5")
    assert len(result["history"]) == 4
    async with context.database.session_factory() as session:
        row = await session.get(AttendanceRecord, UUID(rid))
        assert row.archived_at is not None
        for _ in range(2):
            await sync_attendance_rating(
                session, UUID(worker["user"]["id"]), UUID(admin["user"]["id"])
            )
        await session.commit()
    assert len((await rating())["history"]) == 4


async def test_legacy_cancelled_absence_is_reversed_by_data_migration(context):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from backend.access.models import RatingEvent

    worker = await login(context, UserRole.WORKER)
    wid = UUID(worker["user"]["id"])
    day = utc_now().date() - timedelta(days=4)
    async with context.database.session_factory() as session:
        old = RatingEvent(
            worker_user_id=wid,
            event_date=day,
            event_type="ABSENCE",
            delta=Decimal("-.1"),
            reason="Synthetic legacy",
            source_key=f"ABSENCE:{day}",
            created_by=wid,
            created_at=utc_now(),
        )
        session.add(old)
        session.add(
            AttendanceRecord(
                worker_user_id=wid,
                work_date=day,
                status="PRESENT",
                created_by=wid,
                updated_by=wid,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        await session.commit()
    spec = importlib.util.spec_from_file_location(
        "rating_migration",
        Path(__file__).parents[1] / "alembic/versions/20261101_0032_rating_reconciliation.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def apply(connection):
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()

    for _ in range(2):
        async with context.database.engine.begin() as connection:
            await connection.run_sync(apply)
    response = await context.client.get(
        "/api/v1/workers/me/rating", headers={"Authorization": f"Bearer {worker['access_token']}"}
    )
    assert Decimal(response.json()["rating"]) == 5
    assert len(response.json()["history"]) == 2


async def test_archived_streak_reward_is_reversed_without_clamping_artifact(context):
    from backend.access.models import RatingEvent

    admin = await login(context, UserRole.ADMIN)
    worker = await login(context, UserRole.WORKER)
    wid, aid = UUID(worker["user"]["id"]), UUID(admin["user"]["id"])
    async with context.database.session_factory() as session:
        session.add(
            RatingEvent(
                worker_user_id=wid,
                event_date=utc_now().date() - timedelta(days=20),
                event_type="NO_GLASSES",
                delta=Decimal("-.2"),
                reason="Synthetic safety",
                source_key="synthetic-safety",
                created_by=aid,
                created_at=utc_now(),
            )
        )
        for days in range(14, 0, -1):
            session.add(
                AttendanceRecord(
                    worker_user_id=wid,
                    work_date=utc_now().date() - timedelta(days=days),
                    status="PRESENT",
                    created_by=aid,
                    updated_by=aid,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            )
        await sync_attendance_rating(session, wid, aid)
        await session.commit()
    headers = {"Authorization": f"Bearer {worker['access_token']}"}
    assert Decimal(
        (await context.client.get("/api/v1/workers/me/rating", headers=headers)).json()["rating"]
    ) == Decimal("4.9")
    async with context.database.session_factory() as session:
        record = await session.scalar(
            select(AttendanceRecord)
            .where(AttendanceRecord.worker_user_id == wid)
            .order_by(AttendanceRecord.work_date)
            .limit(1)
        )
        record.archived_at = utc_now()
        await sync_attendance_rating(session, wid, aid)
        await session.commit()
    result = (await context.client.get("/api/v1/workers/me/rating", headers=headers)).json()
    assert Decimal(result["rating"]) == Decimal("4.8")
    assert len(result["history"]) == 3
