"""Today's attendance for the master's current, actively assigned brigade members."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from backend.access.attendance_views import attendance_read, events_for
from backend.access.routes import Session
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.time import ensure_utc, utc_now
from backend.identity.dependencies import CurrentUser
from backend.identity.models import AccountStatus, EmployeeProfile, User, UserRole
from backend.operations.models import AttendanceRecord, AttendanceStatus, WorkSchedule
from backend.workforce.models import Brigade, WorkerAssignment, WorkObject

router = APIRouter(tags=["attendance"])


@router.get("/master/attendance-summary")
async def master_summary(
    session: Session,
    actor: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_READ_TEAM))],
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    now = utc_now()
    assignments = list(
        (
            await session.scalars(
                select(WorkerAssignment)
                .join(Brigade, Brigade.id == WorkerAssignment.brigade_id)
                .join(User, User.id == WorkerAssignment.worker_user_id)
                .join(EmployeeProfile, EmployeeProfile.user_id == User.id)
                .where(
                    WorkerAssignment.master_user_id == actor.id,
                    WorkerAssignment.effective_to.is_(None),
                    WorkerAssignment.effective_from <= now,
                    WorkerAssignment.archived_at.is_(None),
                    Brigade.master_user_id == actor.id,
                    Brigade.archived_at.is_(None),
                    User.role == UserRole.WORKER,
                    User.status == AccountStatus.ACTIVE,
                    User.archived_at.is_(None),
                    EmployeeProfile.archived_at.is_(None),
                    EmployeeProfile.master_user_id == actor.id,
                )
                .options(
                    selectinload(WorkerAssignment.brigade).selectinload(Brigade.city),
                    selectinload(WorkerAssignment.work_object).selectinload(WorkObject.city),
                )
            )
        ).all()
    )
    worker_ids = [row.worker_user_id for row in assignments]
    zones = {
        row.worker_user_id: ZoneInfo(
            row.work_object.city.timezone if row.work_object else row.brigade.city.timezone
        )
        for row in assignments
    }
    dates = {
        now.astimezone(zone).date() - timedelta(days=offset)
        for zone in zones.values()
        for offset in (0, 1)
    }
    # All data queries are constrained to the authorized current team in SQL.
    attendance = (
        list(
            (
                await session.scalars(
                    select(AttendanceRecord).where(
                        AttendanceRecord.worker_user_id.in_(worker_ids),
                        AttendanceRecord.archived_at.is_(None),
                        or_(
                            AttendanceRecord.work_date.in_(dates),
                            (
                                AttendanceRecord.check_in.is_not(None)
                                & AttendanceRecord.check_out.is_(None)
                            ),
                        ),
                    )
                )
            ).all()
        )
        if worker_ids
        else []
    )
    schedules = (
        list(
            (
                await session.scalars(
                    select(WorkSchedule)
                    .join(Brigade, Brigade.id == WorkSchedule.brigade_id)
                    .where(
                        WorkSchedule.worker_user_id.in_(worker_ids),
                        WorkSchedule.work_date.in_(dates),
                        WorkSchedule.archived_at.is_(None),
                        Brigade.master_user_id == actor.id,
                        Brigade.archived_at.is_(None),
                    )
                    .options(selectinload(WorkSchedule.work_object).selectinload(WorkObject.city))
                )
            ).all()
        )
        if worker_ids
        else []
    )
    profiles = (
        {
            profile.user_id: profile
            for profile in (
                await session.scalars(
                    select(EmployeeProfile).where(EmployeeProfile.user_id.in_(worker_ids))
                )
            ).all()
        }
        if worker_ids
        else {}
    )
    events = await events_for(session, [row.id for row in attendance])
    plans = {(row.worker_user_id, row.work_date): row for row in schedules}
    attendance_by_worker = {}
    for row in attendance:
        attendance_by_worker.setdefault(row.worker_user_id, []).append(row)
    items = []
    for assignment in assignments:
        worker_id = assignment.worker_user_id
        zone = zones[worker_id]
        today = now.astimezone(zone).date()
        visible = [
            row
            for row in attendance_by_worker.get(worker_id, [])
            if row.worker_user_id == worker_id
            and (not events.get(row.id) or events[row.id][0].brigade_id == assignment.brigade_id)
        ]
        opened = [row for row in visible if row.check_in and not row.check_out]
        item = max(opened, key=lambda row: ensure_utc(row.check_in), default=None)
        if item is None:
            item = next((row for row in visible if row.work_date == today), None)
        if item is None:
            item = next(
                (
                    row
                    for row in visible
                    if row.check_out and ensure_utc(row.check_out).astimezone(zone).date() == today
                ),
                None,
            )
        day = item.work_date if item else today
        plan = plans.get((worker_id, day))
        if plan and plan.brigade_id != assignment.brigade_id:
            plan = None
        scans = events.get(item.id, []) if item else []
        context = scans[0] if scans else None
        if context:
            zone = ZoneInfo(context.timezone)
        elif plan:
            zone = ZoneInfo(plan.work_object.city.timezone)
        data = attendance_read(item, scans, zone.key, now=now) if item else {}
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
        obj = plan.work_object if plan else assignment.work_object
        # Explicit projection excludes financial/private profile data and unrelated scan history.
        items.append(
            {
                "worker_user_id": worker_id,
                "worker_name": profiles[worker_id].full_name,
                "photo_url": profiles[worker_id].photo_url,
                "work_date": day,
                "state": state,
                "scheduled": plan is not None,
                "shift_start": plan.shift_start if plan else None,
                "shift_end": plan.shift_end if plan else None,
                "check_in": data.get("check_in"),
                "check_out": data.get("check_out"),
                "hours": data.get("hours", Decimal(0)),
                "worked_minutes": data.get("worked_minutes", 0),
                "brigade_id": assignment.brigade_id,
                "brigade_name": assignment.brigade.name,
                "object_id": context.object_id if context else obj.id if obj else None,
                "object_name": context.object_name if context else obj.name if obj else None,
                "timezone": zone.key,
                "needs_correction": len(opened) > 1
                or bool(
                    item
                    and item.check_in
                    and not item.check_out
                    and (now - ensure_utc(item.check_in)).total_seconds() > 86400
                ),
            }
        )
    items.sort(key=lambda row: (row["worker_name"].casefold(), str(row["worker_user_id"])))
    counts = {
        state: sum(row["state"] == state for row in items)
        for state in ("WORKING", "FINISHED", "ABSENT", "NOT_ARRIVED", "EXCUSED", "UNSCHEDULED")
    }
    return {
        "server_time": now,
        "total": len(items),
        "counts": counts,
        "scheduled": sum(row["scheduled"] for row in items),
        "arrived": counts["WORKING"] + counts["FINISHED"],
        "worked_minutes": sum(row["worked_minutes"] for row in items),
        "hours": sum((row["hours"] for row in items), Decimal(0)),
        "items": items[offset : offset + limit],
    }
