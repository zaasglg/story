from datetime import date, datetime
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
from backend.communications.models import Notification
from backend.communications.push import stage_notifications
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.finance.models import FinancialRequest, LedgerCorrection, LedgerEntry
from backend.finance.payroll_costs import sync_payroll_cost
from backend.identity.dependencies import CurrentUser, get_db_session
from backend.identity.models import AccountStatus, EmployeeProfile, User, UserRole
from backend.operations.models import WorkDay, WorkDayStatus
from backend.workforce.service import require_worker

router = APIRouter(tags=["payroll"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
Creator = Annotated[CurrentUser, Depends(require_permission(Permission.FINANCIAL_REQUEST_CREATE))]
Approver = Annotated[CurrentUser, Depends(require_permission(Permission.FINANCIAL_REQUEST_APPROVE))]


FINANCIAL_REQUEST_LABELS = {
    "SALARY": "Зарплата",
    "BONUS": "Бонус",
    "PENALTY": "Штраф",
    "ADVANCE": "Аванс",
    "PAYMENT": "Выплата",
    "SALARY_CHANGE": "Изменение зарплаты",
}


async def notify_approvers(session: AsyncSession, item: FinancialRequest) -> None:
    worker_name = await session.scalar(
        select(EmployeeProfile.full_name).where(EmployeeProfile.user_id == item.worker_user_id)
    )
    bosses = (
        await session.scalars(
            select(User.id).where(
                User.role == UserRole.BOSS,
                User.status == AccountStatus.ACTIVE,
                User.archived_at.is_(None),
            )
        )
    ).all()
    sign = {"BONUS": "+", "PENALTY": "−"}.get(item.type, "")
    amount = (
        f"Текущая: {item.expected_amount} {item.currency}\nНовая: {item.amount} {item.currency}"
        if item.type == "SALARY_CHANGE"
        else f"Сумма: {sign}{item.amount} {item.currency}"
    )
    await stage_notifications(
        session,
        [
            Notification(
                recipient_user_id=boss_id,
                title="Запрос на изменение зарплаты"
                if item.type == "SALARY_CHANGE"
                else f"{FINANCIAL_REQUEST_LABELS[item.type]} на согласовании",
                body=f"{worker_name or 'Сотрудник'}\nМесяц: {item.period:%m.%Y}\n{amount}\n"
                f"Причина: {item.reason}\n"
                "Откройте раздел «Согласование выплат», чтобы принять решение.",
                created_at=utc_now(),
            )
            for boss_id in bosses
        ],
    )


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
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
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
    type: Literal["SALARY", "BONUS", "PENALTY", "ADVANCE", "PAYMENT", "SALARY_CHANGE"]
    amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    ledger_entry_id: UUID | None = None
    expected_amount: Decimal | None = None
    expected_revision: int | None = None
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
    await notify_approvers(session, item)
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
    if approve and item.type == "SALARY_CHANGE":
        entry = await session.scalar(
            select(LedgerEntry).where(LedgerEntry.id == item.ledger_entry_id).with_for_update()
        )
        if entry is None or entry.type != "SALARY":
            raise ApiError(409, "stale_correction", "Salary entry is unavailable")
        last = await latest_correction(session, entry.id)
        current = last.new_amount if last else entry.amount
        revision = last.revision if last else 0
        if current != item.expected_amount or revision != item.expected_revision:
            raise ApiError(409, "stale_salary_request", "Salary changed; submit a new request")
        correction = LedgerCorrection(
            ledger_entry_id=entry.id,
            revision=revision + 1,
            old_amount=current,
            new_amount=item.amount,
            reason=item.reason,
            actor_user_id=actor.id,
            idempotency_key=f"salary-change:{item.id}",
            created_at=utc_now(),
        )
        session.add(correction)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise ApiError(
                409, "correction_conflict", "Salary changed; reload before approving"
            ) from exc
        add_audit_event(
            session,
            actor_user_id=actor.id,
            action="PAYROLL_CORRECTED",
            entity_type="ledger_correction",
            entity_id=correction.id,
            old_value={"amount": str(current)},
            new_value={
                "amount": str(item.amount),
                "financial_request_id": str(item.id),
                "ledger_entry_id": str(entry.id),
                "reason": item.reason,
            },
            request_id=getattr(request.state, "request_id", None),
        )
    elif approve:
        entry = LedgerEntry(
            cost_integrated=True,
            request_id=item.id,
            worker_user_id=item.worker_user_id,
            type=item.type,
            amount=item.amount,
            currency=item.currency,
            period=item.period,
            created_at=utc_now(),
        )
        session.add(entry)
    try:
        # Validate the ledger insert before notification queries can autoflush it.
        await session.flush()
        if approve:
            await sync_payroll_cost(session, entry, item.amount, actor.id)
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(
            409, "ledger_conflict", "Salary for this period has already been posted"
        ) from exc
    if item.type in FINANCIAL_REQUEST_LABELS:
        worker_name = await session.scalar(
            select(EmployeeProfile.full_name).where(EmployeeProfile.user_id == item.worker_user_id)
        )
        await stage_notifications(
            session,
            [
                Notification(
                    recipient_user_id=item.created_by,
                    title=(
                        "Изменение зарплаты одобрено" if approve else "Изменение зарплаты отклонено"
                    )
                    if item.type == "SALARY_CHANGE"
                    else (
                        "Финансовая заявка одобрена" if approve else "Финансовая заявка отклонена"
                    ),
                    body=f"{worker_name or 'Сотрудник'}\nМесяц: {item.period:%m.%Y}\n"
                    + (
                        f"{item.expected_amount} → {item.amount} {item.currency}. "
                        if item.type == "SALARY_CHANGE"
                        else f"{FINANCIAL_REQUEST_LABELS[item.type]}: "
                        f"{item.amount} {item.currency}. "
                    )
                    + (
                        "Одобрено и учтено в расчёте."
                        if approve
                        else "Отклонено. В расчёт не включено."
                    ),
                    created_at=utc_now(),
                )
            ],
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


class WorkerFinanceHistoryRead(BaseModel):
    id: UUID
    type: Literal["SALARY", "BONUS", "PENALTY", "ADVANCE", "PAYMENT"]
    amount: Decimal
    currency: str
    period: date
    reason: str
    created_at: datetime
    corrected: bool


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


@router.get("/workers/me/finance/history", response_model=list[WorkerFinanceHistoryRead])
async def own_finance_history(
    period: date,
    session: Session,
    actor: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_READ_OWN))],
) -> list[WorkerFinanceHistoryRead]:
    rows = list(
        (
            await session.scalars(
                select(LedgerEntry)
                .where(
                    LedgerEntry.worker_user_id == actor.id,
                    LedgerEntry.period == period,
                )
                .order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc())
            )
        ).all()
    )
    if not rows:
        return []
    corrections = list(
        (
            await session.scalars(
                select(LedgerCorrection)
                .where(LedgerCorrection.ledger_entry_id.in_([row.id for row in rows]))
                .order_by(LedgerCorrection.revision)
            )
        ).all()
    )
    corrected = {item.ledger_entry_id: item.new_amount for item in corrections}
    requests = {
        item.id: item
        for item in (
            await session.scalars(
                select(FinancialRequest).where(
                    FinancialRequest.id.in_([row.request_id for row in rows])
                )
            )
        ).all()
    }
    return [
        WorkerFinanceHistoryRead(
            id=row.id,
            type=row.type,
            amount=corrected.get(row.id, row.amount),
            currency=row.currency,
            period=row.period,
            reason=requests[row.request_id].reason,
            created_at=row.created_at,
            corrected=row.id in corrected,
        )
        for row in rows
    ]


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
        await sync_payroll_cost(session, entry, payload.new_amount, actor.id)
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


