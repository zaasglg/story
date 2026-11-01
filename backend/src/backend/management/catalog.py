from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.access.models import WorkAuthorization
from backend.access.routes import AuthorizationRead
from backend.authorization.permissions import Permission, has_permission
from backend.core.crypto import PiiCipher
from backend.core.errors import ApiError
from backend.equipment.models import EquipmentIssue, EquipmentItem
from backend.equipment.routes import ItemRead, issue_read
from backend.finance.models import Expense, FinancialRequest
from backend.finance.payroll import RequestRead
from backend.finance.routes import ExpenseRead
from backend.identity.dependencies import CurrentUser, get_current_user, get_db_session
from backend.identity.models import EmployeeProfile, User, UserRole
from backend.operations.models import (
    AttendanceRecord,
    ObjectCalendarDay,
    TariffHistory,
    WorkDay,
    WorkSchedule,
)
from backend.operations.object_calendar import DayRead
from backend.operations.routes import (
    attendance_projection,
    schedule_projection,
    schedule_query,
    work_day_query,
)
from backend.operations.routes import (
    private_projection as day_projection,
)
from backend.operations.schemas import TariffRead
from backend.workforce.models import (
    Brigade,
    City,
    ProfileChangeRequest,
    WorkerAssignment,
    WorkObject,
)
from backend.workforce.request_pii import request_read
from backend.workforce.routes import brigade_read, object_read
from backend.workforce.schemas import ChangeRequestRead, CityRead
from backend.workforce.service import private_projection as worker_projection

