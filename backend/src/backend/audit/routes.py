from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit.service import safe_audit_data
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.identity.dependencies import CurrentUser, get_db_session
from backend.identity.models import AuditLog, EmployeeProfile
from backend.workforce.models import Brigade, City, WorkObject

router = APIRouter(tags=["audit"])
Session = Annotated[AsyncSession, Depends(get_db_session)]


class AuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    actor_user_id: UUID | None
    action: str
    entity_type: str
    entity_id: UUID | None
    created_at: datetime
    succeeded: bool
    actor_name: str | None = None
    subject_name: str | None = None
    changes: list[dict] = []


@router.get("/admin/audit", response_model=list[AuditRead])
async def own_admin_audit(
    session: Session,
    actor: Annotated[CurrentUser, Depends(require_permission(Permission.AUDIT_READ_ADMIN))],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    hide_session_refresh: bool = False,
):
    query = select(AuditLog).where(AuditLog.actor_user_id == actor.id)
    if hide_session_refresh:
        query = query.where(AuditLog.action != "AUTH_TOKEN_REFRESHED")
    rows = (
        await session.scalars(
            query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    actor_name = await session.scalar(
        select(EmployeeProfile.full_name).where(EmployeeProfile.user_id == actor.id)
    )
    result = []
    for row in rows:
        before = safe_audit_data(row.old_value) or {}
        after = safe_audit_data(row.new_value) or {}
        subject = (
            after.get("full_name")
            or after.get("name")
            or before.get("full_name")
            or before.get("name")
        )
        if not subject and row.entity_id:
            if row.entity_type in ("worker", "users", "user"):
                subject = await session.scalar(
                    select(EmployeeProfile.full_name).where(
                        EmployeeProfile.user_id == row.entity_id
                    )
                )
            elif row.entity_type == "employee_profiles":
                profile = await session.get(EmployeeProfile, row.entity_id)
                subject = profile.full_name if profile else None
            else:
                model = {
                    "city": City,
                    "cities": City,
                    "brigade": Brigade,
                    "brigades": Brigade,
                    "object": WorkObject,
                    "work_object": WorkObject,
                    "work_objects": WorkObject,
                }.get(row.entity_type)
                if model:
                    subject = await session.scalar(
                        select(model.name).where(model.id == row.entity_id)
                    )
        changes = [
            {"field": key, "before": before.get(key), "after": after.get(key)}
            for key in (
                "full_name",
                "name",
                "status",
                "position",
                "city",
                "address",
                "quantity",
                "hours",
                "m3",
            )
            if key in after and before.get(key) != after.get(key)
        ]
        result.append(
            AuditRead.model_validate(row).model_copy(
                update={
                    "actor_name": actor_name or "Администратор",
                    "subject_name": subject,
                    "changes": changes,
                }
            )
        )
    return result


class BossAuditRead(AuditRead):
    actor_role: str | None = None
    reason: str | None = None

    old_value: dict | None = None
    new_value: dict | None = None
    request_id: str | None = None
    actor_name: str | None = None


@router.get("/boss/audit", response_model=list[BossAuditRead])
async def full_audit(
    session: Session,
    _: Annotated[CurrentUser, Depends(require_permission(Permission.AUDIT_READ_ALL))],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    actor_user_id: UUID | None = None,
    entity_type: str | None = None,
    action: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    query = select(AuditLog)
    for value, condition in (
        (actor_user_id, AuditLog.actor_user_id == actor_user_id),
        (entity_type, AuditLog.entity_type == entity_type),
        (action, AuditLog.action == action),
        (date_from, AuditLog.created_at >= date_from if date_from else None),
        (date_to, AuditLog.created_at <= date_to if date_to else None),
    ):
        if value is not None:
            query = query.where(condition)
    rows = (
        await session.scalars(
            query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    actors = dict(
        (
            await session.execute(
                select(EmployeeProfile.user_id, EmployeeProfile.full_name).where(
                    EmployeeProfile.user_id.in_(
                        {row.actor_user_id for row in rows if row.actor_user_id}
                    )
                )
            )
        ).all()
    )
    return [
        BossAuditRead.model_validate(row).model_copy(
            update={
                "old_value": safe_audit_data(row.old_value),
                "new_value": safe_audit_data(row.new_value),
                "actor_name": actors.get(row.actor_user_id),
            }
        )
        for row in rows
    ]
