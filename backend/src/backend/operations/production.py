from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.time import ensure_utc, utc_now
from backend.identity.dependencies import CurrentUser
from backend.operations.models import ProductionEntry
from backend.operations.routes import Session
from backend.workforce.models import City, WorkerAssignment, WorkObject
from backend.workforce.service import active_assignment, require_worker

router = APIRouter(tags=["production"])
OwnWorker = Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_READ_OWN))]


class EntryCreate(BaseModel):
    volume_m3: Decimal = Field(gt=0, le=10000, max_digits=12, decimal_places=2)
    note: str | None = Field(default=None, max_length=240)


class EntryRead(BaseModel):
    id: str
    volume_m3: Decimal
    note: str | None
    recorded_at: datetime


async def worker_timezone(session: Session, actor: CurrentUser) -> ZoneInfo:
    worker = await require_worker(session, actor.id)
    assignment = await active_assignment(session, actor.id)
    city_id = (
        assignment.work_object.city_id
        if assignment and assignment.work_object
        else worker.profile.city_id
    )
    city = await session.get(City, city_id) if city_id else None
    return ZoneInfo(city.timezone if city else "Asia/Almaty")


async def entry_timezone(session, row, fallback):
    if row.timezone:
        return ZoneInfo(row.timezone)
    # Legacy entries retain their original assignment context when it is known.
    name = await session.scalar(
        select(City.timezone)
        .join(WorkObject, WorkObject.city_id == City.id)
        .join(WorkerAssignment, WorkerAssignment.object_id == WorkObject.id)
        .where(
            WorkerAssignment.worker_user_id == row.worker_user_id,
            WorkerAssignment.effective_from <= ensure_utc(row.recorded_at),
            or_(
                WorkerAssignment.effective_to.is_(None),
                WorkerAssignment.effective_to > ensure_utc(row.recorded_at),
            ),
        )
        .order_by(WorkerAssignment.effective_from.desc())
        .limit(1)
    )
    return ZoneInfo(name) if name else fallback


async def daily_values(session, actor, start_day, end_day, timezone):
    # Query UTC bounds, then bucket by each entry's immutable local calendar.
    start = datetime.combine(start_day, datetime.min.time(), tzinfo=UTC) - timedelta(hours=14)
    end = datetime.combine(
        end_day + timedelta(days=1), datetime.min.time(), tzinfo=UTC
    ) + timedelta(hours=14)
    rows = (
        await session.scalars(
            select(ProductionEntry).where(
                ProductionEntry.worker_user_id == actor.id,
                ProductionEntry.recorded_at >= start,
                ProductionEntry.recorded_at < end,
                ProductionEntry.recorded_at <= utc_now(),
            )
        )
    ).all()
    values = {}
    for row in rows:
        zone = await entry_timezone(session, row, timezone)
        day = ensure_utc(row.recorded_at).astimezone(zone).date()
        if start_day <= day <= end_day:
            values[day] = values.get(day, Decimal("0")) + row.volume_m3
    return values


@router.post("/workers/me/production", response_model=EntryRead, status_code=201)
async def create_entry(payload: EntryCreate, session: Session, actor: OwnWorker) -> EntryRead:
    await require_worker(session, actor.id)
    assignment = await active_assignment(session, actor.id)
    zone = await worker_timezone(session, actor)
    item = ProductionEntry(
        object_id=assignment.object_id if assignment else None,
        timezone=zone.key,
        worker_user_id=actor.id,
        volume_m3=payload.volume_m3,
        note=payload.note.strip() if payload.note and payload.note.strip() else None,
        recorded_at=utc_now(),
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return EntryRead(
        id=str(item.id), volume_m3=item.volume_m3, note=item.note, recorded_at=item.recorded_at
    )


@router.get("/workers/me/production")
async def production_entries(
    session: Session,
    actor: OwnWorker,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    timezone = await worker_timezone(session, actor)
    now = utc_now().astimezone(timezone)
    rows = list(
        (
            await session.scalars(
                select(ProductionEntry)
                .where(ProductionEntry.worker_user_id == actor.id)
                .order_by(ProductionEntry.recorded_at.desc())
                .limit(limit)
            )
        ).all()
    )
    values = await daily_values(session, actor, now.date(), now.date(), timezone)
    total = values.get(now.date(), Decimal("0"))
    return {
        "date": now.date(),
        "today_total": total,
        "entries": [
            EntryRead(
                id=str(row.id), volume_m3=row.volume_m3, note=row.note, recorded_at=row.recorded_at
            )
            for row in rows
        ],
    }


@router.get("/workers/me/production/stats")
async def production_stats(
    session: Session,
    actor: OwnWorker,
    period: Literal["week", "month"] = "week",
) -> dict:
    timezone = await worker_timezone(session, actor)
    today = utc_now().astimezone(timezone).date()
    start_date = (
        today - timedelta(days=today.weekday()) if period == "week" else today.replace(day=1)
    )
    days = 7 if period == "week" else (today - start_date).days + 1
    values = await daily_values(session, actor, start_date, today, timezone)
    series = [
        {
            "date": start_date + timedelta(days=i),
            "volume_m3": values.get(start_date + timedelta(days=i), Decimal("0")),
        }
        for i in range(days)
    ]
    total = sum((item["volume_m3"] for item in series), Decimal("0"))
    active = sum(1 for item in series if item["volume_m3"] > 0)
    return {
        "period": period,
        "date_from": start_date,
        "date_to": today,
        "total_m3": total,
        "average_m3": total / active if active else Decimal("0"),
        "active_days": active,
        "series": series,
    }
