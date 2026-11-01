"""Read-only attendance history and a schedule-based administrative day sheet."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from backend.access.models import ShiftScanEvent
from backend.access.routes import Session
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.time import ensure_utc, utc_now
from backend.identity.dependencies import CurrentUser
from backend.identity.models import EmployeeProfile
from backend.operations.models import AttendanceRecord, AttendanceStatus, WorkSchedule
from backend.workforce.models import City, WorkObject

router = APIRouter(tags=["attendance"])


def event_read(event: ShiftScanEvent) -> dict:
    return {
        "id": event.id,
        "action": event.action,
        "actor_user_id": event.actor_user_id,
        "actor_name": event.actor_name,
        "worker_name": event.worker_name,
        "brigade_id": event.brigade_id,
        "brigade_name": event.brigade_name,
        "object_id": event.object_id,
        "object_name": event.object_name,
        "city_id": event.city_id,
        "timezone": event.timezone,
        "recorded_at": ensure_utc(event.recorded_at).astimezone(ZoneInfo(event.timezone)),
    }


def attendance_read(
    item: AttendanceRecord,
    events: list[ShiftScanEvent],
    timezone: str = "Europe/Moscow",
    now: datetime | None = None,
) -> dict:
    zone = ZoneInfo(events[0].timezone if events else timezone)
    start = ensure_utc(item.check_in) if item.check_in else None
    end = ensure_utc(item.check_out) if item.check_out else None
    seconds = max(0, ((end or now or utc_now()) - start).total_seconds()) if start else 0
    return {
        "id": item.id,
        "worker_user_id": item.worker_user_id,
        "work_date": item.work_date,
        "check_in": start.astimezone(zone) if start else None,
        "check_out": end.astimezone(zone) if end else None,
        "timezone": zone.key,
        "hours": (Decimal(str(seconds)) / Decimal(3600)).quantize(Decimal("0.01")),
        "worked_minutes": int(seconds // 60),
        "state": "FINISHED" if end else "WORKING" if start else str(item.status),
        "events": [event_read(event) for event in events],
        "object_name": events[0].object_name if events else None,
        "brigade_name": events[0].brigade_name if events else None,
    }


async def events_for(
    session: Session, attendance_ids: list[UUID]
) -> dict[UUID, list[ShiftScanEvent]]:
    result: dict[UUID, list[ShiftScanEvent]] = {}
    if attendance_ids:
        events = (
            await session.scalars(
                select(ShiftScanEvent)
                .where(ShiftScanEvent.attendance_id.in_(attendance_ids))
                .order_by(ShiftScanEvent.recorded_at, ShiftScanEvent.id)
            )
        ).all()
        for event in events:
            result.setdefault(event.attendance_id, []).append(event)
    return result


@router.get("/workers/me/attendance-history")
async def own_history(
    session: Session,
    actor: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_READ_OWN))],
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    where = (AttendanceRecord.worker_user_id == actor.id, AttendanceRecord.archived_at.is_(None))
    total = await session.scalar(select(func.count()).select_from(AttendanceRecord).where(*where))
    rows = list(
        (
            await session.scalars(
                select(AttendanceRecord)
                .where(*where)
                .order_by(AttendanceRecord.work_date.desc(), AttendanceRecord.id)
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    events = await events_for(session, [row.id for row in rows])
    timezone = (
        await session.scalar(
            select(City.timezone)
            .join(EmployeeProfile, EmployeeProfile.city_id == City.id)
            .where(EmployeeProfile.user_id == actor.id)
        )
        or "Europe/Moscow"
    )
    return {
        "items": [attendance_read(row, events.get(row.id, []), timezone) for row in rows],
        "total": total,
    }


@router.get("/admin/attendance-summary")
async def attendance_summary(
    session: Session,
    _: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_MANAGE))],
    work_date: date | None = None,
    object_id: UUID | None = None,
    city_id: UUID | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    now = utc_now()
    day = work_date or now.astimezone(ZoneInfo("Europe/Moscow")).date()
    attendance = list(
        (
            await session.scalars(
                select(AttendanceRecord).where(
                    AttendanceRecord.work_date == day, AttendanceRecord.archived_at.is_(None)
                )
            )
        ).all()
    )
    schedules = list(
        (
            await session.scalars(
                select(WorkSchedule)
                .where(WorkSchedule.work_date == day, WorkSchedule.archived_at.is_(None))
                .options(
                    selectinload(WorkSchedule.work_object).selectinload(WorkObject.city),
                    selectinload(WorkSchedule.brigade),
                )
            )
        ).all()
    )
    events = await events_for(session, [row.id for row in attendance])
    by_worker = {row.worker_user_id: row for row in attendance}
    plans = {row.worker_user_id: row for row in schedules}
    worker_ids = set(by_worker) | set(plans)
    names = dict(
        (
            await session.execute(
                select(EmployeeProfile.user_id, EmployeeProfile.full_name).where(
                    EmployeeProfile.user_id.in_(worker_ids)
                )
            )
        ).all()
    )
    items = []
    for worker_id in worker_ids:
        item, plan = by_worker.get(worker_id), plans.get(worker_id)
        scans = events.get(item.id, []) if item else []
        context = scans[0] if scans else None
        item_object = context.object_id if context else plan.object_id if plan else None
        item_city = context.city_id if context else plan.work_object.city_id if plan else None
        if object_id is not None and object_id != item_object:
            continue
        if city_id is not None and city_id != item_city:
            continue
        zone = ZoneInfo(
            context.timezone
            if context
            else plan.work_object.city.timezone
            if plan
            else "Europe/Moscow"
        )
        data = (
            attendance_read(item, scans, zone.key, now=now)
            if item
            else {
                "id": None,
                "worker_user_id": worker_id,
                "work_date": day,
                "check_in": None,
                "check_out": None,
                "hours": Decimal(0),
                "worked_minutes": 0,
                "events": [],
            }
        )
        if item and item.check_in:
            state = "FINISHED" if item.check_out else "WORKING"
        elif item and item.status == AttendanceStatus.EXCUSED:
            state = "EXCUSED"
        elif item and item.status == AttendanceStatus.ABSENT:
            state = "ABSENT"
        elif plan:
            end = datetime.combine(day, plan.shift_end, zone)
            if plan.shift_end <= plan.shift_start:
                end += timedelta(days=1)
            state = "ABSENT" if now >= end else "NOT_ARRIVED"
        else:
            state = "UNSCHEDULED"
        items.append(
            {
                **data,
                "state": state,
                "worker_name": context.worker_name if context else names.get(worker_id, "—"),
                "object_id": item_object,
                "city_id": item_city,
                "object_name": context.object_name
                if context
                else plan.work_object.name
                if plan
                else None,
                "brigade_name": context.brigade_name
                if context
                else plan.brigade.name
                if plan
                else None,
                "timezone": zone.key,
                "scheduled": plan is not None,
            }
        )
    items.sort(key=lambda row: (row["worker_name"].casefold(), str(row["worker_user_id"])))
    counts = {
        state: sum(row["state"] == state for row in items)
        for state in ("WORKING", "FINISHED", "ABSENT", "NOT_ARRIVED", "EXCUSED", "UNSCHEDULED")
    }
    return {
        "work_date": day,
        "server_time": now,
        "total": len(items),
        "counts": counts,
        "arrived": counts["WORKING"] + counts["FINISHED"],
        "scheduled": sum(row["scheduled"] for row in items),
        "worked_minutes": sum(row["worked_minutes"] for row in items),
        "hours": sum((row["hours"] for row in items), Decimal(0)),
        "items": items[offset : offset + limit],
    }
