from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.errors import ApiError
from backend.core.time import ensure_utc, utc_now
from backend.identity.dependencies import CurrentUser
from backend.workforce.models import EmployeeDocument
from backend.workforce.routes import Session
from backend.workforce.service import require_worker

router = APIRouter(tags=["documents"])
Admin = Annotated[CurrentUser, Depends(require_permission(Permission.WORKER_MANAGE))]
Own = Annotated[CurrentUser, Depends(require_permission(Permission.PROFILE_READ_OWN))]
DocumentType = Literal["PASSPORT", "MEDICAL", "CONTRACT", "CERTIFICATE", "SAFETY", "OTHER"]
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
ALLOWED_TYPES = {
    "application/pdf": b"%PDF-",
    "image/jpeg": b"\xff\xd8\xff",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/webp": b"RIFF",
}


class DocumentRead(BaseModel):
    id: UUID
    worker_user_id: UUID
    document_type: DocumentType
    title: str
    filename: str
    content_type: str
    issue_date: date | None
    expires_at: date | None
    status: Literal["ACTIVE", "EXPIRING", "EXPIRED", "NO_EXPIRY"]
    created_at: datetime


def day(value: datetime | None) -> date | None:
    return ensure_utc(value).date() if value else None


def read(item: EmployeeDocument) -> DocumentRead:
    expiry = day(item.expires_at)
    today = utc_now().date()
    status = (
        "NO_EXPIRY"
        if expiry is None
        else "EXPIRED"
        if expiry < today
        else "EXPIRING"
        if expiry <= today + timedelta(days=30)
        else "ACTIVE"
    )
    return DocumentRead(
        id=item.id,
        worker_user_id=item.worker_user_id,
        document_type=item.document_type,
        title=item.title,
        filename=item.filename,
        content_type=item.content_type,
        issue_date=day(item.issue_date),
        expires_at=expiry,
        status=status,
        created_at=item.created_at,
    )


def stored_date(value: date | None) -> datetime | None:
    return datetime.combine(value, time.min, tzinfo=UTC) if value else None


async def load(session: Session, document_id: UUID) -> EmployeeDocument:
    item = await session.get(EmployeeDocument, document_id)
    if item is None or item.archived_at is not None:
        raise ApiError(404, "document_not_found", "Document was not found")
    return item


def file_response(item: EmployeeDocument) -> Response:
    safe_name = item.filename.replace('"', "").replace("\r", "").replace("\n", "")
    return Response(
        item.content,
        media_type=item.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/workers/me/documents", response_model=list[DocumentRead])
async def own_documents(session: Session, actor: Own) -> list[DocumentRead]:
    await require_worker(session, actor.id)
    rows = (
        await session.scalars(
            select(EmployeeDocument)
            .where(
                EmployeeDocument.worker_user_id == actor.id,
                EmployeeDocument.archived_at.is_(None),
            )
            .order_by(EmployeeDocument.expires_at, EmployeeDocument.created_at.desc())
        )
    ).all()
    return [read(item) for item in rows]


@router.get("/workers/me/documents/{document_id}/file")
async def own_document_file(document_id: UUID, session: Session, actor: Own) -> Response:
    item = await load(session, document_id)
    if item.worker_user_id != actor.id:
        raise ApiError(404, "document_not_found", "Document was not found")
    return file_response(item)


@router.get("/admin/documents", response_model=list[DocumentRead])
async def admin_documents(
    session: Session, _: Admin, worker_user_id: Annotated[UUID | None, Query()] = None
) -> list[DocumentRead]:
    query = select(EmployeeDocument).where(EmployeeDocument.archived_at.is_(None))
    if worker_user_id:
        query = query.where(EmployeeDocument.worker_user_id == worker_user_id)
    rows = (await session.scalars(query.order_by(EmployeeDocument.created_at.desc()))).all()
    return [read(item) for item in rows]


@router.post("/admin/documents", response_model=DocumentRead, status_code=201)
async def create_document(
    session: Session,
    actor: Admin,
    worker_user_id: Annotated[UUID, Form()],
    document_type: Annotated[DocumentType, Form()],
    title: Annotated[str, Form(min_length=1, max_length=255)],
    file: Annotated[UploadFile, File()],
    issue_date: Annotated[date | None, Form()] = None,
    expires_at: Annotated[date | None, Form()] = None,
) -> DocumentRead:
    await require_worker(session, worker_user_id)
    content_type = file.content_type or ""
    try:
        content = await file.read(MAX_DOCUMENT_BYTES + 1)
    finally:
        await file.close()
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ApiError(413, "document_too_large", "Document must not exceed 10 MB")
    signature = ALLOWED_TYPES.get(content_type)
    valid = signature is not None and content.startswith(signature)
    if content_type == "image/webp":
        valid = valid and len(content) >= 12 and content[8:12] == b"WEBP"
    if not valid:
        raise ApiError(422, "invalid_document", "Choose a valid PDF, JPG, PNG or WebP file")
    if issue_date and expires_at and expires_at < issue_date:
        raise ApiError(422, "invalid_document_dates", "Expiry date must not precede issue date")
    now = utc_now()
    item = EmployeeDocument(
        worker_user_id=worker_user_id,
        document_type=document_type,
        title=title.strip(),
        filename=(file.filename or "document")[:255],
        content_type=content_type,
        content=content,
        issue_date=stored_date(issue_date),
        expires_at=stored_date(expires_at),
        uploaded_by=actor.id,
        created_at=now,
        updated_at=now,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return read(item)


@router.delete("/admin/documents/{document_id}", status_code=204)
async def archive_document(document_id: UUID, session: Session, _: Admin) -> Response:
    item = await load(session, document_id)
    item.archived_at = item.updated_at = utc_now()
    await session.commit()
    return Response(status_code=204)


@router.get("/admin/documents/{document_id}/file")
async def admin_document_file(document_id: UUID, session: Session, _: Admin) -> Response:
    return file_response(await load(session, document_id))
