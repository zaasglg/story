from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Boolean, DateTime, ForeignKey, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from backend.audit.service import add_audit_event
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.identity.dependencies import CurrentUser, get_db_session
from backend.identity.models import Base, User, UserRole

PermitCode = Literal["GROUND", "HEIGHT", "ENCLOSED", "MACHINERY", "TOOLS"]
PERMIT_NAMES = {
    "GROUND": ("Наземные работы", "Жердегі жұмыстар"),
    "HEIGHT": ("Работы на высоте", "Биіктіктегі жұмыстар"),
    "ENCLOSED": ("Работы в закрытых помещениях", "Жабық нысандағы жұмыстар"),
    "MACHINERY": ("Управление спецтехникой", "Арнайы техниканы басқару"),
    "TOOLS": ("Работа со специальным инструментом", "Арнайы құрал-саймандармен жұмыс"),
}


class EmployeePermit(Base):
    __tablename__ = "employee_permits"
    worker_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    code: Mapped[str] = mapped_column(String(24), primary_key=True)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))


router = APIRouter(tags=["employee-permits"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
Reader = Annotated[CurrentUser, Depends(require_permission(Permission.WORKER_READ_ALL))]
Manager = Annotated[CurrentUser, Depends(require_permission(Permission.ORGANIZATION_MANAGE))]
OwnReader = Annotated[CurrentUser, Depends(require_permission(Permission.PROFILE_READ_OWN))]


class PermitUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    granted: bool


async def employee(session: AsyncSession, worker_id: UUID) -> User:
    user = await session.get(User, worker_id)
    if user is None or user.role not in (UserRole.WORKER, UserRole.MASTER):
        raise ApiError(404, "employee_not_found", "Worker or master was not found")
    return user


async def permit_list(session: AsyncSession, worker_id: UUID) -> list[dict]:
    await employee(session, worker_id)
    rows = (
        await session.scalars(
            select(EmployeePermit).where(EmployeePermit.worker_user_id == worker_id)
        )
    ).all()
    existing = {row.code: row for row in rows}
    return [
        {
            "code": code,
            "name": names[0],
            "name_kk": names[1],
            "granted": existing[code].granted if code in existing else False,
            "updated_at": existing[code].updated_at if code in existing else None,
        }
        for code, names in PERMIT_NAMES.items()
    ]


@router.get("/admin/workers/{worker_id}/permits")
async def list_permits(worker_id: UUID, session: Session, _: Reader) -> list[dict]:
    return await permit_list(session, worker_id)


@router.get("/workers/me/permits")
async def own_permits(session: Session, actor: OwnReader) -> list[dict]:
    return await permit_list(session, actor.id)


@router.put("/admin/workers/{worker_id}/permits/{code}")
async def set_permit(
    worker_id: UUID, code: PermitCode, payload: PermitUpdate, session: Session, actor: Manager
) -> list[dict]:
    # Lock the employee to serialize concurrent grants of the same missing row.
    await employee(session, worker_id)
    await session.scalar(select(User).where(User.id == worker_id).with_for_update())
    row = await session.get(EmployeePermit, (worker_id, code))
    before = row.granted if row else False
    if row is None:
        row = EmployeePermit(worker_user_id=worker_id, code=code)
        session.add(row)
    row.granted = payload.granted
    row.updated_at = utc_now()
    row.updated_by = actor.id
    add_audit_event(
        session,
        actor_user_id=actor.id,
        action="EMPLOYEE_PERMIT_GRANTED" if payload.granted else "EMPLOYEE_PERMIT_REVOKED",
        entity_type="employee_permit",
        entity_id=worker_id,
        old_value={"code": code, "granted": before},
        new_value={"code": code, "granted": payload.granted},
    )
    await session.commit()
    return await permit_list(session, worker_id)
