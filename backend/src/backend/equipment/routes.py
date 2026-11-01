from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit.service import add_audit_event
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.equipment.models import EquipmentIssue, EquipmentItem, EquipmentPhoto, StockMovement
from backend.identity.dependencies import CurrentUser, get_db_session
from backend.workforce.service import require_worker

router = APIRouter(tags=["equipment"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
Manager = Annotated[CurrentUser, Depends(require_permission(Permission.EQUIPMENT_MANAGE))]
Reader = Annotated[CurrentUser, Depends(require_permission(Permission.EQUIPMENT_READ_ALL))]


class ItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    serial_number: str | None = Field(default=None, min_length=1, max_length=128)
    quantity: int = Field(ge=0, le=1000000)
    photo_url: str | None = Field(default=None, max_length=128)


class ItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    photo_url: str | None = Field(default=None, max_length=128)


class ItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    serial_number: str | None
    available_quantity: int
    photo_url: str | None


class StockAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    delta: int = Field(ge=-1000000, le=1000000)
    reason: str = Field(min_length=1, max_length=255)


class IssueCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    equipment_id: UUID
    worker_user_id: UUID
    quantity: int = Field(gt=0, le=1000000)
    issue_date: date


class IssueReturn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    return_date: date


class IssueRead(BaseModel):
    id: UUID
    equipment_id: UUID
    equipment_name: str
    equipment_photo_url: str | None = None
    worker_user_id: UUID
    quantity: int
    issue_date: date
    return_date: date | None
    status: str


class MovementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    equipment_id: UUID
    delta: int
    reason: str


async def validate_photo(session: AsyncSession, url: str | None) -> None:
    if url is None:
        return
    try:
        prefix, identifier = url.rsplit("/", 1)
        if prefix != "/equipment-photos":
            raise ValueError
        photo_id = UUID(identifier)
    except ValueError as exc:
        raise ApiError(422, "invalid_photo", "Upload an equipment photo first") from exc
    if await session.get(EquipmentPhoto, photo_id) is None:
        raise ApiError(422, "invalid_photo", "Equipment photo was not found")


def event(
    session: AsyncSession,
    request: Request,
    actor: CurrentUser,
    action: str,
    entity_id: UUID,
    entity_type: str = "equipment",
) -> None:
    add_audit_event(
        session,
        actor_user_id=actor.id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        request_id=getattr(request.state, "request_id", None),
    )


def movement(
    session: AsyncSession, item_id: UUID, actor: CurrentUser, delta: int, reason: str
) -> None:
    session.add(
        StockMovement(
            equipment_id=item_id,
            actor_user_id=actor.id,
            delta=delta,
            reason=reason,
            created_at=utc_now(),
        )
    )


async def issue_read(session: AsyncSession, issue: EquipmentIssue) -> IssueRead:
    item = await session.get(EquipmentItem, issue.equipment_id)
    assert item is not None
    return IssueRead(
        id=issue.id,
        equipment_id=item.id,
        equipment_name=item.name,
        equipment_photo_url=item.photo_url,
        worker_user_id=issue.worker_user_id,
        quantity=issue.quantity,
        issue_date=issue.issue_date,
        return_date=issue.return_date,
        status="RETURNED" if issue.return_date else "ISSUED",
    )


@router.get("/admin/equipment", response_model=list[ItemRead])
@router.get("/boss/equipment", response_model=list[ItemRead])
async def list_items(session: Session, _: Reader) -> list[EquipmentItem]:
    return list(
        (
            await session.scalars(
                select(EquipmentItem)
                .where(EquipmentItem.archived_at.is_(None))
                .order_by(EquipmentItem.name)
            )
        ).all()
    )


@router.post("/admin/equipment", response_model=ItemRead, status_code=201)
async def create_item(
    payload: ItemCreate, request: Request, session: Session, actor: Manager
) -> EquipmentItem:
    await validate_photo(session, payload.photo_url)
    now = utc_now()
    item = EquipmentItem(
        name=payload.name,
        photo_url=payload.photo_url,
        serial_number=payload.serial_number,
        available_quantity=payload.quantity,
        created_at=now,
        updated_at=now,
    )
    session.add(item)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "serial_conflict", "Serial number already exists") from exc
    movement(session, item.id, actor, payload.quantity, "INITIAL_STOCK")
    event(session, request, actor, "EQUIPMENT_CREATED", item.id)
    await session.commit()
    return item


@router.get("/admin/equipment/{item_id}/movements", response_model=list[MovementRead])
@router.get("/boss/equipment/{item_id}/movements", response_model=list[MovementRead])
async def stock_history(item_id: UUID, session: Session, _: Reader) -> list[StockMovement]:
    if await session.get(EquipmentItem, item_id) is None:
        raise ApiError(404, "equipment_not_found", "Equipment was not found")
    return list(
        (
            await session.scalars(
                select(StockMovement)
                .where(StockMovement.equipment_id == item_id)
                .order_by(StockMovement.created_at)
            )
        ).all()
    )


@router.patch("/admin/equipment/{item_id}", response_model=ItemRead)
async def update_item(
    item_id: UUID, payload: ItemUpdate, request: Request, session: Session, actor: Manager
) -> EquipmentItem:
    item = await session.get(EquipmentItem, item_id)
    if item is None or item.archived_at is not None:
        raise ApiError(404, "equipment_not_found", "Equipment was not found")
    if "photo_url" in payload.model_fields_set:
        await validate_photo(session, payload.photo_url)
        item.photo_url = payload.photo_url
    item.name, item.updated_at = payload.name, utc_now()
    event(session, request, actor, "EQUIPMENT_UPDATED", item.id)
    await session.commit()
    return item


@router.post("/admin/equipment/{item_id}/stock", response_model=ItemRead)
async def adjust_stock(
    item_id: UUID, payload: StockAdjustment, request: Request, session: Session, actor: Manager
) -> EquipmentItem:
    result = await session.execute(
        update(EquipmentItem)
        .where(
            EquipmentItem.id == item_id,
            EquipmentItem.archived_at.is_(None),
            EquipmentItem.available_quantity + payload.delta >= 0,
        )
        .values(
            available_quantity=EquipmentItem.available_quantity + payload.delta,
            updated_at=utc_now(),
        )
        .returning(EquipmentItem)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise ApiError(
            409, "invalid_stock_adjustment", "Equipment is unavailable or stock would be negative"
        )
    movement(session, item.id, actor, payload.delta, payload.reason)
    event(session, request, actor, "STOCK_ADJUSTED", item.id)
    await session.commit()
    return item


@router.delete("/admin/equipment/{item_id}", status_code=204)
async def archive_item(
    item_id: UUID, request: Request, session: Session, actor: Manager
) -> Response:
    item = await session.scalar(
        select(EquipmentItem)
        .where(EquipmentItem.id == item_id, EquipmentItem.archived_at.is_(None))
        .with_for_update()
    )
    if item is None:
        raise ApiError(404, "equipment_not_found", "Equipment was not found")
    active = await session.scalar(
        select(EquipmentIssue.id)
        .where(EquipmentIssue.equipment_id == item_id, EquipmentIssue.return_date.is_(None))
        .limit(1)
    )
    if active:
        raise ApiError(409, "equipment_in_use", "Return issued equipment before archiving")
    item.archived_at = item.updated_at = utc_now()
    event(session, request, actor, "EQUIPMENT_ARCHIVED", item.id)
    await session.commit()
    return Response(status_code=204)


@router.get("/admin/equipment/issues", response_model=list[IssueRead])
@router.get("/boss/equipment/issues", response_model=list[IssueRead])
async def list_issues(session: Session, _: Reader) -> list[IssueRead]:
    rows = (
        await session.scalars(select(EquipmentIssue).order_by(EquipmentIssue.issue_date.desc()))
    ).all()
    return [await issue_read(session, row) for row in rows]


@router.get("/workers/me/equipment", response_model=list[IssueRead])
async def own_issues(
    session: Session,
    actor: Annotated[CurrentUser, Depends(require_permission(Permission.EQUIPMENT_READ_OWN))],
) -> list[IssueRead]:
    rows = (
        await session.scalars(
            select(EquipmentIssue)
            .where(EquipmentIssue.worker_user_id == actor.id)
            .order_by(EquipmentIssue.issue_date.desc())
        )
    ).all()
    return [await issue_read(session, row) for row in rows]


@router.post("/admin/equipment/issues", response_model=IssueRead, status_code=201)
async def create_issue(
    payload: IssueCreate, request: Request, session: Session, actor: Manager
) -> IssueRead:
    await require_worker(session, payload.worker_user_id)
    result = await session.execute(
        update(EquipmentItem)
        .where(
            EquipmentItem.id == payload.equipment_id,
            EquipmentItem.archived_at.is_(None),
            EquipmentItem.available_quantity >= payload.quantity,
        )
        .values(
            available_quantity=EquipmentItem.available_quantity - payload.quantity,
            updated_at=utc_now(),
        )
        .returning(EquipmentItem.id)
    )
    if result.scalar_one_or_none() is None:
        raise ApiError(409, "insufficient_stock", "Not enough available equipment")
    issue = EquipmentIssue(**payload.model_dump(), created_by=actor.id, created_at=utc_now())
    session.add(issue)
    await session.flush()
    movement(session, payload.equipment_id, actor, -payload.quantity, "ISSUE")
    event(session, request, actor, "EQUIPMENT_ISSUED", issue.id, "equipment_issue")
    await session.commit()
    return await issue_read(session, issue)


@router.post("/admin/equipment/issues/{issue_id}/return", response_model=IssueRead)
async def return_issue(
    issue_id: UUID, payload: IssueReturn, request: Request, session: Session, actor: Manager
) -> IssueRead:
    issue = await session.scalar(
        select(EquipmentIssue).where(EquipmentIssue.id == issue_id).with_for_update()
    )
    if issue is None:
        raise ApiError(404, "issue_not_found", "Equipment issue was not found")
    if payload.return_date < issue.issue_date:
        raise ApiError(422, "invalid_return_date", "Return date must not precede issue date")
    result = await session.execute(
        update(EquipmentIssue)
        .where(EquipmentIssue.id == issue_id, EquipmentIssue.return_date.is_(None))
        .values(return_date=payload.return_date, returned_by=actor.id)
        .returning(EquipmentIssue.id)
    )
    if result.scalar_one_or_none() is None:
        raise ApiError(409, "already_returned", "Equipment was already returned")
    await session.execute(
        update(EquipmentItem)
        .where(EquipmentItem.id == issue.equipment_id)
        .values(
            available_quantity=EquipmentItem.available_quantity + issue.quantity,
            updated_at=utc_now(),
        )
    )
    movement(session, issue.equipment_id, actor, issue.quantity, "RETURN")
    event(session, request, actor, "EQUIPMENT_RETURNED", issue.id, "equipment_issue")
    await session.commit()
    await session.refresh(issue)
    return await issue_read(session, issue)
