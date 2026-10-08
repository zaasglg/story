from datetime import timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.access.models import RatingEvent
from backend.audit.service import add_audit_event
from backend.core.time import utc_now
from backend.identity.models import User
from backend.operations.models import AttendanceRecord, AttendanceStatus


async def sync_attendance_rating(session: AsyncSession, worker_id: UUID, actor_id: UUID) -> None:
    await session.scalar(select(User).where(User.id == worker_id).with_for_update())
    await session.flush()
    existing = (
        await session.scalars(select(RatingEvent).where(RatingEvent.worker_user_id == worker_id))
    ).all()
    keys = {row.source_key for row in existing}
    absent_dates = {row.event_date for row in existing if row.event_type == "ABSENCE"}
    rows = (
        await session.scalars(
            select(AttendanceRecord)
            .where(AttendanceRecord.worker_user_id == worker_id)
            .order_by(AttendanceRecord.work_date)
        )
    ).all()
    for row in rows:
        if row.status == AttendanceStatus.ABSENT:
            absent_dates.add(row.work_date)
            key = f"ABSENCE:{row.work_date}"
            if key not in keys:
                item = RatingEvent(
                    worker_user_id=worker_id,
                    event_date=row.work_date,
                    event_type="ABSENCE",
                    delta=Decimal("-0.1"),
                    reason="Recorded absence",
                    source_key=key,
                    created_by=actor_id,
                    created_at=utc_now(),
                )
                session.add(item)
                keys.add(key)
                add_audit_event(
                    session,
                    actor_user_id=actor_id,
                    action="RATING_ABSENCE_RECORDED",
                    entity_type="worker",
                    entity_id=worker_id,
                    new_value={"date": row.work_date.isoformat()},
                )
    streak = 0
    previous = None
    for row in rows:
        if (
            row.archived_at is not None
            or row.work_date > utc_now().astimezone(ZoneInfo("Europe/Moscow")).date()
            or row.work_date in absent_dates
            or row.status not in (AttendanceStatus.PRESENT, AttendanceStatus.LATE)
        ):
            streak, previous = 0, None
            continue
        streak = streak + 1 if previous and row.work_date == previous + timedelta(days=1) else 1
        previous = row.work_date
        key = f"STREAK_14:{row.work_date}"
        if streak % 14 == 0 and key not in keys:
            session.add(
                RatingEvent(
                    worker_user_id=worker_id,
                    event_date=row.work_date,
                    event_type="STREAK_14",
                    delta=Decimal("0.1"),
                    reason="14 consecutive attended days",
                    source_key=key,
                    created_by=actor_id,
                    created_at=utc_now(),
                )
            )
            keys.add(key)
            add_audit_event(
                session,
                actor_user_id=actor_id,
                action="RATING_STREAK_EARNED",
                entity_type="worker",
                entity_id=worker_id,
                new_value={"date": row.work_date.isoformat()},
            )
