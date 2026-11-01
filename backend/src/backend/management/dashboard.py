"""Operational reports for the administrator, built from persisted shift records."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from backend.access.attendance_views import events_for
from backend.access.routes import Session
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.time import ensure_utc, utc_now
from backend.identity.dependencies import CurrentUser
from backend.identity.models import AccountStatus, EmployeeProfile, User, UserRole
from backend.operations.models import (
    AttendanceRecord,
    AttendanceStatus,
    WorkDay,
    WorkDayStatus,
    WorkSchedule,
)
from backend.workforce.models import ObjectStatus, WorkObject

router = APIRouter(tags=["management"])


@router.get("/admin/dashboard")
async def admin_dashboard(
    session: Session,
    _: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_MANAGE))],
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> dict:
    return await build_operational_dashboard(session, days)


@router.get("/boss/dashboard")
async def boss_dashboard(
    session: Session,
    _: Annotated[CurrentUser, Depends(require_permission(Permission.PROFIT_READ))],
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> dict:
    return await build_operational_dashboard(session, days)


async def build_operational_dashboard(session: Session, days: int) -> dict:
    now = utc_now()
    end = now.astimezone(ZoneInfo("Europe/Moscow")).date()
    start = end - timedelta(days=days - 1)
    daily = {
        start + timedelta(days=i): {
            "date": start + timedelta(days=i),
            "scheduled": 0,
            "arrived": 0,
            "working": 0,
            "finished": 0,
            "absent": 0,
            "waiting": 0,
            "excused": 0,
            "worked_minutes": 0,
            "approved_volume_m3": Decimal(0),
        }
        for i in range(days)
    }
    attendance = list(
        (
            await session.scalars(
                select(AttendanceRecord)
                .where(
                    AttendanceRecord.work_date.between(start, end),
                    AttendanceRecord.archived_at.is_(None),
                )
                .order_by(AttendanceRecord.work_date)
            )
        ).all()
    )
    schedules = list(
        (
            await session.scalars(
                select(WorkSchedule)
                .where(
                    WorkSchedule.work_date.between(start, end),
                    WorkSchedule.archived_at.is_(None),
                )
                .options(selectinload(WorkSchedule.work_object).selectinload(WorkObject.city))
            )
        ).all()
    )
    events = await events_for(session, [row.id for row in attendance])
    plans = {(row.worker_user_id, row.work_date): row for row in schedules}
    records = {(row.worker_user_id, row.work_date): row for row in attendance}
    workers_by_day = {day: set() for day in daily}
    objects = {}
    corrections = 0

    def object_row(object_id, name):
        key = str(object_id) if object_id else "unassigned"
        if key not in objects:
            objects[key] = {
                "id": key,
                "name": name or "Объект не указан",
                "arrived": 0,
                "worked_minutes": 0,
                "approved_volume_m3": Decimal(0),
            }
        return objects[key]

    for row in attendance:
        day = daily[row.work_date]
        workers_by_day[row.work_date].add(row.worker_user_id)
        plan = plans.get((row.worker_user_id, row.work_date))
        context = (events.get(row.id) or [None])[0]
        obj = object_row(
            context.object_id if context else plan.object_id if plan else None,
            context.object_name if context else plan.work_object.name if plan else None,
        )
        if row.check_in:
            day["arrived"] += 1
            obj["arrived"] += 1
            seconds = max(
                0, (ensure_utc(row.check_out or now) - ensure_utc(row.check_in)).total_seconds()
            )
            if row.check_out:
                day["finished"] += 1
            else:
                day["working"] += 1
                if seconds > 86400:
                    corrections += 1
                    seconds = 0
            minutes = int(seconds // 60)
            day["worked_minutes"] += minutes
            obj["worked_minutes"] += minutes
        elif row.status == AttendanceStatus.EXCUSED:
            day["excused"] += 1
        elif row.status == AttendanceStatus.ABSENT:
            day["absent"] += 1
    for plan in schedules:
        day = daily[plan.work_date]
        workers_by_day[plan.work_date].add(plan.worker_user_id)
        day["scheduled"] += 1
        row = records.get((plan.worker_user_id, plan.work_date))
        if row and (
            row.check_in or row.status in (AttendanceStatus.ABSENT, AttendanceStatus.EXCUSED)
        ):
            continue
        zone = ZoneInfo(plan.work_object.city.timezone)
        shift_end = datetime.combine(plan.work_date, plan.shift_end, zone)
        if plan.shift_end <= plan.shift_start:
            shift_end += timedelta(days=1)
        day["absent" if now >= shift_end else "waiting"] += 1

    approved = (
        await session.execute(
            select(
                WorkDay.work_date,
                WorkDay.object_id,
                func.sum(WorkDay.m3),
            )
            .where(
                WorkDay.work_date.between(start, end),
                WorkDay.archived_at.is_(None),
                WorkDay.status == WorkDayStatus.APPROVED,
            )
            .group_by(WorkDay.work_date, WorkDay.object_id)
        )
    ).all()
    names = dict((await session.execute(select(WorkObject.id, WorkObject.name))).all())
    for obj in objects.values():
        if obj["id"] != "unassigned":
            obj["name"] = names.get(UUID(obj["id"]), obj["name"])
    for day, object_id, volume in approved:
        daily[day]["approved_volume_m3"] += volume
        object_row(object_id, names.get(object_id))["approved_volume_m3"] += volume
    for date, day in daily.items():
        day["total"] = len(workers_by_day[date])
        day["unmarked"] = day["total"] - sum(
            day[k] for k in ("working", "finished", "absent", "waiting", "excused")
        )

    active_workers = await session.scalar(
        select(func.count())
        .select_from(User)
        .join(
            EmployeeProfile,
            EmployeeProfile.user_id == User.id,
        )
        .where(
            User.role == UserRole.WORKER,
            User.status == AccountStatus.ACTIVE,
            User.archived_at.is_(None),
            EmployeeProfile.archived_at.is_(None),
        )
    )
    active_objects = await session.scalar(
        select(func.count())
        .select_from(WorkObject)
        .where(
            WorkObject.archived_at.is_(None),
            WorkObject.status == ObjectStatus.ACTIVE,
        )
    )
    return {
        "date_from": start,
        "date_to": end,
        "generated_at": now,
        "report_timezone": "Europe/Moscow",
        "active_workers": active_workers,
        "active_objects": active_objects,
        "needs_correction": corrections,
        "totals": {
            key: sum(day[key] for day in daily.values())
            for key in (
                "scheduled",
                "arrived",
                "finished",
                "absent",
                "worked_minutes",
                "approved_volume_m3",
            )
        },
        "today": daily[end],
        "daily": list(daily.values()),
        "objects": sorted(objects.values(), key=lambda row: row["worked_minutes"], reverse=True),
    }
