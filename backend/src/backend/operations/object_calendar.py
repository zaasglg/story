"""Object-wide approved calendar; ordinary users cannot change exceptions."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from backend.access.routes import Session
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.identity.dependencies import CurrentUser
from backend.operations.models import ObjectCalendarDay
from backend.workforce.models import WorkObject

router = APIRouter(prefix="/admin/object-calendar", tags=["object calendar"])
Manager = Annotated[CurrentUser, Depends(require_permission(Permission.ORGANIZATION_MANAGE))]
Reader = Annotated[CurrentUser, Depends(require_permission(Permission.ORGANIZATION_READ))]


class DayWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    object_id: UUID
    work_date: date
    is_working_day: bool = False
    reason: str = Field(min_length=3, max_length=2000)


class DayRead(DayWrite):
    model_config = ConfigDict(from_attributes=True)
    id: UUID


@router.get("", response_model=list[DayRead])
async def days(session: Session, actor: Reader):
    return list(
        (
            await session.scalars(
                select(ObjectCalendarDay)
                .where(ObjectCalendarDay.archived_at.is_(None))
                .order_by(ObjectCalendarDay.work_date.desc())
            )
        ).all()
    )


@router.post("", response_model=DayRead, status_code=201)
async def set_day(payload: DayWrite, session: Session, actor: Manager):
    obj = await session.get(WorkObject, payload.object_id, with_for_update=True)
    if obj is None or obj.archived_at is not None:
        raise ApiError(404, "object_not_found", "Object was not found")
    item = await session.scalar(
        select(ObjectCalendarDay)
        .where(
            ObjectCalendarDay.object_id == payload.object_id,
            ObjectCalendarDay.work_date == payload.work_date,
        )
        .with_for_update()
    )
    now = utc_now()
    if item is None:
        item = ObjectCalendarDay(
            **payload.model_dump(),
            created_by=actor.id,
            updated_by=actor.id,
            created_at=now,
            updated_at=now,
        )
        session.add(item)
    else:
        for key, value in payload.model_dump().items():
            setattr(item, key, value)
        item.updated_by = actor.id
        item.updated_at = now
        item.archived_at = None
    await session.commit()
    return item
