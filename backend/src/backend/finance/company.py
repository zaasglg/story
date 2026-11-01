from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit.service import add_audit_event
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.config import Settings
from backend.core.crypto import PiiCipher
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.finance.models import CompanyBankAccount, Expense, Revenue
from backend.identity.dependencies import CurrentUser, get_app_settings, get_db_session
from backend.workforce.models import WorkObject
from backend.workforce.service import require_city

router = APIRouter(tags=["boss-finance"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
RevenueManager = Annotated[CurrentUser, Depends(require_permission(Permission.REVENUE_MANAGE))]
RevenueReader = Annotated[CurrentUser, Depends(require_permission(Permission.REVENUE_READ))]
ProfitReader = Annotated[CurrentUser, Depends(require_permission(Permission.PROFIT_READ))]
ExpenseApprover = Annotated[CurrentUser, Depends(require_permission(Permission.EXPENSE_APPROVE))]
BankManager = Annotated[CurrentUser, Depends(require_permission(Permission.COMPANY_BANK_MANAGE))]
BankReader = Annotated[CurrentUser, Depends(require_permission(Permission.COMPANY_BANK_READ))]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


class RevenueWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object_id: UUID | None = None
    city_id: UUID
    revenue_date: date
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: str = Field(default="RUB", pattern=r"^[A-Z]{3}$")
    category: str = Field(min_length=1, max_length=128)
    comment: str | None = Field(default=None, max_length=2000)
    attachment_url: str | None = Field(default=None, max_length=2048)


class RevenueRead(RevenueWrite):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: str


def audit(
    session: AsyncSession,
    request: Request,
    actor: CurrentUser,
    action: str,
    entity: str,
    entity_id: UUID,
    old_value: dict | None = None,
    new_value: dict | None = None,
) -> None:
    add_audit_event(
        session,
        actor_user_id=actor.id,
        action=action,
        entity_type=entity,
        entity_id=entity_id,
        old_value=old_value,
        new_value=new_value,
        request_id=getattr(request.state, "request_id", None),
    )


async def validate_context(session: AsyncSession, city_id: UUID, object_id: UUID | None) -> None:
    await require_city(session, city_id)
    if object_id:
        obj = await session.get(WorkObject, object_id)
        if obj is None or obj.archived_at is not None or obj.city_id != city_id:
            raise ApiError(422, "invalid_object", "Object must be active and in the selected city")


@router.get("/boss/revenues", response_model=list[RevenueRead])
async def revenues(session: Session, _: RevenueReader) -> list[Revenue]:
    return list(
        (
            await session.scalars(
                select(Revenue)
                .where(Revenue.archived_at.is_(None))
                .order_by(Revenue.revenue_date.desc())
            )
        ).all()
    )


@router.post("/boss/revenues", response_model=RevenueRead, status_code=201)
async def create_revenue(
    payload: RevenueWrite, session: Session, actor: RevenueManager, request: Request
) -> Revenue:
    await validate_context(session, payload.city_id, payload.object_id)
    now = utc_now()
    item = Revenue(
        **payload.model_dump(),
        status="DRAFT",
        created_by=actor.id,
        updated_by=actor.id,
        created_at=now,
        updated_at=now,
    )
    session.add(item)
    await session.flush()
    audit(
        session,
        request,
        actor,
        "REVENUE_CREATED",
        "revenue",
        item.id,
        new_value=payload.model_dump(mode="json"),
    )
    await session.commit()
    return item


@router.put("/boss/revenues/{item_id}", response_model=RevenueRead)
async def update_revenue(
    item_id: UUID, payload: RevenueWrite, session: Session, actor: RevenueManager, request: Request
) -> Revenue:
    item = await session.scalar(select(Revenue).where(Revenue.id == item_id).with_for_update())
    if item is None or item.archived_at is not None:
        raise ApiError(404, "revenue_not_found", "Revenue was not found")
    await validate_context(session, payload.city_id, payload.object_id)
    old = RevenueRead.model_validate(item).model_dump(mode="json")
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    item.status, item.updated_by, item.updated_at = "DRAFT", actor.id, utc_now()
    audit(
        session,
        request,
        actor,
        "REVENUE_UPDATED",
        "revenue",
        item.id,
        old,
        payload.model_dump(mode="json"),
    )
    await session.commit()
    return item


@router.post("/boss/revenues/{item_id}/approve", response_model=RevenueRead)
async def approve_revenue(
    item_id: UUID, session: Session, actor: RevenueManager, request: Request
) -> Revenue:
    item = await session.scalar(select(Revenue).where(Revenue.id == item_id).with_for_update())
    if item is None or item.archived_at is not None:
        raise ApiError(404, "revenue_not_found", "Revenue was not found")
    if item.status != "APPROVED":
        item.status, item.updated_by, item.updated_at = "APPROVED", actor.id, utc_now()
        audit(
            session,
            request,
            actor,
            "REVENUE_APPROVED",
            "revenue",
            item.id,
            {"status": "DRAFT"},
            {"status": "APPROVED"},
        )
        await session.commit()
    return item


@router.delete("/boss/revenues/{item_id}", status_code=204)
async def archive_revenue(
    item_id: UUID, session: Session, actor: RevenueManager, request: Request
) -> Response:
    item = await session.scalar(select(Revenue).where(Revenue.id == item_id).with_for_update())
    if item is None or item.archived_at is not None:
        raise ApiError(404, "revenue_not_found", "Revenue was not found")
    item.archived_at = item.updated_at = utc_now()
    item.updated_by = actor.id
    audit(session, request, actor, "REVENUE_ARCHIVED", "revenue", item.id)
    await session.commit()
    return Response(status_code=204)


class ExpenseApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    reason: str = Field(default="Подтверждение расхода", min_length=3, max_length=2000)


@router.post("/boss/expenses/{item_id}/approve")
async def approve_expense(
    item_id: UUID,
    payload: ExpenseApproval,
    session: Session,
    actor: ExpenseApprover,
    request: Request,
) -> dict[str, str]:
    item = await session.scalar(select(Expense).where(Expense.id == item_id).with_for_update())
    if item is None or item.archived_at is not None:
        raise ApiError(404, "expense_not_found", "Expense was not found")
    if payload.expected_revision != item.revision:
        if item.status == "APPROVED" and payload.expected_revision == item.revision - 1:
            return {"status": item.status}
        raise ApiError(
            409, "stale_expense", "Expense changed; load the latest record before approval"
        )
    if item.status != "APPROVED":
        item.revision += 1
        item.status, item.updated_by, item.updated_at = "APPROVED", actor.id, utc_now()
        audit(
            session,
            request,
            actor,
            "EXPENSE_APPROVED",
            "expense",
            item.id,
            {"status": "DRAFT"},
            {"status": "APPROVED", "reason": payload.reason, "revision": item.revision},
        )
        await session.commit()
    return {"status": item.status}


class ExpenseCategoryTotal(BaseModel):
    category: str
    amount: Decimal


class FinanceTotal(BaseModel):
    currency: str
    approved_revenue: Decimal
    approved_expenses: Decimal
    net_profit: Decimal
    expense_breakdown: list[ExpenseCategoryTotal] = Field(default_factory=list)


async def finance_totals(
    session: AsyncSession,
    date_from: date | None,
    date_to: date | None,
    city_id: UUID | None,
    object_id: UUID | None,
) -> list[FinanceTotal]:
    if date_from and date_to and date_to < date_from:
        raise ApiError(422, "invalid_date_range", "End date must follow start date")
    totals = []
    breakdown: dict[str, list[ExpenseCategoryTotal]] = {}
    for model, date_field in ((Revenue, Revenue.revenue_date), (Expense, Expense.expense_date)):
        query = select(model.currency, func.sum(model.amount)).where(
            model.archived_at.is_(None), model.status == "APPROVED"
        )
        if date_from:
            query = query.where(date_field >= date_from)
        if date_to:
            query = query.where(date_field <= date_to)
        if city_id:
            query = query.where(model.city_id == city_id)
        if object_id:
            query = query.where(model.object_id == object_id)
        if model is Expense:
            grouped = (
                query.add_columns(Expense.category)
                .group_by(Expense.currency, Expense.category)
                .order_by(Expense.category)
            )
            expense_totals: dict[str, Decimal] = {}
            for currency, amount, category in (await session.execute(grouped)).all():
                expense_totals[currency] = expense_totals.get(currency, Decimal("0.00")) + amount
                breakdown.setdefault(currency, []).append(
                    ExpenseCategoryTotal(category=category, amount=amount)
                )
            totals.append(expense_totals)
        else:
            totals.append(dict((await session.execute(query.group_by(model.currency))).all()))
    return [
        FinanceTotal(
            currency=currency,
            expense_breakdown=breakdown.get(currency, []),
            approved_revenue=totals[0].get(currency, Decimal("0.00")),
            approved_expenses=totals[1].get(currency, Decimal("0.00")),
            net_profit=totals[0].get(currency, Decimal("0.00"))
            - totals[1].get(currency, Decimal("0.00")),
        )
        for currency in sorted(totals[0].keys() | totals[1].keys())
    ]


@router.get("/boss/finance", response_model=list[FinanceTotal])
@router.get("/boss/profit", response_model=list[FinanceTotal])
async def finance(
    session: Session,
    _: ProfitReader,
    date_from: date | None = None,
    date_to: date | None = None,
    city_id: UUID | None = None,
    object_id: UUID | None = None,
) -> list[FinanceTotal]:
    return await finance_totals(session, date_from, date_to, city_id, object_id)


@router.get("/boss/finance/object/{object_id}", response_model=list[FinanceTotal])
async def object_finance(
    object_id: UUID,
    session: Session,
    _: ProfitReader,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[FinanceTotal]:
    if await session.get(WorkObject, object_id) is None:
        raise ApiError(404, "object_not_found", "Object was not found")
    return await finance_totals(session, date_from, date_to, None, object_id)


@router.get("/boss/finance/object/{object_id}/months", response_model=list[date])
async def object_finance_months(
    object_id: UUID,
    session: Session,
    _: ProfitReader,
) -> list[date]:
    if await session.get(WorkObject, object_id) is None:
        raise ApiError(404, "object_not_found", "Object was not found")
    months = set()
    for model, day in ((Revenue, Revenue.revenue_date), (Expense, Expense.expense_date)):
        rows = (
            await session.execute(
                select(func.extract("year", day), func.extract("month", day))
                .where(
                    model.object_id == object_id,
                    model.status == "APPROVED",
                    model.archived_at.is_(None),
                )
                .distinct()
            )
        ).all()
        months.update(date(int(year), int(month), 1) for year, month in rows)
    return sorted(months, reverse=True)


class ObjectFinanceOperation(BaseModel):
    id: UUID
    date: date
    kind: str
    category: str
    comment: str | None
    amount: Decimal
    currency: str


@router.get(
    "/boss/finance/object/{object_id}/operations", response_model=list[ObjectFinanceOperation]
)
async def object_finance_operations(
    object_id: UUID,
    session: Session,
    _: ProfitReader,
    date_from: date,
    date_to: date,
) -> list[ObjectFinanceOperation]:
    if date_to < date_from:
        raise ApiError(422, "invalid_date_range", "End date must follow start date")
    if await session.get(WorkObject, object_id) is None:
        raise ApiError(404, "object_not_found", "Object was not found")
    operations = []
    for model, day, kind in (
        (Revenue, Revenue.revenue_date, "income"),
        (Expense, Expense.expense_date, "expense"),
    ):
        items = await session.scalars(
            select(model).where(
                model.object_id == object_id,
                model.archived_at.is_(None),
                model.status == "APPROVED",
                day.between(date_from, date_to),
            )
        )
        for item in items:
            operations.append(
                ObjectFinanceOperation(
                    id=item.id,
                    date=getattr(item, day.key),
                    kind=kind,
                    category=item.category,
                    comment=item.comment,
                    amount=item.amount,
                    currency=item.currency,
                )
            )
    return sorted(operations, key=lambda row: (row.date, row.kind, str(row.id)), reverse=True)


class BankWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    iban: str = Field(min_length=15, max_length=34, pattern=r"^[A-Z]{2}[0-9]{2}[A-Z0-9]+$")

    @field_validator("iban")
    @classmethod
    def validate_checksum(cls, value: str) -> str:
        rearranged = value[4:] + value[:4]
        numeric = "".join(str(ord(char) - 55) if char.isalpha() else char for char in rearranged)
        if int(numeric) % 97 != 1:
            raise ValueError("Invalid IBAN checksum")
        return value


class BankRead(BaseModel):
    id: UUID
    name: str
    iban: str


@router.get("/boss/bank-accounts", response_model=list[BankRead])
async def bank_accounts(session: Session, _: BankReader, settings: SettingsDep) -> list[BankRead]:
    cipher = PiiCipher(settings.pii_encryption_key.get_secret_value())
    rows = (
        await session.scalars(
            select(CompanyBankAccount).where(CompanyBankAccount.archived_at.is_(None))
        )
    ).all()
    return [
        BankRead(id=row.id, name=row.name, iban=cipher.decrypt(row.iban_encrypted)) for row in rows
    ]


@router.post("/boss/bank-accounts", response_model=BankRead, status_code=201)
async def create_bank(
    payload: BankWrite,
    session: Session,
    actor: BankManager,
    settings: SettingsDep,
    request: Request,
) -> BankRead:
    item = CompanyBankAccount(
        name=payload.name,
        iban_encrypted=PiiCipher(settings.pii_encryption_key.get_secret_value()).encrypt(
            payload.iban
        ),
        created_at=utc_now(),
    )
    session.add(item)
    await session.flush()
    audit(
        session,
        request,
        actor,
        "COMPANY_BANK_CREATED",
        "company_bank",
        item.id,
        new_value={"name": payload.name},
    )
    await session.commit()
    return BankRead(id=item.id, name=item.name, iban=payload.iban)


@router.delete("/boss/bank-accounts/{item_id}", status_code=204)
async def archive_bank(
    item_id: UUID, session: Session, actor: BankManager, request: Request
) -> Response:
    item = await session.get(CompanyBankAccount, item_id)
    if item is None or item.archived_at is not None:
        raise ApiError(404, "bank_not_found", "Bank account was not found")
    item.archived_at = utc_now()
    audit(session, request, actor, "COMPANY_BANK_ARCHIVED", "company_bank", item.id)
    await session.commit()
    return Response(status_code=204)


@router.put("/boss/bank-accounts/{item_id}", response_model=BankRead)
async def update_bank(
    item_id: UUID,
    payload: BankWrite,
    session: Session,
    actor: BankManager,
    settings: SettingsDep,
    request: Request,
) -> BankRead:
    item = await session.get(CompanyBankAccount, item_id)
    if item is None or item.archived_at is not None:
        raise ApiError(404, "bank_not_found", "Bank account was not found")
    old_name = item.name
    item.name = payload.name
    item.iban_encrypted = PiiCipher(settings.pii_encryption_key.get_secret_value()).encrypt(
        payload.iban
    )
    audit(
        session,
        request,
        actor,
        "COMPANY_BANK_UPDATED",
        "company_bank",
        item.id,
        {"name": old_name},
        {"name": payload.name},
    )
    await session.commit()
    return BankRead(id=item.id, name=item.name, iban=payload.iban)
