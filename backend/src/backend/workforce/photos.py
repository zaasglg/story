import warnings
from io import BytesIO
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import or_, select
from starlette.concurrency import run_in_threadpool

from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.identity.dependencies import CurrentUser
from backend.identity.models import EmployeeProfile, UserRole
from backend.workforce.models import EmployeePhoto
from backend.workforce.routes import Session

router = APIRouter(tags=["workforce"])
MAX_PHOTO_BYTES = 5 * 1024 * 1024


def normalize_photo(content: bytes) -> bytes:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content), formats=["JPEG", "PNG", "WEBP"]) as image:
                if image.width * image.height > 20_000_000:
                    raise ApiError(422, "invalid_photo", "Photo dimensions are too large")
                image.load()
                photo = ImageOps.exif_transpose(image).convert("RGBA")
                photo.thumbnail((768, 768))
                background = Image.new("RGB", photo.size, "white")
                background.paste(photo, mask=photo.getchannel("A"))
                output = BytesIO()
                background.save(output, format="JPEG", quality=85)
                return output.getvalue()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ApiError(422, "invalid_photo", "Choose a valid JPEG, PNG or WebP photo") from exc


@router.post("/admin/worker-photos", status_code=201)
async def upload_worker_photo(
    file: UploadFile,
    session: Session,
    actor: Annotated[CurrentUser, Depends(require_permission(Permission.WORKER_MANAGE))],
) -> dict[str, str]:
    try:
        content = await file.read(MAX_PHOTO_BYTES + 1)
    finally:
        await file.close()
    if len(content) > MAX_PHOTO_BYTES:
        raise ApiError(413, "photo_too_large", "Photo must not exceed 5 MB")
    normalized = await run_in_threadpool(normalize_photo, content)
    photo = EmployeePhoto(content=normalized, uploaded_by=actor.id, created_at=utc_now())
    session.add(photo)
    await session.commit()
    return {"photo_url": f"/photos/{photo.id}"}


@router.get("/photos/{photo_id}")
async def get_worker_photo(
    photo_id: UUID,
    session: Session,
    actor: Annotated[CurrentUser, Depends(require_permission(Permission.WORKER_PUBLIC_READ))],
) -> Response:
    photo = await session.get(EmployeePhoto, photo_id)
    if photo is None:
        raise ApiError(404, "photo_not_found", "Photo was not found")
    if actor.role == UserRole.MASTER:
        visible = await session.scalar(
            select(EmployeeProfile.user_id).where(
                EmployeeProfile.photo_url == f"/photos/{photo_id}",
                EmployeeProfile.archived_at.is_(None),
                or_(
                    EmployeeProfile.master_user_id == actor.id, EmployeeProfile.user_id == actor.id
                ),
            )
        )
        if visible is None:
            raise ApiError(404, "photo_not_found", "Photo was not found")
    return Response(
        photo.content,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )
