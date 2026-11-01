from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import select

from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.time import ensure_utc, utc_now
from backend.identity.dependencies import CurrentUser
from backend.operations.models import AttendanceRecord, WorkDay, WorkSchedule
from backend.operations.routes import Session
from backend.workforce.models import City
from backend.workforce.service import require_worker

router = APIRouter(tags=["operations"])


@router.get("/workers/me/dashboard")
async def own_dashboard(
    session: Session,
    actor: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_READ_OWN))],
) -> dict:
    worker = await require_worker(session, actor.id)
    city = await session.get(City, worker.profile.city_id) if worker.profile.city_id else None
    now = utc_now().astimezone(ZoneInfo(city.timezone if city else "Europe/Moscow"))
    today = now.date()
    schedule = await session.scalar(
        select(WorkSchedule).where(
            WorkSchedule.worker_user_id == actor.id,
            WorkSchedule.work_date == today,
            WorkSchedule.archived_at.is_(None),
        )
    )
    attendance = await session.scalar(
        select(AttendanceRecord).where(
            AttendanceRecord.worker_user_id == actor.id,
            AttendanceRecord.work_date == today,
            AttendanceRecord.archived_at.is_(None),
        )
    )
    if attendance is None:
        attendance = await session.scalar(
            select(AttendanceRecord)
            .where(
                AttendanceRecord.worker_user_id == actor.id,
                AttendanceRecord.work_date == today - timedelta(days=1),
                AttendanceRecord.check_in.is_not(None),
                AttendanceRecord.archived_at.is_(None),
            )
            .limit(1)
        )
        if (
            attendance
            and attendance.check_out
            and (ensure_utc(attendance.check_out).astimezone(now.tzinfo).date() != today)
        ):
            attendance = None
    days = list(
        (
            await session.scalars(
                select(WorkDay)
                .where(
                    WorkDay.worker_user_id == actor.id,
                    WorkDay.work_date == today,
                    WorkDay.archived_at.is_(None),
                    WorkDay.status != "REJECTED",
                )
                .order_by(WorkDay.created_at)
            )
        ).all()
    )
    latest = max(
        (day for day in days if day.check_in),
        key=lambda day: ensure_utc(day.check_in),
        default=None,
    )
    check_in = attendance.check_in if attendance else latest.check_in if latest else None
    check_out = attendance.check_out if attendance else latest.check_out if latest else None
    state = "NONE"
    if attendance and attendance.status == "ABSENT":
        state = "ABSENT"
    elif check_out:
        state = "FINISHED"
    elif check_in:
        state = "WORKING"
    elif days:
        state = "RECORDED"
    elif schedule:
        state = "SCHEDULED"
    planned = None
    if schedule:
        start = datetime.combine(today, schedule.shift_start)
        end = datetime.combine(today, schedule.shift_end)
        if end <= start:
            end += timedelta(days=1)
        planned = Decimal(str((end - start).total_seconds())) / Decimal(3600)
    return {
        "server_time": now.isoformat(),
        "work_date": today,
        "state": state,
        "utc_offset_minutes": int(now.utcoffset().total_seconds() / 60),
        "check_in": ensure_utc(check_in).astimezone(now.tzinfo) if check_in else None,
        "check_out": ensure_utc(check_out).astimezone(now.tzinfo) if check_out else None,
        "attendance_hours": (
            Decimal(
                str(
                    (
                        (ensure_utc(check_out) if check_out else now) - ensure_utc(check_in)
                    ).total_seconds()
                )
            )
            / Decimal(3600)
            if attendance and check_in
            else None
        ),
        "hours": sum((day.hours for day in days), Decimal("0")),
        "m3": sum((day.m3 for day in days), Decimal("0")),
        "planned_hours": planned,
        "shift_start": schedule.shift_start if schedule else None,
        "shift_end": schedule.shift_end if schedule else None,
    }
