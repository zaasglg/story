from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.audit.service import add_audit_event
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.identity.dependencies import CurrentUser, get_db_session
from backend.identity.models import AccountStatus, AuthSession, EmployeeProfile, User, UserRole
from backend.identity.phone import normalize_phone
from backend.identity.security import hash_password

router = APIRouter(tags=["administrators"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
Manager = Annotated[CurrentUser, Depends(require_permission(Permission.USER_ROLE_MANAGE))]


class AdminCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str = Field(min_length=1, max_length=255)
    phone: str = Field(min_length=5, max_length=32)
    password: str = Field(min_length=8, max_length=128, repr=False)

    @field_validator("full_name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name must not be blank")
        return value.strip()


class AdminRead(BaseModel):
    id: UUID
    full_name: str
    phone: str
    status: str
    created_at: datetime


class AdminStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ACTIVE", "BLOCKED"]


class AdminPassword(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=8, max_length=128, repr=False)


def project(user: User) -> AdminRead:
    return AdminRead(
        id=user.id,
        full_name=user.profile.full_name if user.profile else "",
        phone=user.phone,
        status=user.status.value,
        created_at=user.created_at,
    )


async def require_admin(session: AsyncSession, admin_id: UUID) -> User:
    user = await session.scalar(
        select(User)
        .where(User.id == admin_id, User.role == UserRole.ADMIN, User.archived_at.is_(None))
        .options(selectinload(User.profile))
        .with_for_update()
    )
    if user is None:
        raise ApiError(404, "administrator_not_found", "Administrator was not found")
    return user


def audit(session: AsyncSession, actor: CurrentUser, user: User, action: str) -> None:
    add_audit_event(
        session,
        actor_user_id=actor.id,
        action=action,
        entity_type="administrator",
        entity_id=user.id,
    )


async def revoke_sessions(session: AsyncSession, user: User) -> None:
    user.auth_version += 1
    user.updated_at = utc_now()
    await session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utc_now())
    )


@router.get("/boss/administrators", response_model=list[AdminRead])
async def administrators(
    session: Session,
    _: Manager,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(max_length=255)] = None,
) -> list[AdminRead]:
    query = (
        select(User)
        .join(EmployeeProfile, EmployeeProfile.user_id == User.id)
        .where(
            User.role == UserRole.ADMIN,
            User.archived_at.is_(None),
            EmployeeProfile.archived_at.is_(None),
        )
        .options(selectinload(User.profile))
    )
    if q and q.strip():
        query = query.where(
            EmployeeProfile.full_name.icontains(q.strip(), autoescape=True)
            | User.phone.icontains(q.strip(), autoescape=True)
        )
    users = await session.scalars(
        query.order_by(User.created_at.desc(), User.id).limit(limit).offset(offset)
    )
    return [project(user) for user in users]


@router.post("/boss/administrators", response_model=AdminRead, status_code=201)
async def create_administrator(payload: AdminCreate, session: Session, actor: Manager) -> AdminRead:
    now = utc_now()
    user = User(
        phone=normalize_phone(payload.phone),
        role=UserRole.ADMIN,
        password_hash=hash_password(payload.password),
        status=AccountStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    try:
        await session.flush()
        user.profile = EmployeeProfile(
            user_id=user.id,
            full_name=payload.full_name,
            employee_id=f"ADMIN-{uuid4().hex}",
            created_at=now,
            updated_at=now,
        )
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "administrator_conflict", "Phone number is already registered") from exc
    audit(session, actor, user, "ADMINISTRATOR_CREATED")
    await session.commit()
    return project(user)


@router.put("/boss/administrators/{admin_id}/status", response_model=AdminRead)
async def change_status(
    admin_id: UUID, payload: AdminStatus, session: Session, actor: Manager
) -> AdminRead:
    user = await require_admin(session, admin_id)
    if user.status.value != payload.status:
        user.status = AccountStatus(payload.status)
        await revoke_sessions(session, user)
        audit(
            session,
            actor,
            user,
            "ADMINISTRATOR_BLOCKED" if payload.status == "BLOCKED" else "ADMINISTRATOR_UNBLOCKED",
        )
        await session.commit()
    return project(user)


@router.post("/boss/administrators/{admin_id}/password", status_code=204)
async def reset_password(
    admin_id: UUID, payload: AdminPassword, session: Session, actor: Manager
) -> None:
    user = await require_admin(session, admin_id)
    user.password_hash = hash_password(payload.password)
    await revoke_sessions(session, user)
    audit(session, actor, user, "ADMINISTRATOR_PASSWORD_RESET")
    await session.commit()
