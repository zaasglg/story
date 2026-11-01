import calendar
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit.service import add_audit_event
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.finance.models import Expense
from backend.identity.dependencies import CurrentUser, get_db_session
from backend.workforce.models import WorkObject
from backend.workforce.service import require_city

router = APIRouter(tags=["expenses"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
Reader = Annotated[CurrentUser, Depends(require_permission(Permission.EXPENSE_READ))]
Manager = Annotated[CurrentUser, Depends(require_permission(Permission.EXPENSE_MANAGE))]
Category = Literal[
    "salary",
    "bonus",
    "material",
    "transport",
    "food",
    "accommodation",
    "equipment",
    "fuel",
    "other",
]


class ExpenseWrite(BaseModel):
    recipient: str | None = Field(default=None, max_length=255)

    model_config = ConfigDict(extra="forbid")
    change_reason: str | None = Field(default=None, min_length=3, max_length=2000, exclude=True)
    object_id: UUID | None = None
    city_id: UUID
    expense_date: date
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: str = Field(default="RUB", pattern=r"^[A-Z]{3}$")
    category: Category
    comment: str | None = Field(default=None, max_length=2000)
    receipt_url: str | None = Field(default=None, max_length=2048)


class ExpenseRead(ExpenseWrite):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: str
    amount: Decimal
    payroll_entry_id: UUID | None = None
    revision: int


class ExpenseTotal(BaseModel):
    currency: str
    total: Decimal
    count: int


class ExpenseFilters:
    def __init__(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        city_id: UUID | None = None,
        object_id: UUID | None = None,
        category: Category | None = None,
    ):
        if date_from and date_to and date_to < date_from:
            raise ApiError(422, "invalid_date_range", "End date must follow start date")
        self.date_from, self.date_to = date_from, date_to
        self.city_id, self.object_id, self.category = city_id, object_id, category

    def apply(self, query):
        query = query.where(Expense.archived_at.is_(None))
        for value, condition in (
            (self.date_from, Expense.expense_date >= self.date_from if self.date_from else None),
            (self.date_to, Expense.expense_date <= self.date_to if self.date_to else None),
            (self.city_id, Expense.city_id == self.city_id),
            (self.object_id, Expense.object_id == self.object_id),
            (self.category, Expense.category == self.category),
        ):
            if value is not None:
                query = query.where(condition)
        return query


Filters = Annotated[ExpenseFilters, Depends()]


async def validate_expense(session: AsyncSession, payload: ExpenseWrite) -> None:
    await require_city(session, payload.city_id)
    if payload.object_id:
        obj = await session.get(WorkObject, payload.object_id)
        if obj is None or obj.archived_at is not None or obj.city_id != payload.city_id:
            raise ApiError(
                422, "invalid_expense_object", "Object must be active and in the selected city"
            )


def audit(
    session: AsyncSession, request: Request, actor: CurrentUser, action: str, item: Expense
) -> None:
    add_audit_event(
        session,
        actor_user_id=actor.id,
        action=action,
        entity_type="expense",
        entity_id=item.id,
        request_id=getattr(request.state, "request_id", None),
        new_value={
            "amount": str(item.amount),
            "currency": item.currency,
            "category": item.category,
            "date": item.expense_date.isoformat(),
        },
    )


@router.get("/admin/expenses", response_model=list[ExpenseRead])
@router.get("/boss/expenses", response_model=list[ExpenseRead])
async def list_expenses(
    session: Session,
    _: Reader,
    filters: Filters,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Expense]:
    return list(
        (
            await session.scalars(
                filters.apply(select(Expense))
                .order_by(Expense.expense_date.desc(), Expense.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )


async def totals(session: AsyncSession, filters: ExpenseFilters) -> list[ExpenseTotal]:
    rows = (
        await session.execute(
            filters.apply(
                select(Expense.currency, func.sum(Expense.amount), func.count(Expense.id))
            )
            .group_by(Expense.currency)
            .order_by(Expense.currency)
        )
    ).all()
    return [ExpenseTotal(currency=row[0], total=row[1], count=row[2]) for row in rows]


@router.get("/admin/expenses/daily", response_model=list[ExpenseTotal])
@router.get("/boss/expenses/daily", response_model=list[ExpenseTotal])
async def daily_report(
    day: date, session: Session, _: Reader, filters: Filters
) -> list[ExpenseTotal]:
    filters.date_from = filters.date_to = day
    return await totals(session, filters)


@router.get("/admin/expenses/monthly", response_model=list[ExpenseTotal])
@router.get("/boss/expenses/monthly", response_model=list[ExpenseTotal])
async def monthly_report(
    year: Annotated[int, Query(ge=2000, le=2100)],
    month: Annotated[int, Query(ge=1, le=12)],
    session: Session,
    _: Reader,
    filters: Filters,
) -> list[ExpenseTotal]:
    filters.date_from = date(year, month, 1)
    filters.date_to = date(year, month, calendar.monthrange(year, month)[1])
    return await totals(session, filters)


@router.get("/admin/expenses/object/{object_id}", response_model=list[ExpenseTotal])
@router.get("/boss/expenses/object/{object_id}", response_model=list[ExpenseTotal])
async def object_report(
    object_id: UUID, session: Session, _: Reader, filters: Filters
) -> list[ExpenseTotal]:
    if await session.get(WorkObject, object_id) is None:
        raise ApiError(404, "object_not_found", "Object was not found")
    filters.object_id = object_id
    return await totals(session, filters)


@router.post("/admin/expenses", response_model=ExpenseRead, status_code=201)
@router.post("/boss/expenses", response_model=ExpenseRead, status_code=201)
async def create_expense(
    payload: ExpenseWrite, request: Request, session: Session, actor: Manager
) -> Expense:
    await validate_expense(session, payload)
    now = utc_now()
    item = Expense(
        **payload.model_dump(),
        created_by=actor.id,
        updated_by=actor.id,
        created_at=now,
        updated_at=now,
    )
    session.add(item)
    await session.flush()
    audit(session, request, actor, "EXPENSE_CREATED", item)
    await session.commit()
    return item


@router.get("/admin/expenses/{expense_id}", response_model=ExpenseRead)
@router.get("/boss/expenses/{expense_id}", response_model=ExpenseRead)
async def get_expense(expense_id: UUID, session: Session, _: Reader) -> Expense:
    item = await session.get(Expense, expense_id)
    if item is None or item.archived_at is not None:
        raise ApiError(404, "expense_not_found", "Expense was not found")
    return item


@router.put("/admin/expenses/{expense_id}", response_model=ExpenseRead)
@router.put("/boss/expenses/{expense_id}", response_model=ExpenseRead)
async def update_expense(
    expense_id: UUID, payload: ExpenseWrite, request: Request, session: Session, actor: Manager
) -> Expense:
    item = await session.scalar(select(Expense).where(Expense.id == expense_id).with_for_update())
    if item is None or item.archived_at is not None:
        raise ApiError(404, "expense_not_found", "Expense was not found")
    if item.payroll_entry_id is not None:
        raise ApiError(
            409,
            "payroll_expense_managed",
            "Correct the payroll entry instead of its linked expense",
        )
    await validate_expense(session, payload)
    # Always write the status, including when the loaded snapshot was already
    # DRAFT. ORM dirty tracking would otherwise omit it after a concurrent approval.
    session.info["audit_reason"] = payload.change_reason
    for field, value in payload.model_dump().items():
        setattr(item, field, value)
    item.updated_by, item.updated_at, item.status = actor.id, utc_now(), "DRAFT"
    item.revision += 1
    # The row lock serializes approval and edits. ORM snapshots retain every changed field.
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(item, "status")
    audit(session, request, actor, "EXPENSE_UPDATED", item)
    await session.commit()
    await session.refresh(item)
    return item


@router.delete("/admin/expenses/{expense_id}", status_code=204)
@router.delete("/boss/expenses/{expense_id}", status_code=204)
async def archive_expense(
    expense_id: UUID, request: Request, session: Session, actor: Manager
) -> Response:
    item = await session.get(Expense, expense_id, with_for_update=True)
    if item is None or item.archived_at is not None:
        raise ApiError(404, "expense_not_found", "Expense was not found")
    if item.payroll_entry_id is not None:
        raise ApiError(
            409,
            "payroll_expense_managed",
            "Correct the payroll entry instead of its linked expense",
        )
    item.archived_at = item.updated_at = utc_now()
    item.updated_by = actor.id
    item.revision += 1
    audit(session, request, actor, "EXPENSE_ARCHIVED", item)
    await session.commit()
    return Response(status_code=204)
