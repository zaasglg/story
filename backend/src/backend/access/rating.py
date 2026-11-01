"""Idempotent attendance rating with append-only reversals."""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.access.models import RatingEvent
from backend.audit.service import add_audit_event
from backend.core.time import ensure_utc, utc_now
from backend.identity.models import EmployeeProfile, User
from backend.operations.models import (
    AttendanceRecord,
    AttendanceStatus,
    ObjectCalendarDay,
    WorkSchedule,
)
from backend.workforce.models import City
from backend.workforce.service import active_assignment


def reversed_event_ids(events):
    return {
        event.source_key.removeprefix("REVERSE:")
        for event in events
        if event.event_type == "ATTENDANCE_REVERSED" and event.source_key.startswith("REVERSE:")
    }


def desired_attendance_events(rows):
    desired = {}
    streak, previous = 0, None
    for row in rows:
        if row.status == AttendanceStatus.ABSENT:
            desired[("ABSENCE", row.work_date)] = (Decimal("-0.1"), "Recorded absence")
        if row.status not in (AttendanceStatus.PRESENT, AttendanceStatus.LATE):
            streak, previous = 0, None
            continue
        streak = streak + 1 if previous and row.work_date == previous + timedelta(days=1) else 1
        previous = row.work_date
        if streak % 14 == 0:
            desired[("STREAK_14", row.work_date)] = (Decimal("0.1"), "14 consecutive attended days")
    return desired


async def current_rating(session: AsyncSession, worker_id: UUID) -> Decimal:
    events = list(
        (
            await session.scalars(
                select(RatingEvent)
                .where(RatingEvent.worker_user_id == worker_id)
                .order_by(RatingEvent.event_date, RatingEvent.created_at, RatingEvent.id)
            )
        ).all()
    )
    cancelled = reversed_event_ids(events)
    value = Decimal("5")
    for event in events:
        if str(event.id) not in cancelled and event.event_type != "ATTENDANCE_REVERSED":
            value = max(Decimal("0"), min(Decimal("5"), value + event.delta))
    return value


async def append_rating_event(session: AsyncSession, event: RatingEvent) -> None:
    events = list(
        (
            await session.scalars(
                select(RatingEvent)
                .where(RatingEvent.worker_user_id == event.worker_user_id)
                .order_by(RatingEvent.event_date, RatingEvent.created_at, RatingEvent.id)
            )
        ).all()
    )

    def value(rows):
        cancelled = reversed_event_ids(rows)
        result = Decimal("5")
        for row in sorted(
            rows, key=lambda row: (row.event_date, ensure_utc(row.created_at), str(row.id))
        ):
            if str(row.id) not in cancelled and row.event_type != "ATTENDANCE_REVERSED":
                result = max(Decimal("0"), min(Decimal("5"), result + row.delta))
        return result

    event.id = event.id or uuid4()
    event.source = event.source or "MANUAL"
    event.rating_before = value(events)
    event.rating_after = value(events + [event])
    session.add(event)
    # Both snapshots are part of INSERT. Existing PostgreSQL append-only triggers
    # forbid updating an event even to fill previously missing metadata.
    await session.flush()