async def latest_correction(session: AsyncSession, entry_id: UUID) -> LedgerCorrection | None:
    return await session.scalar(
        select(LedgerCorrection)
        .where(LedgerCorrection.ledger_entry_id == entry_id)
        .order_by(LedgerCorrection.revision.desc())
        .limit(1)
    )


class SalaryChangeCreate(CorrectionCreate):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_revision: int = Field(ge=0)


class AdminSalaryRead(LedgerRead):
    worker_name: str
    original_amount: Decimal
    expected_revision: int
    status: str


@router.get("/admin/salary-ledger", response_model=list[AdminSalaryRead])
async def admin_salary_ledger(
    session: Session,
    _: Creator,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str, Query(max_length=200)] = "",
) -> list[AdminSalaryRead]:
    rows = (
        await session.execute(
            select(LedgerEntry, EmployeeProfile.full_name)
            .join(EmployeeProfile, EmployeeProfile.user_id == LedgerEntry.worker_user_id)
            .where(
                LedgerEntry.type == "SALARY",
                EmployeeProfile.full_name.icontains(q.strip(), autoescape=True),
            )
            .order_by(LedgerEntry.period.desc(), LedgerEntry.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    pending = set(
        (
            await session.scalars(
                select(FinancialRequest.ledger_entry_id).where(
                    FinancialRequest.type == "SALARY_CHANGE",
                    FinancialRequest.status == "PENDING_BOSS",
                )
            )
        ).all()
    )
    result = []
    for entry, name in rows:
        last = await latest_correction(session, entry.id)
        result.append(
            AdminSalaryRead(
                **LedgerRead.model_validate(entry).model_dump(exclude={"amount"}),
                amount=last.new_amount if last else entry.amount,
                original_amount=entry.amount,
                worker_name=name,
                expected_revision=last.revision if last else 0,
                status="PENDING_BOSS" if entry.id in pending else "APPROVED",
            )
        )
    return result


@router.post(
    "/admin/salary-ledger/{entry_id}/change-requests", response_model=RequestRead, status_code=201
)
async def request_salary_change(
    entry_id: UUID,
    payload: SalaryChangeCreate,
    session: Session,
    actor: Creator,
    request: Request,
    key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> FinancialRequest:
    replay = await session.scalar(
        select(FinancialRequest).where(
            FinancialRequest.created_by == actor.id, FinancialRequest.idempotency_key == key
        )
    )
    if replay:
        if (
            replay.type != "SALARY_CHANGE"
            or replay.ledger_entry_id != entry_id
            or replay.amount != payload.new_amount
            or replay.expected_amount != payload.expected_amount
            or replay.expected_revision != payload.expected_revision
            or replay.reason != payload.reason
        ):
            raise ApiError(409, "idempotency_conflict", "Key was already used for another request")
        return replay
    entry = await session.scalar(
        select(LedgerEntry)
        .where(LedgerEntry.id == entry_id, LedgerEntry.type == "SALARY")
        .with_for_update()
    )
    if entry is None:
        raise ApiError(404, "ledger_entry_not_found", "Salary entry was not found")
    last = await latest_correction(session, entry.id)
    current = last.new_amount if last else entry.amount
    revision = last.revision if last else 0
    if current != payload.expected_amount or revision != payload.expected_revision:
        raise ApiError(409, "stale_salary_request", "Salary changed; submit a new request")
    if current == payload.new_amount:
        raise ApiError(422, "unchanged_amount", "New salary must differ")
    if not payload.reason.strip():
        raise ApiError(422, "invalid_reason", "Provide a reason")
    item = FinancialRequest(
        worker_user_id=entry.worker_user_id,
        type="SALARY_CHANGE",
        amount=payload.new_amount,
        expected_amount=current,
        expected_revision=revision,
        ledger_entry_id=entry.id,
        period=entry.period,
        currency=entry.currency,
        reason=payload.reason.strip(),
        created_by=actor.id,
        idempotency_key=key,
        status="PENDING_BOSS",
        created_at=utc_now(),
    )
    session.add(item)
    try:
        await session.flush()
        await notify_approvers(session, item)
        add_audit_event(
            session,
            actor_user_id=actor.id,
            action="SALARY_CHANGE_REQUESTED",
            entity_type="financial_request",
            entity_id=item.id,
            old_value={"amount": str(current), "revision": revision},
            new_value={
                "amount": str(item.amount),
                "ledger_entry_id": str(entry.id),
                "status": item.status,
                "reason": item.reason,
            },
            request_id=getattr(request.state, "request_id", None),
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "pending_salary_change", "A salary change is already pending") from exc
    return item


class PayrollExpenseLinks(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expense_ids: list[UUID] = Field(min_length=1, max_length=100)
    expected_amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    expected_revision: int = Field(ge=0)


@router.post("/boss/ledger/{entry_id}/expense-links")
async def reconcile_payroll_expenses(
    entry_id: UUID,
    payload: PayrollExpenseLinks,
    session: Session,
    actor: Approver,
) -> dict:
    """Explicitly link already-booked legacy costs; never infer or rewrite money."""
    from backend.finance.models import Expense

    entry = await session.scalar(
        select(LedgerEntry).where(LedgerEntry.id == entry_id).with_for_update()
    )
    if entry is None or entry.type not in ("SALARY", "BONUS", "PENALTY"):
        raise ApiError(404, "ledger_entry_not_found", "Payroll accrual entry was not found")
    last = await latest_correction(session, entry.id)
    amount, revision = (last.new_amount, last.revision) if last else (entry.amount, 0)
    if amount != payload.expected_amount or revision != payload.expected_revision:
        raise ApiError(409, "stale_correction", "Payroll changed; reload before reconciliation")
    existing = list(
        (await session.scalars(select(Expense).where(Expense.payroll_entry_id == entry.id))).all()
    )
    if existing:
        if {e.id for e in existing} == set(payload.expense_ids):
            return {"status": "LINKED", "expense_ids": [e.id for e in existing]}
        raise ApiError(
            409,
            "payroll_cost_already_linked",
            "Entry already has linked costs; do not book it twice",
        )
    expenses = list(
        (
            await session.scalars(
                select(Expense)
                .where(Expense.id.in_(payload.expense_ids))
                .order_by(Expense.id)
                .with_for_update()
            )
        ).all()
    )
    expected = -amount if entry.type == "PENALTY" else amount
    if (
        len(expenses) != len(set(payload.expense_ids))
        or not expenses
        or len({(e.object_id, e.city_id) for e in expenses}) != len(expenses)
        or sum(e.amount for e in expenses) != expected
        or any(
            e.payroll_entry_id is not None
            or e.archived_at is not None
            or e.status != "APPROVED"
            or e.currency != entry.currency
            or (e.expense_date.year, e.expense_date.month)
            != (entry.period.year, entry.period.month)
            or e.category not in ("salary", "bonus")
            for e in expenses
        )
    ):
        raise ApiError(
            422,
            "payroll_reconciliation_mismatch",
            "Select distinct approved payroll costs matching amount, currency, "
            "period and object allocations",
        )
    for expense in expenses:
        expense.payroll_entry_id = entry.id
        expense.payroll_source_key = f"{entry.id}:{expense.object_id or expense.city_id}"
    entry.cost_integrated = True
    add_audit_event(
        session,
        actor_user_id=actor.id,
        action="PAYROLL_EXPENSES_LINKED",
        entity_type="ledger_entry",
        entity_id=entry.id,
        new_value={"amount": str(amount), "source": "explicit_reconciliation"},
    )
    await session.commit()
    return {"status": "LINKED", "expense_ids": [e.id for e in expenses]}
