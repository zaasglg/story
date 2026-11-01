"""Server-clock attendance reconciliation. Run only from an explicit activation date."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.access.rating import sync_attendance_rating
from backend.audit.service import add_audit_event
from backend.core.config import Settings
from backend.core.time import ensure_utc, utc_now
from backend.identity.models import AccountStatus, User
from backend.operations.models import (
    AttendanceRecord,
    AttendanceStatus,
    ObjectCalendarDay,
    WorkSchedule,
)
from backend.workforce.models import City


def deadline(schedule: WorkSchedule, timezone: str, grace_minutes: int) -> datetime:
    end_day = schedule.work_date
    if schedule.shift_end <= schedule.shift_start:
        end_day += timedelta(days=1)
    return datetime.combine(end_day, schedule.shift_end, ZoneInfo(timezone)) + timedelta(
        minutes=grace_minutes
    )


async def reconcile_attendance(session: AsyncSession, settings: Settings) -> int:
    if not settings.attendance_auto_enabled:
        return 0
    if settings.attendance_auto_from is None:
        raise ValueError("Attendance activation date is required; historical replay is forbidden")
    # One transaction-wide lock across all processes. No durable claim can be lost on crash.
    if session.bind.dialect.name == "postgresql":
        claimed = await session.scalar(text("SELECT pg_try_advisory_xact_lock(1917, 33)"))
        if not claimed:
            return 0
    now = utc_now()
    schedules = list(
        (
            await session.scalars(
                select(WorkSchedule)
                .where(
                    WorkSchedule.work_date >= settings.attendance_auto_from,
                    WorkSchedule.work_date <= now.date() + timedelta(days=1),
                    WorkSchedule.archived_at.is_(None),
                    WorkSchedule.approved.is_(True),
                )
                .options(selectinload(WorkSchedule.work_object), selectinload(WorkSchedule.brigade))
                .order_by(WorkSchedule.worker_user_id, WorkSchedule.work_date)
            )
        ).all()
    )
    created, workers = 0, set()
    for schedule in schedules:
        user = await session.scalar(
            select(User).where(User.id == schedule.worker_user_id).with_for_update()
        )
        if user.archived_at is not None or user.status != AccountStatus.ACTIVE:
            continue
        city = await session.get(City, schedule.work_object.city_id)
        if ensure_utc(deadline(schedule, city.timezone, settings.attendance_grace_minutes)) > now:
            continue
        if schedule.work_object.archived_at is not None:
            continue
        workers.add(user.id)
        # A deleted/corrected record is deliberately not resurrected by a background task.
        existing = await session.scalar(
            select(AttendanceRecord).where(
                AttendanceRecord.worker_user_id == user.id,
                AttendanceRecord.work_date == schedule.work_date,
            )
        )
        workers.add(user.id)
        exception = await session.scalar(
            select(ObjectCalendarDay).where(
                ObjectCalendarDay.object_id == schedule.object_id,
                ObjectCalendarDay.work_date == schedule.work_date,
                ObjectCalendarDay.archived_at.is_(None),
            )
        )
        working = schedule.is_working_day and (exception is None or exception.is_working_day)
        if not working:
            if (
                existing is not None
                and existing.status == AttendanceStatus.ABSENT
                and existing.comment == "Automatic absence: approved shift ended"
            ):
                existing.status = AttendanceStatus.EXCUSED
                existing.updated_at = now
                add_audit_event(
                    session,
                    action="AUTO_ABSENCE_EXCUSED",
                    entity_type="attendance",
                    entity_id=existing.id,
                    old_value={"status": "ABSENT"},
                    new_value={"status": "EXCUSED", "reason": "Object calendar is non-working"},
                )
            continue
        if existing is not None:
            continue
        record = AttendanceRecord(
            worker_user_id=user.id,
            work_date=schedule.work_date,
            status=AttendanceStatus.ABSENT,
            comment="Automatic absence: approved shift ended",
            created_by=schedule.created_by,
            updated_by=schedule.updated_by,
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        await session.flush()
        add_audit_event(
            session,
            action="ATTENDANCE_AUTO_ABSENT",
            entity_type="attendance",
            entity_id=record.id,
            new_value={
                "worker_user_id": str(user.id),
                "date": schedule.work_date.isoformat(),
                "source": "AUTOMATIC",
                "status": "ABSENT",
            },
        )
        created += 1
    for worker in sorted(workers, key=str):
        await sync_attendance_rating(session, worker, None)
    await session.commit()
    return created
