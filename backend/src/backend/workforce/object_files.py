from datetime import datetime
from typing import Annotated
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, select
from sqlalchemy.orm import Mapped, mapped_column

from backend.audit.service import add_audit_event
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.identity.dependencies import CurrentUser
from backend.identity.models import Base
from backend.workforce.models import WorkObject
from backend.workforce.routes import Session

router = APIRouter(prefix="/boss/objects/{object_id}/files", tags=["object-files"])
Boss = Annotated[CurrentUser, Depends(require_permission(Permission.OBJECT_MANAGE))]
MAX_BYTES = 25 * 1024 * 1024


class ObjectFile(Base):
    __tablename__ = "object_files"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    object_id: Mapped[UUID] = mapped_column(
        ForeignKey("work_objects.id", ondelete="RESTRICT"), index=True
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("object_files.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(8))
    content: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    size: Mapped[int] = mapped_column(default=0)


class FolderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    parent_id: UUID | None = None


class EntryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: UUID | None = None


def clean_name(name: str) -> str:
    name = name.strip()
    if (
        not name
        or len(name) > 255
        or name in (".", "..")
        or any(c in "/\\" or ord(c) < 32 for c in name)
    ):
        raise ApiError(
            422, "invalid_file_name", "Choose a name without slashes or control characters"
        )
    return name


async def work_object(session: Session, object_id: UUID, lock: bool = False) -> WorkObject:
    item = await session.get(WorkObject, object_id, with_for_update=lock)
    if item is None or item.archived_at is not None:
        raise ApiError(404, "object_not_found", "Object was not found")
    return item


async def entry(session: Session, object_id: UUID, entry_id: UUID) -> ObjectFile:
    item = await session.get(ObjectFile, entry_id)
    if item is None or item.object_id != object_id or item.archived_at is not None:
        raise ApiError(404, "file_not_found", "File or folder was not found")
    return item


async def parent(session: Session, object_id: UUID, parent_id: UUID | None) -> None:
    if parent_id is not None:
        folder = await entry(session, object_id, parent_id)
        if folder.kind != "folder":
            raise ApiError(422, "not_a_folder", "Parent must be a folder")


async def available(
    session: Session,
    object_id: UUID,
    parent_id: UUID | None,
    name: str,
    exclude: UUID | None = None,
) -> None:
    query = select(ObjectFile.id).where(
        ObjectFile.object_id == object_id,
        ObjectFile.parent_id == parent_id,
        ObjectFile.name == name,
        ObjectFile.archived_at.is_(None),
    )
    if exclude is not None:
        query = query.where(ObjectFile.id != exclude)
    if await session.scalar(query):
        raise ApiError(409, "file_name_exists", "A file or folder with this name already exists")


def read(item: ObjectFile) -> dict:
    return {
        "id": item.id,
        "parent_id": item.parent_id,
        "name": item.name,
        "kind": item.kind,
        "size": item.size,
        "created_at": item.created_at,
    }


def audit(session: Session, actor: CurrentUser, item: ObjectFile, action: str) -> None:
    add_audit_event(
        session,
        actor_user_id=actor.id,
        action=action,
        entity_type="object_file",
        entity_id=item.id,
        new_value={"object_id": str(item.object_id), "name": item.name},
    )


@router.get("")
async def listing(
    object_id: UUID,
    session: Session,
    _: Boss,
    parent_id: UUID | None = None,
    folders_only: bool = False,
) -> list[dict]:
    await work_object(session, object_id)
    await parent(session, object_id, parent_id)
    query = select(ObjectFile).where(
        ObjectFile.object_id == object_id, ObjectFile.archived_at.is_(None)
    )
    query = (
        query.where(ObjectFile.kind == "folder")
        if folders_only
        else query.where(ObjectFile.parent_id == parent_id)
    )
    rows = (await session.scalars(query.order_by(ObjectFile.kind.desc(), ObjectFile.name))).all()
    return [read(item) for item in rows]


@router.post("/folders", status_code=201)
async def create_folder(
    object_id: UUID, payload: FolderCreate, session: Session, actor: Boss
) -> dict:
    await work_object(session, object_id, lock=True)
    await parent(session, object_id, payload.parent_id)
    name = clean_name(payload.name)
    await available(session, object_id, payload.parent_id, name)
    item = ObjectFile(
        object_id=object_id,
        parent_id=payload.parent_id,
        name=name,
        kind="folder",
        size=0,
        created_by=actor.id,
        created_at=utc_now(),
    )
    session.add(item)
    await session.flush()
    audit(session, actor, item, "OBJECT_FOLDER_CREATED")
    await session.commit()
    return read(item)


@router.post("/upload", status_code=201)
async def upload(
    object_id: UUID,
    session: Session,
    actor: Boss,
    file: Annotated[UploadFile, File()],
    parent_id: Annotated[UUID | None, Form()] = None,
) -> dict:
    try:
        content = await file.read(MAX_BYTES + 1)
    finally:
        await file.close()
    if len(content) > MAX_BYTES:
        raise ApiError(413, "file_too_large", "File must not exceed 25 MB")
    await work_object(session, object_id, lock=True)
    await parent(session, object_id, parent_id)
    name = clean_name(file.filename or "file")
    await available(session, object_id, parent_id, name)
    item = ObjectFile(
        object_id=object_id,
        parent_id=parent_id,
        name=name,
        kind="file",
        size=len(content),
        content=content,
        created_by=actor.id,
        created_at=utc_now(),
    )
    session.add(item)
    await session.flush()
    audit(session, actor, item, "OBJECT_FILE_UPLOADED")
    await session.commit()
    return read(item)


@router.patch("/{entry_id}")
async def change(
    object_id: UUID, entry_id: UUID, payload: EntryUpdate, session: Session, actor: Boss
) -> dict:
    await work_object(session, object_id, lock=True)
    item = await entry(session, object_id, entry_id)
    target = payload.parent_id if "parent_id" in payload.model_fields_set else item.parent_id
    await parent(session, object_id, target)
    ancestor = target
    while ancestor is not None:
        if ancestor == item.id:
            raise ApiError(422, "folder_cycle", "A folder cannot be moved inside itself")
        ancestor = (await entry(session, object_id, ancestor)).parent_id
    name = clean_name(payload.name) if payload.name is not None else item.name
    await available(session, object_id, target, name, exclude=item.id)
    item.parent_id, item.name = target, name
    audit(session, actor, item, "OBJECT_FILE_UPDATED")
    await session.commit()
    return read(item)


@router.delete("/{entry_id}", status_code=204)
async def remove(object_id: UUID, entry_id: UUID, session: Session, actor: Boss) -> Response:
    await work_object(session, object_id, lock=True)
    item = await entry(session, object_id, entry_id)
    rows = (
        await session.scalars(
            select(ObjectFile).where(
                ObjectFile.object_id == object_id, ObjectFile.archived_at.is_(None)
            )
        )
    ).all()
    removed = {item.id}
    while True:
        children = {row.id for row in rows if row.parent_id in removed}
        expanded = removed | children
        if expanded == removed:
            break
        removed = expanded
    now = utc_now()
    for row in rows:
        if row.id in removed:
            row.archived_at = now
    audit(session, actor, item, "OBJECT_FILE_ARCHIVED")
    await session.commit()
    return Response(status_code=204)


@router.get("/{entry_id}/download")
async def download(object_id: UUID, entry_id: UUID, session: Session, _: Boss) -> Response:
    await work_object(session, object_id)
    item = await entry(session, object_id, entry_id)
    if item.kind != "file":
        raise ApiError(422, "not_a_file", "Choose a file to download")
    await session.refresh(item, attribute_names=["content"])
    return Response(
        item.content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(item.name, safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
