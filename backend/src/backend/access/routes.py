import base64
import hashlib
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import qrcode
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.access.models import PassToken, RatingEvent, WorkAuthorization
from backend.access.rating import append_rating_event, reversed_event_ids
from backend.audit.service import add_audit_event
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.errors import ApiError
from backend.core.time import ensure_utc, utc_now
from backend.equipment.models import EquipmentIssue, EquipmentItem
from backend.identity.dependencies import CurrentUser, get_db_session
from backend.identity.models import AccountStatus, EmployeeProfile, User, UserRole
from backend.workforce.models import City
from backend.workforce.service import active_assignment, require_worker

router = APIRouter(tags=["access-rating"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
Manager = Annotated[CurrentUser, Depends(require_permission(Permission.ORGANIZATION_MANAGE))]
Own = Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_READ_OWN))]


class AuthorizationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_user_id: UUID
    valid_from: date
    valid_until: date
    reference: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def valid_dates(self):
        if self.valid_until < self.valid_from:
            raise ValueError("End date must follow start date")
        return self


class AuthorizationRead(AuthorizationCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    revoked_at: datetime | None = None


@router.get("/admin/work-authorizations", response_model=list[AuthorizationRead])
async def list_authorizations(session: Session, _: Manager) -> list[WorkAuthorization]:
    return list(
        (
            await session.scalars(
                select(WorkAuthorization).order_by(WorkAuthorization.created_at.desc())
            )
        ).all()
    )


@router.patch("/admin/work-authorizations/{item_id}", response_model=AuthorizationRead)
async def update_authorization(
    item_id: UUID, payload: AuthorizationCreate, session: Session, actor: Manager
) -> WorkAuthorization:
    item = await session.scalar(
        select(WorkAuthorization).where(WorkAuthorization.id == item_id).with_for_update()
    )
    if item is None:
        raise ApiError(404, "authorization_not_found", "Work authorization was not found")
    if item.revoked_at is not None:
        raise ApiError(409, "authorization_revoked", "Revoked authorization cannot be changed")
    if payload.worker_user_id != item.worker_user_id:
        raise ApiError(422, "authorization_worker_mismatch", "Worker cannot be changed")
    for field in ("valid_from", "valid_until", "reference"):
        setattr(item, field, getattr(payload, field))
    add_audit_event(
        session,
        actor_user_id=actor.id,
        action="WORK_AUTHORIZATION_UPDATED",
        entity_type="work_authorization",
        entity_id=item.id,
        new_value=payload.model_dump(mode="json"),
    )
    await session.commit()
    return item


@router.post("/admin/work-authorizations", response_model=AuthorizationRead, status_code=201)
async def grant(
    payload: AuthorizationCreate, session: Session, actor: Manager
) -> WorkAuthorization:
    await require_worker(session, payload.worker_user_id)
    item = WorkAuthorization(**payload.model_dump(), created_by=actor.id, created_at=utc_now())
    session.add(item)
    await session.flush()
    add_audit_event(
        session,
        actor_user_id=actor.id,
        action="WORK_AUTHORIZATION_GRANTED",
        entity_type="work_authorization",
        entity_id=item.id,
        new_value=payload.model_dump(mode="json"),
    )
    await session.commit()
    return item


@router.post("/admin/work-authorizations/{item_id}/revoke")
async def revoke(item_id: UUID, session: Session, actor: Manager) -> dict[str, str]:
    item = await session.get(WorkAuthorization, item_id)
    if item is None:
        raise ApiError(404, "authorization_not_found", "Work authorization was not found")
    if item.revoked_at is None:
        item.revoked_at = utc_now()
        add_audit_event(
            session,
            actor_user_id=actor.id,
            action="WORK_AUTHORIZATION_REVOKED",
            entity_type="work_authorization",
            entity_id=item.id,
        )
        await session.commit()
    return {"status": "REVOKED"}


async def pass_details(session: AsyncSession, worker_id: UUID) -> dict:
    worker = await require_worker(session, worker_id)
    profile = worker.profile
    assert profile is not None
    city = await session.get(City, profile.city_id) if profile.city_id else None
    today = utc_now().astimezone(ZoneInfo(city.timezone if city else "Europe/Moscow")).date()
    valid = await session.scalar(
        select(WorkAuthorization)
        .where(
            WorkAuthorization.worker_user_id == worker_id,
            WorkAuthorization.revoked_at.is_(None),
            WorkAuthorization.valid_from <= today,
            WorkAuthorization.valid_until >= today,
        )
        .order_by(WorkAuthorization.valid_until.desc(), WorkAuthorization.created_at.desc())
        .limit(1)
    )
    active = worker.status == AccountStatus.ACTIVE and valid is not None
    assignment = await active_assignment(session, worker_id)
    items = (
        await session.execute(
            select(EquipmentItem.name, EquipmentIssue.quantity)
            .join(EquipmentIssue, EquipmentIssue.equipment_id == EquipmentItem.id)
            .where(EquipmentIssue.worker_user_id == worker_id, EquipmentIssue.return_date.is_(None))
        )
    ).all()
    return {
        "worker_user_id": worker.id,
        "full_name": profile.full_name,
        "role": worker.role,
        "city": city.name if city else profile.city,
        "brigade": assignment.brigade.name if assignment else None,
        "employee_id": profile.employee_id,
        "photo_url": profile.photo_url,
        "object_name": assignment.work_object.name
        if assignment and assignment.work_object
        else None,
        "valid_from": valid.valid_from if valid else None,
        "valid_until": valid.valid_until if valid else None,
        "rating": (await rating_details(session, worker_id))["rating"],
        "status": "ACTIVE" if active else "BLOCKED",
        "permissions": ["WORK_ENTRY"] if active else [],
        "issued_equipment": [{"name": row[0], "quantity": row[1]} for row in items],
    }


@router.get("/workers/me/pass")
async def own_pass(session: Session, actor: Own) -> dict:
    details = await pass_details(session, actor.id)
    token = secrets.token_urlsafe(32)
    expiry = utc_now() + timedelta(minutes=2)
    session.add(
        PassToken(
            worker_user_id=actor.id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=expiry,
        )
    )
    await session.commit()
    png = BytesIO()
    qrcode.make(token).save(png, format="PNG")
    return {
        **details,
        "phone": (await require_worker(session, actor.id)).phone,
        "qr_payload": token,
        "qr_png_base64": base64.b64encode(png.getvalue()).decode(),
        "expires_at": expiry,
    }


class ScanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qr_payload: str = Field(min_length=20, max_length=128)


@router.post("/admin/pass-scans")
async def scan(payload: ScanCreate, session: Session, actor: Manager) -> dict:
    token = await session.scalar(
        select(PassToken)
        .where(PassToken.token_hash == hashlib.sha256(payload.qr_payload.encode()).hexdigest())
        .with_for_update()
    )
    if token is None or token.consumed_at is not None or ensure_utc(token.expires_at) <= utc_now():
        raise ApiError(422, "invalid_pass_token", "Pass token is expired or already used")
    details = await pass_details(session, token.worker_user_id)
    result = await session.execute(
        update(PassToken)
        .where(PassToken.id == token.id, PassToken.consumed_at.is_(None))
        .values(consumed_at=utc_now())
        .returning(PassToken.id)
    )
    if result.scalar_one_or_none() is None:
        raise ApiError(409, "pass_token_used", "Pass token was already used")
    add_audit_event(
        session,
        actor_user_id=actor.id,
        action="PASS_SCANNED",
        entity_type="worker",
        entity_id=token.worker_user_id,
        new_value={"status": details["status"]},
    )
    await session.commit()
    return details


DELTAS = {
    "ABSENCE": Decimal("-0.1"),
    "NO_BELT": Decimal("-2.0"),
    "NO_GLASSES": Decimal("-0.2"),
    "NO_HELMET": Decimal("-0.9"),
    "STREAK_14": Decimal("0.1"),
}


class ViolationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_user_id: UUID
    event_date: date
    event_type: Literal["NO_BELT", "NO_GLASSES", "NO_HELMET"]
    reason: str = Field(min_length=1, max_length=2000)
    photo_url: str | None = Field(default=None, max_length=2048)


class RatingEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    event_date: date
    event_type: str
    delta: Decimal
    reason: str
    photo_url: str | None
    reversed: bool = False
    created_at: datetime
    created_by: UUID | None
    source: str
    actor_name: str | None = None
    rating_before: Decimal | None
    rating_after: Decimal | None


async def rating_details(session: AsyncSession, worker_id: UUID) -> dict:
    rows = (
        await session.scalars(
            select(RatingEvent)
            .where(RatingEvent.worker_user_id == worker_id)
            .order_by(RatingEvent.event_date, RatingEvent.created_at, RatingEvent.id)
        )
    ).all()
    authors = dict(
        (
            await session.execute(
                select(EmployeeProfile.user_id, EmployeeProfile.full_name).where(
                    EmployeeProfile.user_id.in_({row.created_by for row in rows if row.created_by})
                )
            )
        ).all()
    )
    rating = Decimal("5.0")
    cancelled = reversed_event_ids(rows)
    for row in rows:
        if str(row.id) in cancelled or row.event_type == "ATTENDANCE_REVERSED":
            continue
        rating = max(Decimal("0"), min(Decimal("5"), rating + row.delta))
    return {
        "rating": rating,
        "history": [
            RatingEventRead.model_validate(row).model_copy(
                update={
                    "reversed": str(row.id) in cancelled,
                    "actor_name": authors.get(row.created_by) if row.created_by else "Система",
                }
            )
            for row in rows
        ],
    }


@router.post("/admin/rating-events", response_model=RatingEventRead, status_code=201)
async def violation(
    payload: ViolationCreate,
    session: Session,
    actor: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_MANAGE))],
) -> RatingEvent:
    await require_worker(session, payload.worker_user_id)
    await session.scalar(select(User).where(User.id == payload.worker_user_id).with_for_update())
    item = RatingEvent(
        **payload.model_dump(),
        delta=DELTAS[payload.event_type],
        source_key=secrets.token_hex(16),
        created_by=actor.id,
        created_at=utc_now(),
    )
    await append_rating_event(session, item)
    add_audit_event(
        session,
        actor_user_id=actor.id,
        action="RATING_EVENT_CREATED",
        entity_type="rating_event",
        entity_id=item.id,
        new_value={"type": item.event_type, "delta": str(item.delta)},
    )
    await session.commit()
    return item


@router.get("/workers/me/rating")
async def own_rating(session: Session, actor: Own) -> dict:
    return await rating_details(session, actor.id)


@router.get("/workers/{worker_id}/rating/public")
async def public_rating(
    worker_id: UUID,
    session: Session,
    actor: Annotated[CurrentUser, Depends(require_permission(Permission.WORKER_PUBLIC_READ))],
) -> dict:
    worker = await require_worker(session, worker_id)
    if actor.role == UserRole.MASTER and worker.profile.master_user_id != actor.id:
        raise ApiError(404, "worker_not_found", "Worker was not found")
    return await rating_details(session, worker_id)