async def sync_attendance_rating(
    session: AsyncSession, worker_id: UUID, actor_id: UUID | None
) -> None:
    await session.scalar(select(User).where(User.id == worker_id).with_for_update())
    await session.flush()
    existing = list(
        (
            await session.scalars(
                select(RatingEvent).where(RatingEvent.worker_user_id == worker_id)
            )
        ).all()
    )
    assignment = await active_assignment(session, worker_id)
    city_id = (
        assignment.work_object.city_id
        if assignment and assignment.work_object
        else await session.scalar(
            select(EmployeeProfile.city_id).where(EmployeeProfile.user_id == worker_id)
        )
    )
    city = await session.get(City, city_id) if city_id else None
    today = utc_now().astimezone(ZoneInfo(city.timezone if city else "Europe/Moscow")).date()
    rows = (
        await session.scalars(
            select(AttendanceRecord)
            .where(
                AttendanceRecord.worker_user_id == worker_id,
                AttendanceRecord.archived_at.is_(None),
                AttendanceRecord.work_date <= today,
            )
            .order_by(AttendanceRecord.work_date)
        )
    ).all()
    desired = desired_attendance_events(rows)
    schedules = list(
        (
            await session.scalars(
                select(WorkSchedule)
                .where(
                    WorkSchedule.worker_user_id == worker_id,
                    WorkSchedule.approved.is_(True),
                    WorkSchedule.archived_at.is_(None),
                    WorkSchedule.work_date <= today,
                )
                .options(selectinload(WorkSchedule.work_object))
                .order_by(WorkSchedule.work_date)
            )
        ).all()
    )
    if schedules:
        desired = {
            key: value
            for key, value in desired.items()
            if key[0] == "ABSENCE" or key[1] < schedules[0].work_date
        }
        by_day = {row.work_date: row for row in rows}
        calendar = {row.work_date: row for row in schedules}
        day, last, consecutive = schedules[0].work_date, schedules[-1].work_date, 0
        while day <= last:
            schedule, attendance = calendar.get(day), by_day.get(day)
            exception = (
                await session.scalar(
                    select(ObjectCalendarDay).where(
                        ObjectCalendarDay.object_id == schedule.object_id,
                        ObjectCalendarDay.work_date == day,
                        ObjectCalendarDay.archived_at.is_(None),
                    )
                )
                if schedule
                else None
            )
            working = (
                schedule
                and schedule.is_working_day
                and (exception is None or exception.is_working_day)
            )
            complete = schedule is not None and day < today
            if working:
                object_city = await session.get(City, schedule.work_object.city_id)
                end = datetime.combine(
                    day + timedelta(days=schedule.shift_end <= schedule.shift_start),
                    schedule.shift_end,
                    ZoneInfo(object_city.timezone if object_city else "Europe/Moscow"),
                )
                complete = end <= utc_now() and attendance is not None
            if schedule and not working:
                desired.pop(("ABSENCE", day), None)
            if (
                attendance
                and attendance.status == AttendanceStatus.ABSENT
                and (not schedule or working)
            ):
                consecutive = 0
            elif complete:
                consecutive += 1
                if consecutive % 14 == 0:
                    desired[("STREAK_14", day)] = (
                        Decimal("0.1"),
                        "14 calendar days without confirmed absence",
                    )
            else:
                consecutive = 0
            day += timedelta(days=1)
    reversed_ids = reversed_event_ids(existing)
    active = {
        (e.event_type, e.event_date): e
        for e in existing
        if e.event_type in ("ABSENCE", "STREAK_14") and str(e.id) not in reversed_ids
    }
    for key, event in active.items():
        if key not in desired:
            await append_rating_event(
                session,
                RatingEvent(
                    worker_user_id=worker_id,
                    event_date=event.event_date,
                    event_type="ATTENDANCE_REVERSED",
                    source="AUTOMATIC",
                    delta=-event.delta,
                    reason="Attendance correction: previous rating event cancelled",
                    source_key=f"REVERSE:{event.id}",
                    created_by=actor_id,
                    created_at=utc_now(),
                ),
            )
            add_audit_event(
                session,
                actor_user_id=actor_id,
                action="RATING_EVENT_REVERSED",
                entity_type="rating_event",
                entity_id=event.id,
                new_value={"reason": "attendance_corrected"},
            )
    for key, (delta, reason) in desired.items():
        if key in active:
            continue
        kind, day = key
        generation = sum(e.event_type == kind and e.event_date == day for e in existing)
        await append_rating_event(
            session,
            RatingEvent(
                worker_user_id=worker_id,
                event_date=day,
                event_type=kind,
                source="AUTOMATIC",
                delta=delta,
                reason=reason,
                source_key=f"{kind}:{day}:v{generation}",
                created_by=actor_id,
                created_at=utc_now(),
            ),
        )
        add_audit_event(
            session,
            actor_user_id=actor_id,
            action="RATING_ABSENCE_RECORDED" if kind == "ABSENCE" else "RATING_STREAK_EARNED",
            entity_type="worker",
            entity_id=worker_id,
            new_value={"date": day.isoformat()},
        )
    await session.flush()
