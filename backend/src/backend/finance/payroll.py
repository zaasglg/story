from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit.service import add_audit_event
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.finance.models import FinancialRequest, LedgerCorrection, LedgerEntry
from backend.identity.dependencies import CurrentUser, get_db_session
from backend.operations.models import WorkDay, WorkDayStatus
from backend.workforce.service import require_worker

router = APIRouter(tags=["payroll"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
Creator = Annotated[CurrentUser, Depends(require_permission(Permission.FINANCIAL_REQUEST_CREATE))]
Approver = Annotated[CurrentUser, Depends(require_permission(Permission.FINANCIAL_REQUEST_APPROVE))]


async def calculated_salary(
    session: AsyncSession, worker_id: UUID, period: date, currency: str
) -> Decimal:
    end = date(period.year + (period.month == 12), period.month % 12 + 1, 1)
    value = await session.scalar(
        select(func.sum(WorkDay.daily_earnings)).where(
            WorkDay.worker_user_id == worker_id,
            WorkDay.work_date >= period,
            WorkDay.work_date < end,
            WorkDay.currency == currency,
            WorkDay.status == WorkDayStatus.APPROVED,
            WorkDay.archived_at.is_(None),
        )
    )
    return value or Decimal("0.00")


class RequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_user_id: UUID
    type: Literal["SALARY", "BONUS", "PENALTY", "ADVANCE", "PAYMENT"]
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: str = Field(default="RUB", pattern=r"^[A-Z]{3}$")
    period: date
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def month_start(self):
        if self.period.day != 1:
            raise ValueError("Period must be the first day of the payroll month")
        return self


class RequestRead(RequestCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: str
    created_by: UUID
    reviewed_by: UUID | None


@router.post("/admin/financial-requests", response_model=RequestRead, status_code=201)
async def create_request(
    payload: RequestCreate,
    session: Session,
    actor: Creator,
    request: Request,
    key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> FinancialRequest:
    existing = await session.scalar(
        select(FinancialRequest).where(
            FinancialRequest.created_by == actor.id, FinancialRequest.idempotency_key == key
        )
    )
    if existing:
        if any(getattr(existing, field) != value for field, value in payload.model_dump().items()):
            raise ApiError(409, "idempotency_conflict", "Key was already used for another request")
        return existing
    await require_worker(session, payload.worker_user_id)
    if payload.type == "SALARY":
        expected = await calculated_salary(
            session, payload.worker_user_id, payload.period, payload.currency
        )
        if payload.amount != expected:
            raise ApiError(
                422,
                "salary_calculation_mismatch",
                "Salary must match approved work-day earnings for this month",
            )
    item = FinancialRequest(
        **payload.model_dump(),
        created_by=actor.id,
        idempotency_key=key,
        status="PENDING_BOSS",
        created_at=utc_now(),
    )
    session.add(item)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(
            409, "request_conflict", "Request already exists; retry with the same key"
        ) from exc
    add_audit_event(
        session,
        actor_user_id=actor.id,
        action="FINANCIAL_REQUEST_CREATED",
        entity_type="financial_request",
        entity_id=item.id,
        new_value={**payload.model_dump(mode="json"), "status": item.status},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return item


@router.get("/admin/financial-requests", response_model=list[RequestRead])
async def admin_requests(session: Session, _: Creator) -> list[FinancialRequest]:
    return list(
        (
            await session.scalars(
                select(FinancialRequest).order_by(FinancialRequest.created_at.desc())
            )
        ).all()
    )


@router.get("/boss/pending-approvals", response_model=list[RequestRead])
async def pending_requests(session: Session, _: Approver) -> list[FinancialRequest]:
    return list(
        (
            await session.scalars(
                select(FinancialRequest)
                .where(FinancialRequest.status == "PENDING_BOSS")
                .order_by(FinancialRequest.created_at)
            )
        ).all()
    )


async def decide(
    item_id: UUID, session: AsyncSession, actor: CurrentUser, request: Request, approve: bool
) -> FinancialRequest:
    item = await session.scalar(
        select(FinancialRequest).where(FinancialRequest.id == item_id).with_for_update()
    )
    if item is None:
        raise ApiError(404, "request_not_found", "Financial request was not found")
    desired = "APPROVED" if approve else "REJECTED"
    if item.status == desired:
        return item
    if item.status != "PENDING_BOSS":
        raise ApiError(409, "request_decided", "Request has already been decided")
    if approve and item.type == "SALARY":
        current_amount = await calculated_salary(
            session, item.worker_user_id, item.period, item.currency
        )
        if current_amount != item.amount:
            raise ApiError(
                409,
                "salary_changed",
                "Work-day earnings changed; cancel and create a new salary request",
            )
    result = await session.execute(
        update(FinancialRequest)
        .where(FinancialRequest.id == item.id, FinancialRequest.status == "PENDING_BOSS")
        .values(status=desired, reviewed_by=actor.id, reviewed_at=utc_now())
        .returning(FinancialRequest.id)
    )
    if result.scalar_one_or_none() is None:
        raise ApiError(409, "request_decided", "Request has already been decided")
    if approve:
        session.add(
            LedgerEntry(
                request_id=item.id,
                worker_user_id=item.worker_user_id,
                type=item.type,
                amount=item.amount,
                currency=item.currency,
                period=item.period,
                created_at=utc_now(),
            )
        )
    add_audit_event(
        session,
        actor_user_id=actor.id,
        action=f"FINANCIAL_REQUEST_{desired}",
        entity_type="financial_request",
        entity_id=item.id,
        old_value={"status": "PENDING_BOSS"},
        new_value={"status": desired},
        request_id=getattr(request.state, "request_id", None),
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(
            409, "ledger_conflict", "Salary for this period has already been posted"
        ) from exc
    await session.refresh(item)
    return item


@router.post("/boss/approvals/{item_id}/approve", response_model=RequestRead)
async def approve(
    item_id: UUID, session: Session, actor: Approver, request: Request
) -> FinancialRequest:
    return await decide(item_id, session, actor, request, True)


@router.post("/boss/approvals/{item_id}/reject", response_model=RequestRead)
async def reject(
    item_id: UUID, session: Session, actor: Approver, request: Request
) -> FinancialRequest:
    return await decide(item_id, session, actor, request, False)


@router.post("/admin/financial-requests/{item_id}/cancel", response_model=RequestRead)
async def cancel_request(
    item_id: UUID, session: Session, actor: Creator, request: Request
) -> FinancialRequest:
    item = await session.scalar(
        select(FinancialRequest).where(FinancialRequest.id == item_id).with_for_update()
    )
    if item is None:
        raise ApiError(404, "request_not_found", "Financial request was not found")
    if item.status != "PENDING_BOSS":
        raise ApiError(409, "request_decided", "Only pending requests can be cancelled")
    result = await session.execute(
        update(FinancialRequest)
        .where(FinancialRequest.id == item_id, FinancialRequest.status == "PENDING_BOSS")
        .values(status="CANCELLED", reviewed_by=actor.id, reviewed_at=utc_now())
        .returning(FinancialRequest.id)
    )
    if result.scalar_one_or_none() is None:
        raise ApiError(409, "request_decided", "Request has already been decided")
    add_audit_event(
        session,
        actor_user_id=actor.id,
        action="FINANCIAL_REQUEST_CANCELLED",
        entity_type="financial_request",
        entity_id=item_id,
        old_value={"status": "PENDING_BOSS"},
        new_value={"status": "CANCELLED"},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(item)
    return item


class PayrollRead(BaseModel):
    worker_user_id: UUID
    period: date
    currency: str
    salary: Decimal
    bonus: Decimal
    penalty: Decimal
    advance: Decimal
    payment: Decimal
    monthly_pay: Decimal
    remaining: Decimal


class SalaryCalculation(BaseModel):
    worker_user_id: UUID
    period: date
    currency: str
    proposed_salary: Decimal


@router.get("/admin/payroll/calculation", response_model=SalaryCalculation)
async def salary_calculation(
    worker_user_id: UUID,
    period: date,
    session: Session,
    _: Creator,
    currency: Annotated[str, Query(pattern=r"^[A-Z]{3}$")] = "RUB",
) -> SalaryCalculation:
    if period.day != 1:
        raise ApiError(422, "invalid_period", "Period must start on the first day of the month")
    await require_worker(session, worker_user_id)
    return SalaryCalculation(
        worker_user_id=worker_user_id,
        period=period,
        currency=currency,
        proposed_salary=await calculated_salary(session, worker_user_id, period, currency),
    )


async def payroll(
    session: AsyncSession, period: date, worker_id: UUID | None = None
) -> list[PayrollRead]:
    query = select(LedgerEntry).where(LedgerEntry.period == period)
    if worker_id:
        query = query.where(LedgerEntry.worker_user_id == worker_id)
    rows = (await session.scalars(query)).all()
    corrections = (
        (
            await session.scalars(
                select(LedgerCorrection)
                .where(LedgerCorrection.ledger_entry_id.in_([row.id for row in rows]))
                .order_by(LedgerCorrection.revision)
            )
        ).all()
        if rows
        else []
    )
    corrected_amounts = {item.ledger_entry_id: item.new_amount for item in corrections}
    grouped = {}
    for row in rows:
        values = grouped.setdefault(
            (row.worker_user_id, row.currency),
            dict.fromkeys(("SALARY", "BONUS", "PENALTY", "ADVANCE", "PAYMENT"), Decimal("0.00")),
        )
        values[row.type] += corrected_amounts.get(row.id, row.amount)
    result = []
    for (worker, currency), values in grouped.items():
        total = values["SALARY"] + values["BONUS"] - values["PENALTY"]
        result.append(
            PayrollRead(
                worker_user_id=worker,
                period=period,
                currency=currency,
                salary=values["SALARY"],
                bonus=values["BONUS"],
                penalty=values["PENALTY"],
                advance=values["ADVANCE"],
                payment=values["PAYMENT"],
                monthly_pay=total,
                remaining=total - values["ADVANCE"] - values["PAYMENT"],
            )
        )
    return result


@router.get("/admin/payroll", response_model=list[PayrollRead])
async def admin_payroll(period: date, session: Session, _: Creator) -> list[PayrollRead]:
    return await payroll(session, period)


@router.get("/boss/payroll", response_model=list[PayrollRead])
async def boss_payroll(period: date, session: Session, _: Approver) -> list[PayrollRead]:
    return await payroll(session, period)


@router.get("/workers/me/finance", response_model=list[PayrollRead])
async def own_payroll(
    period: date,
    session: Session,
    actor: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_READ_OWN))],
) -> list[PayrollRead]:
    return await payroll(session, period, actor.id)


class CorrectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    new_amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    reason: str = Field(min_length=1, max_length=2000)


class CorrectionRead(CorrectionCreate):
    id: UUID
    ledger_entry_id: UUID
    revision: int


def correction_read(item: LedgerCorrection) -> CorrectionRead:
    return CorrectionRead(
        id=item.id,
        ledger_entry_id=item.ledger_entry_id,
        revision=item.revision,
        expected_amount=item.old_amount,
        new_amount=item.new_amount,
        reason=item.reason,
    )


@router.post("/boss/ledger/{entry_id}/corrections", response_model=CorrectionRead, status_code=201)
async def correct_entry(
    entry_id: UUID,
    payload: CorrectionCreate,
    session: Session,
    actor: Approver,
    request: Request,
    key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> CorrectionRead:
    replay = await session.scalar(
        select(LedgerCorrection).where(
            LedgerCorrection.actor_user_id == actor.id, LedgerCorrection.idempotency_key == key
        )
    )
    if replay:
        if (
            replay.ledger_entry_id != entry_id
            or replay.old_amount != payload.expected_amount
            or replay.new_amount != payload.new_amount
            or replay.reason != payload.reason
        ):
            raise ApiError(
                409, "idempotency_conflict", "Key was already used for another correction"
            )
        return correction_read(replay)
    entry = await session.scalar(
        select(LedgerEntry).where(LedgerEntry.id == entry_id).with_for_update()
    )
    if entry is None:
        raise ApiError(404, "ledger_entry_not_found", "Ledger entry was not found")
    last = await session.scalar(
        select(LedgerCorrection)
        .where(LedgerCorrection.ledger_entry_id == entry_id)
        .order_by(LedgerCorrection.revision.desc())
        .limit(1)
    )
    current_amount = last.new_amount if last else entry.amount
    if payload.expected_amount != current_amount:
        raise ApiError(409, "stale_correction", "Amount changed; reload before correcting")
    if payload.new_amount == current_amount:
        raise ApiError(422, "unchanged_amount", "Correction must change the amount")
    item = LedgerCorrection(
        ledger_entry_id=entry_id,
        revision=last.revision + 1 if last else 1,
        old_amount=current_amount,
        new_amount=payload.new_amount,
        reason=payload.reason,
        actor_user_id=actor.id,
        idempotency_key=key,
        created_at=utc_now(),
    )
    session.add(item)
    try:
        await session.flush()
        add_audit_event(
            session,
            actor_user_id=actor.id,
            action="PAYROLL_CORRECTED",
            entity_type="ledger_correction",
            entity_id=item.id,
            old_value={"amount": str(current_amount)},
            new_value={
                "amount": str(payload.new_amount),
                "reason": payload.reason,
                "ledger_entry_id": str(entry_id),
            },
            request_id=getattr(request.state, "request_id", None),
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(
            409, "correction_conflict", "Another correction was posted; reload and retry"
        ) from exc
    return correction_read(item)


@router.get("/boss/ledger/{entry_id}/corrections", response_model=list[CorrectionRead])
async def correction_history(entry_id: UUID, session: Session, _: Approver) -> list[CorrectionRead]:
    if await session.get(LedgerEntry, entry_id) is None:
        raise ApiError(404, "ledger_entry_not_found", "Ledger entry was not found")
    rows = (
        await session.scalars(
            select(LedgerCorrection)
            .where(LedgerCorrection.ledger_entry_id == entry_id)
            .order_by(LedgerCorrection.revision)
        )
    ).all()
    return [correction_read(row) for row in rows]


class LedgerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    request_id: UUID
    worker_user_id: UUID
    type: str
    amount: Decimal
    currency: str
    period: date


@router.get("/boss/ledger", response_model=list[LedgerRead])
async def ledger_list(period: date, session: Session, _: Approver) -> list[LedgerEntry]:
    return list(
        (
            await session.scalars(
                select(LedgerEntry)
                .where(LedgerEntry.period == period)
                .order_by(LedgerEntry.created_at)
            )
        ).all()
    )
