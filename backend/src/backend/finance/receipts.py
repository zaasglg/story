from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Response, UploadFile
from sqlalchemy import DateTime, ForeignKey, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.finance.routes import Session
from backend.identity.dependencies import CurrentUser
from backend.identity.models import Base, UserRole

router = APIRouter(tags=["finance-receipts"])
Manager = Annotated[CurrentUser, Depends(require_permission(Permission.EXPENSE_MANAGE))]
Reader = Annotated[CurrentUser, Depends(require_permission(Permission.EXPENSE_READ))]
MAX_BYTES = 10 * 1024 * 1024


class FinanceReceipt(Base):
    __tablename__ = "finance_receipts"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


@router.post("/finance/evidence", status_code=201)
async def upload_evidence(
    session: Session,
    actor: Annotated[CurrentUser, Depends(require_permission(Permission.FINANCIAL_REQUEST_CREATE))],
    file: Annotated[UploadFile, File()],
) -> dict:
    result = await upload_receipt(session, actor, file)
    result["url"] = result["url"].replace("/finance/receipts/", "/finance/evidence/")
    return result


@router.get("/finance/evidence/{receipt_id}/file")
async def download_evidence(
    receipt_id: UUID,
    session: Session,
    actor: Annotated[CurrentUser, Depends(require_permission(Permission.WORKER_READ_ALL))],
) -> Response:
    item = await session.get(FinanceReceipt, receipt_id)
    if item is None or (actor.role != UserRole.BOSS and item.created_by != actor.id):
        raise ApiError(404, "receipt_not_found", "Evidence was not found")
    return await download_receipt(receipt_id, session, actor)


@router.post("/finance/receipts", status_code=201)
async def upload_receipt(
    session: Session, actor: Manager, file: Annotated[UploadFile, File()]
) -> dict:
    try:
        content = await file.read(MAX_BYTES + 1)
    finally:
        await file.close()
    if len(content) > MAX_BYTES:
        raise ApiError(413, "receipt_too_large", "Receipt must not exceed 10 MB")
    content_type = next(
        (
            kind
            for signature, kind in [
                (b"%PDF-", "application/pdf"),
                (b"\xff\xd8\xff", "image/jpeg"),
                (b"\x89PNG\r\n\x1a\n", "image/png"),
            ]
            if content.startswith(signature)
        ),
        None,
    )
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        content_type = "image/webp"
    if content_type is None:
        raise ApiError(422, "invalid_receipt", "Choose a PDF, JPG, PNG or WebP receipt")
    item = FinanceReceipt(
        filename=(file.filename or "receipt").replace("\\", "/").split("/")[-1][:255],
        content_type=content_type,
        content=content,
        created_by=actor.id,
        created_at=utc_now(),
    )
    session.add(item)
    await session.commit()
    return {"url": f"/finance/receipts/{item.id}/file", "filename": item.filename}


@router.get("/finance/receipts/{receipt_id}/file")
async def download_receipt(receipt_id: UUID, session: Session, _: Reader) -> Response:
    item = await session.get(FinanceReceipt, receipt_id)
    if item is None:
        raise ApiError(404, "receipt_not_found", "Receipt was not found")
    return Response(
        item.content,
        media_type=item.content_type,
        headers={
            "Content-Disposition": 'attachment; filename="receipt.'
            + {
                "application/pdf": "pdf",
                "image/jpeg": "jpg",
                "image/png": "png",
                "image/webp": "webp",
            }[item.content_type]
            + '"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
