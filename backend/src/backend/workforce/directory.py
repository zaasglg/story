"""Public directory: projections are explicitly whitelisted, never private DTOs."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.access.routes import rating_details
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.errors import ApiError
from backend.core.time import ensure_utc
from backend.finance.models import LedgerEntry
from backend.identity.dependencies import CurrentUser, get_db_session
from backend.identity.models import EmployeeProfile, User, UserRole
from backend.operations.models import AttendanceRecord
from backend.workforce.models import Brigade, City, WorkerAssignment
from backend.workforce.service import active_assignment, public_projection

router = APIRouter(prefix="/directory/workers", tags=["public directory"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
Reader = Annotated[CurrentUser, Depends(require_permission(Permission.WORKER_PUBLIC_READ))]


def query(actor):
    statement = (
        select(User)
        .join(EmployeeProfile, EmployeeProfile.user_id == User.id)
        .where(
            User.role == UserRole.WORKER,
            User.archived_at.is_(None),
            EmployeeProfile.archived_at.is_(None),
        )
    )
    if actor.role == UserRole.MASTER:
        statement = statement.where(EmployeeProfile.master_user_id == actor.id)
    return statement.options(selectinload(User.profile))


async def card(session, worker):
    public = (await public_projection(session, worker)).model_dump()
    details = await rating_details(session, worker.id)
    assignment = await active_assignment(session, worker.id)
    attendance = await session.scalar(
        select(AttendanceRecord)
        .where(AttendanceRecord.worker_user_id == worker.id, AttendanceRecord.archived_at.is_(None))
        .order_by(AttendanceRecord.work_date.desc())
        .limit(1)
    )
    work_status = "OFF_DUTY"
    if attendance and attendance.check_in and not attendance.check_out:
        work_status = "WORKING"
    if worker.status.value != "ACTIVE":
        work_status = worker.status.value
    return {
        **public,
        "position": worker.profile.position or "Работник",
        "city_id": worker.profile.city_id,
        "brigade_id": assignment.brigade_id if assignment else None,
        "rating": details["rating"],
        "work_status": work_status,
    }


@router.get("")
async def directory(
    session: Session,
    actor: Reader,
    q: str = Query(default="", max_length=160),
    city_id: UUID | None = None,
    brigade_id: UUID | None = None,
    position: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    statement = query(actor)
    if q:
        statement = statement.where(EmployeeProfile.full_name.icontains(q, autoescape=True))
    if city_id:
        statement = statement.where(EmployeeProfile.city_id == city_id)
    if position:
        statement = statement.where(func.coalesce(EmployeeProfile.position, "Работник") == position)
    if brigade_id:
        statement = statement.where(
            User.id.in_(
                select(WorkerAssignment.worker_user_id).where(
                    WorkerAssignment.brigade_id == brigade_id,
                    WorkerAssignment.effective_to.is_(None),
                )
            )
        )
    rows = list(
        (
            await session.scalars(
                statement.order_by(EmployeeProfile.full_name, User.id)
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
    )
    return {
        "items": [await card(session, row) for row in rows[:limit]],
        "next_offset": offset + limit if len(rows) > limit else None,
    }


@router.get("/options")
async def options(session: Session, actor: Reader):
    workers = list((await session.scalars(query(actor))).all())
    city_ids = {w.profile.city_id for w in workers if w.profile.city_id}
    cities = list((await session.scalars(select(City).where(City.id.in_(city_ids)))).all())
    brigades = list(
        (
            await session.scalars(
                select(Brigade)
                .join(WorkerAssignment, WorkerAssignment.brigade_id == Brigade.id)
                .where(
                    WorkerAssignment.worker_user_id.in_([w.id for w in workers]),
                    WorkerAssignment.effective_to.is_(None),
                )
                .distinct()
            )
        ).all()
    )
    return {
        "city_id": [{"value": c.id, "label": c.name} for c in cities],
        "brigade_id": [{"value": b.id, "label": b.name} for b in brigades],
        "position": [
            {"value": p, "label": p}
            for p in sorted({w.profile.position or "Работник" for w in workers})
        ],
    }


@router.get("/{worker_id}")
async def profile(worker_id: UUID, session: Session, actor: Reader):
    worker = await session.scalar(query(actor).where(User.id == worker_id))
    if worker is None:
        raise ApiError(404, "worker_not_found", "Worker was not found")
    result = await card(session, worker)
    attendance = list(
        (
            await session.scalars(
                select(AttendanceRecord).where(
                    AttendanceRecord.worker_user_id == worker.id,
                    AttendanceRecord.archived_at.is_(None),
                    AttendanceRecord.check_in.is_not(None),
                    AttendanceRecord.check_out.is_not(None),
                )
            )
        ).all()
    )
    seconds = sum(
        max(0, (ensure_utc(a.check_out) - ensure_utc(a.check_in)).total_seconds())
        for a in attendance
    )
    result.update(
        shifts=len(attendance),
        hours=Decimal(str(seconds / 3600)).quantize(Decimal(".01")),
        rating_history=(await rating_details(session, worker.id))["history"],
    )
    # Public recognition contains no amounts, currency, comments, or private finance identifiers.
    bonuses = list(
        (
            await session.scalars(
                select(LedgerEntry.period)
                .where(LedgerEntry.worker_user_id == worker.id, LedgerEntry.type == "BONUS")
                .order_by(LedgerEntry.period.desc())
            )
        ).all()
    )
    result["bonus_history"] = [
        {"period": day, "label": "Поощрение подтверждено"} for day in bonuses
    ]
    return result