router = APIRouter(prefix="/admin/catalog", tags=["administrative browsing"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
Actor = Annotated[CurrentUser, Depends(get_current_user)]


@dataclass(frozen=True)
class Resource:
    model: type
    permission: Permission
    date_key: str = "created_at"
    statuses: tuple[str, ...] = ()
    reader: type[BaseModel] | None = None


RESOURCES = {
    "object-calendar": Resource(
        ObjectCalendarDay, Permission.ORGANIZATION_READ, "work_date", reader=DayRead
    ),
    "cities": Resource(City, Permission.ORGANIZATION_MANAGE, reader=CityRead),
    "workers": Resource(User, Permission.WORKER_READ_ALL, statuses=("ACTIVE", "BLOCKED")),
    "assignments": Resource(User, Permission.WORKER_READ_ALL, statuses=("ACTIVE", "BLOCKED")),
    "objects": Resource(
        WorkObject,
        Permission.ORGANIZATION_READ,
        statuses=("PLANNED", "ACTIVE", "PAUSED", "COMPLETED"),
    ),
    "brigades": Resource(Brigade, Permission.ORGANIZATION_READ),
    "schedules": Resource(WorkSchedule, Permission.WORKDAY_READ_ALL, "work_date"),
    "attendance": Resource(
        AttendanceRecord,
        Permission.WORKDAY_MANAGE,
        "work_date",
        ("PRESENT", "ABSENT", "LATE", "EXCUSED"),
    ),
    "work-days": Resource(
        WorkDay, Permission.WORKDAY_READ_ALL, "work_date", ("DRAFT", "APPROVED", "REJECTED")
    ),
    "tariffs": Resource(TariffHistory, Permission.WORKDAY_MANAGE, "effective_from"),
    "equipment": Resource(EquipmentItem, Permission.EQUIPMENT_READ_ALL, reader=ItemRead),
    "equipment-issues": Resource(
        EquipmentIssue, Permission.EQUIPMENT_READ_ALL, "issue_date", ("ISSUED", "RETURNED")
    ),
    "expenses": Resource(
        Expense, Permission.EXPENSE_READ, "expense_date", ("DRAFT", "APPROVED"), ExpenseRead
    ),
    "financial-requests": Resource(
        FinancialRequest,
        Permission.FINANCIAL_REQUEST_CREATE,
        "period",
        ("PENDING_BOSS", "APPROVED", "REJECTED", "CANCELLED"),
        RequestRead,
    ),
    "change-requests": Resource(
        ProfileChangeRequest,
        Permission.WORKER_MANAGE,
        statuses=("PENDING", "APPROVED", "REJECTED", "CANCELLED"),
        reader=ChangeRequestRead,
    ),
    "work-authorizations": Resource(
        WorkAuthorization,
        Permission.ORGANIZATION_MANAGE,
        "valid_from",
        ("ACTIVE", "REVOKED"),
        AuthorizationRead,
    ),
}


class BrowseQuery(BaseModel):
    q: str = Field(default="", max_length=160)
    status: str | None = Field(default=None, max_length=32)
    city_id: UUID | None = None
    date_from: date | None = None
    date_to: date | None = None
    sort: Literal["date", "name", "amount", "status"] = "date"
    direction: Literal["asc", "desc"] = "desc"
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=1000000)


def resource_for(name: str, actor: CurrentUser) -> Resource:
    resource = RESOURCES.get(name)
    if resource is None:
        raise ApiError(404, "resource_not_found", "Resource was not found")
    if not has_permission(actor.role, resource.permission):
        raise ApiError(403, "permission_denied", "Permission denied")
    return resource


def base_query(name: str, resource: Resource):
    model = resource.model
    query = select(model)
    if hasattr(model, "archived_at"):
        query = query.where(model.archived_at.is_(None))
    if model is User:
        query = (
            query.join(EmployeeProfile, EmployeeProfile.user_id == User.id)
            .where(User.role == UserRole.WORKER, EmployeeProfile.archived_at.is_(None))
            .options(selectinload(User.profile))
        )
        if name == "assignments":
            query = query.where(
                User.id.in_(
                    select(WorkerAssignment.worker_user_id).where(
                        WorkerAssignment.effective_to.is_(None),
                        WorkerAssignment.archived_at.is_(None),
                    )
                )
            )
    if model in (Brigade, WorkObject):
        query = query.options(selectinload(model.city))
    if model is WorkDay:
        query = work_day_query()
    if model is WorkSchedule:
        query = schedule_query()
    if model is TariffHistory:
        query = query.options(selectinload(TariffHistory.work_object))
    return query


def columns(resource: Resource):
    model = resource.model
    worker = User.id if model is User else getattr(model, "worker_user_id", None)
    worker_name = (
        select(EmployeeProfile.full_name)
        .where(EmployeeProfile.user_id == worker)
        .correlate(model)
        .scalar_subquery()
        if worker is not None
        else None
    )
    name = EmployeeProfile.full_name if model is User else getattr(model, "name", None)
    if name is None:
        name = worker_name
    city = EmployeeProfile.city_id if model is User else getattr(model, "city_id", None)
    if city is None and hasattr(model, "object_id"):
        city = (
            select(WorkObject.city_id)
            .where(WorkObject.id == model.object_id)
            .correlate(model)
            .scalar_subquery()
        )
    if city is None and worker is not None:
        city = (
            select(EmployeeProfile.city_id)
            .where(EmployeeProfile.user_id == worker)
            .correlate(model)
            .scalar_subquery()
        )
    sorts = {
        # Sorting keys are explicit SQL expressions, never caller-supplied SQL.
        "date": getattr(model, resource.date_key),
        "name": name,
        "amount": getattr(model, "amount", None),
        "status": getattr(model, "status", None),
    }
    return worker, city, {key: value for key, value in sorts.items() if value is not None}


def filtered(query, resource: Resource, params: BrowseQuery):
    model = resource.model
    worker, city, sorts = columns(resource)
    if params.date_from and params.date_to and params.date_from > params.date_to:
        raise ApiError(422, "invalid_date_range", "Date range is invalid")
    if params.date_to == date.max:
        raise ApiError(422, "invalid_date_range", "Date is outside supported range")
    if params.sort not in sorts:
        raise ApiError(422, "invalid_sort", "Sort is not supported")
    if params.city_id:
        if city is None:
            raise ApiError(422, "invalid_filter", "City filter is not supported")
        query = query.where(city == params.city_id)
    if params.status:
        if params.status not in resource.statuses:
            raise ApiError(422, "invalid_filter", "Status is not supported")
        if model is EquipmentIssue:
            query = query.where(
                model.return_date.is_(None)
                if params.status == "ISSUED"
                else model.return_date.is_not(None)
            )
        elif model is WorkAuthorization:
            query = query.where(
                model.revoked_at.is_(None)
                if params.status == "ACTIVE"
                else model.revoked_at.is_not(None)
            )
        else:
            query = query.where(model.status == params.status)
    day = getattr(model, resource.date_key)
    timestamp = isinstance(day.type, DateTime)
    if params.date_from:
        start = (
            datetime.combine(params.date_from, datetime.min.time(), UTC)
            if timestamp
            else params.date_from
        )
        query = query.where(day >= start)
    if params.date_to:
        if timestamp:
            end = datetime.combine(params.date_to, datetime.min.time(), UTC) + timedelta(days=1)
            query = query.where(day < end)
        else:
            query = query.where(day <= params.date_to)
    if params.q.strip():
        escaped = params.q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        expressions = [cast(model.id, String).ilike(pattern, escape="\\")]
        for key in ("name", "reference", "reason", "comment", "serial_number", "category"):
            column = getattr(model, key, None)
            if column is not None:
                expressions.append(column.ilike(pattern, escape="\\"))
        if worker is not None:
            expressions.append(
                worker.in_(
                    select(EmployeeProfile.user_id).where(
                        or_(
                            EmployeeProfile.full_name.ilike(pattern, escape="\\"),
                            EmployeeProfile.employee_id.ilike(pattern, escape="\\"),
                        )
                    )
                )
            )
        if model is User:
            expressions.append(User.phone.ilike(pattern, escape="\\"))
        if hasattr(model, "object_id"):
            expressions.append(
                model.object_id.in_(
                    select(WorkObject.id).where(WorkObject.name.ilike(pattern, escape="\\"))
                )
            )
        if model is EquipmentIssue:
            expressions.append(
                model.equipment_id.in_(
                    select(EquipmentItem.id).where(EquipmentItem.name.ilike(pattern, escape="\\"))
                )
            )
        query = query.where(or_(*expressions))
    return query, sorts


async def projection(session: AsyncSession, resource: Resource, item, request: Request) -> dict:
    model = resource.model
    if model is User:
        result = await worker_projection(
            session,
            item,
            PiiCipher(request.app.state.settings.pii_encryption_key.get_secret_value()),
        )
    elif model is WorkObject:
        result = object_read(item)
    elif model is Brigade:
        result = brigade_read(item)
    elif model is WorkDay:
        result = await day_projection(session, item)
    elif model is WorkSchedule:
        result = await schedule_projection(session, item)
    elif model is ProfileChangeRequest:
        result = request_read(
            item, PiiCipher(request.app.state.settings.pii_encryption_key.get_secret_value())
        )
    elif model is AttendanceRecord:
        result = await attendance_projection(session, item)
    elif model is EquipmentIssue:
        result = await issue_read(session, item)
    elif model is TariffHistory:
        result = TariffRead(
            id=item.id,
            object_id=item.object_id,
            object_name=item.work_object.name,
            rate_per_m3=item.rate_per_m3,
            currency=item.currency,
            effective_from=item.effective_from,
            effective_to=item.effective_to,
        )
    else:
        result = resource.reader.model_validate(item, from_attributes=True)
    return result.model_dump(mode="json")


async def labels_for(session: AsyncSession, items: list[dict]) -> dict[str, str]:
    ids: set[UUID] = set()

    def collect(value):
        if isinstance(value, dict):
            for key, field in value.items():
                if key.endswith("_id") or key in ("created_by", "reviewed_by"):
                    try:
                        ids.add(UUID(str(field)))
                    except ValueError:
                        pass
                elif isinstance(field, dict):
                    collect(field)

    for item in items:
        collect(item)
    labels = {}
    if ids:
        for model, title in (
            (EmployeeProfile, EmployeeProfile.full_name),
            (City, City.name),
            (WorkObject, WorkObject.name),
            (Brigade, Brigade.name),
            (EquipmentItem, EquipmentItem.name),
        ):
            identity = model.user_id if model is EmployeeProfile else model.id
            for identity_value, text in (
                await session.execute(select(identity, title).where(identity.in_(ids)))
            ).all():
                labels[str(identity_value)] = text
    return labels


@router.get("/{name}")
async def browse(
    name: str,
    request: Request,
    session: Session,
    actor: Actor,
    params: Annotated[BrowseQuery, Query()],
):
    resource = resource_for(name, actor)
    query, sorts = filtered(base_query(name, resource), resource, params)
    total = await session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    order = sorts[params.sort]
    query = query.order_by(
        order.asc().nullslast() if params.direction == "asc" else order.desc().nullslast(),
        resource.model.id,
    )
    rows = (await session.scalars(query.limit(params.limit).offset(params.offset))).all()
    items = [await projection(session, resource, row, request) for row in rows]
    _, city, _ = columns(resource)
    return {
        "items": items,
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
        "labels": await labels_for(session, items),
        "sorts": list(sorts),
        "statuses": resource.statuses,
        "has_city": city is not None,
    }


@router.get("/{name}/{record_id}")
async def record(name: str, record_id: UUID, request: Request, session: Session, actor: Actor):
    resource = resource_for(name, actor)
    row = await session.scalar(base_query(name, resource).where(resource.model.id == record_id))
    if row is None:
        raise ApiError(404, "record_not_found", "Record was not found")
    item = await projection(session, resource, row, request)
    return {"item": item, "labels": await labels_for(session, [item])}
