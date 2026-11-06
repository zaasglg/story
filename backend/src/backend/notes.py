from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.identity.dependencies import CurrentUser, get_db_session
from backend.identity.models import Base

router = APIRouter(prefix="/boss/notes", tags=["notes"])
Boss = Annotated[CurrentUser, Depends(require_permission(Permission.OBJECT_MANAGE))]
Session = Annotated[AsyncSession, Depends(get_db_session)]


class Note(Base):
    __tablename__ = "personal_notes"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NoteWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="", max_length=255)
    body: str = Field(default="", max_length=50000)
    pinned: bool = False


class NoteUpdate(NoteWrite):
    revision: int = Field(ge=0)


class NoteRead(NoteWrite):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    revision: int
    created_at: datetime
    updated_at: datetime


@router.get("", response_model=list[NoteRead])
async def notes(
    session: Session,
    actor: Boss,
    q: str = Query(default="", max_length=255),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    query = select(Note).where(Note.owner_id == actor.id)
    if q.strip():
        query = query.where(
            or_(
                Note.title.icontains(q.strip(), autoescape=True),
                Note.body.icontains(q.strip(), autoescape=True),
            )
        )
    return (
        await session.scalars(
            query.order_by(Note.pinned.desc(), Note.updated_at.desc(), Note.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()


@router.post("", response_model=NoteRead, status_code=201)
async def create_note(payload: NoteWrite, session: Session, actor: Boss):
    now = utc_now()
    item = Note(**payload.model_dump(), owner_id=actor.id, created_at=now, updated_at=now)
    session.add(item)
    await session.commit()
    return item


@router.put("/{note_id}", response_model=NoteRead)
async def update_note(note_id: UUID, payload: NoteUpdate, session: Session, actor: Boss):
    item = await session.scalar(
        select(Note).where(Note.id == note_id, Note.owner_id == actor.id).with_for_update()
    )
    if item is None:
        raise ApiError(404, "note_not_found", "Note was not found")
    if item.revision != payload.revision:
        raise ApiError(409, "stale_note", "Note changed on another device")
    for key, value in payload.model_dump(exclude={"revision"}).items():
        setattr(item, key, value)
    item.revision += 1
    item.updated_at = utc_now()
    await session.commit()
    return item


@router.delete("/{note_id}", status_code=204)
async def delete_note(note_id: UUID, session: Session, actor: Boss):
    item = await session.scalar(
        select(Note).where(Note.id == note_id, Note.owner_id == actor.id).with_for_update()
    )
    if item is None:
        raise ApiError(404, "note_not_found", "Note was not found")
    await session.delete(item)
    await session.commit()
    return Response(status_code=204)
