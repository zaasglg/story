"""Organization contacts are independent of chat contacts and private worker records."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit.service import add_audit_event
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.identity.dependencies import CurrentUser, get_db_session
from backend.identity.models import UserRole
from backend.workforce.models import OrganizationContact

router = APIRouter(prefix="/organization/contacts", tags=["organization contacts"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
Reader = Annotated[CurrentUser, Depends(require_permission(Permission.PROFILE_READ_OWN))]
Manager = Annotated[CurrentUser, Depends(require_permission(Permission.ORGANIZATION_MANAGE))]


class ContactWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    position: str = Field(min_length=1, max_length=128)
    department: str | None = Field(default=None, max_length=128)
    phone: str = Field(min_length=3, max_length=64)
    additional_phone: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=255, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    responsibilities: str | None = Field(default=None, max_length=2000)
    active: bool = True


class ContactRead(ContactWrite):
    model_config = ConfigDict(from_attributes=True)
    id: UUID


@router.get("", response_model=list[ContactRead])
async def contacts(
    session: Session,
    actor: Reader,
    q: str = Query(default="", max_length=160),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
):
    query = select(OrganizationContact).where(OrganizationContact.archived_at.is_(None))
    if actor.role not in (UserRole.ADMIN, UserRole.BOSS):
        query = query.where(OrganizationContact.active.is_(True))
    if q:
        query = query.where(
            or_(
                OrganizationContact.name.icontains(q, autoescape=True),
                OrganizationContact.position.icontains(q, autoescape=True),
            )
        )
    return list(
        (
            await session.scalars(
                query.order_by(OrganizationContact.name, OrganizationContact.id)
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )


@router.post("", response_model=ContactRead, status_code=201)
async def create(payload: ContactWrite, session: Session, actor: Manager):
    item = OrganizationContact(**payload.model_dump(), created_at=utc_now(), updated_at=utc_now())
    session.add(item)
    await session.flush()
    add_audit_event(
        session,
        actor_user_id=actor.id,
        action="CONTACT_CREATED",
        entity_type="organization_contact",
        entity_id=item.id,
        new_value={"name": item.name, "status": "ACTIVE" if item.active else "INACTIVE"},
    )
    await session.commit()
    return item


async def load(session, item_id):
    item = await session.get(OrganizationContact, item_id, with_for_update=True)
    if item is None or item.archived_at is not None:
        raise ApiError(404, "contact_not_found", "Contact was not found")
    return item


@router.put("/{item_id}", response_model=ContactRead)
async def update(item_id: UUID, payload: ContactWrite, session: Session, actor: Manager):
    item = await load(session, item_id)
    old = {"name": item.name, "status": "ACTIVE" if item.active else "INACTIVE"}
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    item.updated_at = utc_now()
    add_audit_event(
        session,
        actor_user_id=actor.id,
        action="CONTACT_UPDATED",
        entity_type="organization_contact",
        entity_id=item.id,
        old_value=old,
        new_value={"name": item.name, "status": "ACTIVE" if item.active else "INACTIVE"},
    )
    await session.commit()
    return item


@router.delete("/{item_id}", status_code=204)
async def archive(item_id: UUID, session: Session, actor: Manager):
    item = await load(session, item_id)
    item.archived_at = item.updated_at = utc_now()
    add_audit_event(
        session,
        actor_user_id=actor.id,
        action="CONTACT_ARCHIVED",
        entity_type="organization_contact",
        entity_id=item.id,
    )
    await session.commit()
    return Response(status_code=204)
