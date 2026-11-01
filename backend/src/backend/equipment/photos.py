"""Durable equipment images with the same permissions as equipment records."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, UploadFile
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from backend.authorization.permissions import Permission, has_permission
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.equipment.models import EquipmentIssue, EquipmentItem, EquipmentPhoto
from backend.equipment.routes import Manager, Session
from backend.identity.dependencies import CurrentUser, get_current_user
from backend.workforce.photos import MAX_PHOTO_BYTES, normalize_photo

router = APIRouter(tags=["equipment"])


@router.post("/admin/equipment-photos", status_code=201)
async def upload_photo(file: UploadFile, session: Session, actor: Manager) -> dict[str, str]:
    try:
        content = await file.read(MAX_PHOTO_BYTES + 1)
    finally:
        await file.close()
    if len(content) > MAX_PHOTO_BYTES:
        raise ApiError(413, "photo_too_large", "Photo must not exceed 5 MB")
    photo = EquipmentPhoto(
        content=await run_in_threadpool(normalize_photo, content),
        uploaded_by=actor.id,
        created_at=utc_now(),
    )
    session.add(photo)
    await session.commit()
    return {"photo_url": f"/equipment-photos/{photo.id}"}


@router.get("/equipment-photos/{photo_id}")
async def read_photo(
    photo_id: UUID,
    session: Session,
    actor: Annotated[CurrentUser, Depends(get_current_user)],
) -> Response:
    if not has_permission(actor.role, Permission.EQUIPMENT_READ_ALL):
        if not has_permission(actor.role, Permission.EQUIPMENT_READ_OWN):
            raise ApiError(403, "permission_denied", "Equipment access is required")
        issued = await session.scalar(
            select(EquipmentIssue.id)
            .join(EquipmentItem, EquipmentItem.id == EquipmentIssue.equipment_id)
            .where(
                EquipmentIssue.worker_user_id == actor.id,
                EquipmentItem.photo_url == f"/equipment-photos/{photo_id}",
            )
            .limit(1)
        )
        if issued is None:
            raise ApiError(404, "photo_not_found", "Photo was not found")
    photo = await session.get(EquipmentPhoto, photo_id)
    if photo is None:
        raise ApiError(404, "photo_not_found", "Photo was not found")
    return Response(
        photo.content,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )
