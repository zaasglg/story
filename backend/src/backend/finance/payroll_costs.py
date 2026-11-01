"""Accrual-based payroll expenses. No historical financial backfill is implicit."""

from datetime import date
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import select

from backend.audit.service import add_audit_event
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.finance.models import Expense
from backend.identity.models import EmployeeProfile
from backend.operations.models import WorkDay, WorkDayStatus
from backend.workforce.models import WorkObject


def allocate_cents(amount, weights):
    keys = sorted(weights, key=str)
    total = sum(weights.values())
    cents = int(abs(amount) * 100)
    shares = {
        key: int((Decimal(cents) * weights[key] / total).to_integral_value(rounding=ROUND_DOWN))
        for key in keys
    }
    # Largest fractional remainders, stable ties. Sum always equals the ledger amount.
    ranked = sorted(
        keys, key=lambda key: (-(Decimal(cents) * weights[key] / total - shares[key]), str(key))
    )
    for key in ranked[: cents - sum(shares.values())]:
        shares[key] += 1
    return {key: Decimal(value) / 100 * (-1 if amount < 0 else 1) for key, value in shares.items()}


async def sync_payroll_cost(session, entry, amount, actor_id):
    if not entry.cost_integrated or entry.type not in ("SALARY", "BONUS", "PENALTY"):
        return
    signed = -amount if entry.type == "PENALTY" else amount
    existing = list(
        (
            await session.scalars(
                select(Expense).where(Expense.payroll_entry_id == entry.id).with_for_update()
            )
        ).all()
    )
    if existing:
        weights = {(e.object_id, e.city_id): abs(e.amount) for e in existing}
        if not sum(weights.values()):
            weights = {key: Decimal(1) for key in weights}
    else:
        end = date(entry.period.year + (entry.period.month == 12), entry.period.month % 12 + 1, 1)
        rows = (
            await session.execute(
                select(WorkDay.object_id, WorkObject.city_id, WorkDay.daily_earnings)
                .join(WorkObject, WorkObject.id == WorkDay.object_id)
                .where(
                    WorkDay.worker_user_id == entry.worker_user_id,
                    WorkDay.currency == entry.currency,
                    WorkDay.work_date >= entry.period,
                    WorkDay.work_date < end,
                    WorkDay.status == WorkDayStatus.APPROVED,
                    WorkDay.archived_at.is_(None),
                )
            )
        ).all()
        weights = {}
        for oid, cid, value in rows:
            if value > 0:
                weights[(oid, cid)] = weights.get((oid, cid), Decimal(0)) + value
        if not weights:
            cid = await session.scalar(
                select(EmployeeProfile.city_id).where(
                    EmployeeProfile.user_id == entry.worker_user_id
                )
            )
            if cid is None:
                raise ApiError(
                    409, "payroll_city_required", "Set a worker city before posting payroll costs"
                )
            weights[(None, cid)] = Decimal(1)
    amounts = allocate_cents(signed, weights)
    old = {(e.object_id, e.city_id): e for e in existing}
    for (oid, cid), value in amounts.items():
        item = old.get((oid, cid))
        before = {"amount": str(item.amount)} if item else None
        if item is not None and item.amount == value:
            continue
        if item is None:
            item = Expense(
                payroll_entry_id=entry.id,
                payroll_source_key=f"{entry.id}:{oid or cid}",
                object_id=oid,
                city_id=cid,
                expense_date=entry.period,
                amount=value,
                currency=entry.currency,
                category="bonus" if entry.type == "BONUS" else "salary",
                comment=f"Payroll {entry.type}; ledger {entry.id}",
                status="APPROVED",
                created_by=actor_id,
                updated_by=actor_id,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(item)
        else:
            item.amount, item.updated_by, item.updated_at = value, actor_id, utc_now()
            item.revision += 1
        await session.flush()
        add_audit_event(
            session,
            actor_user_id=actor_id,
            action="PAYROLL_EXPENSE_POSTED",
            entity_type="expense",
            entity_id=item.id,
            old_value=before,
            new_value={
                "amount": str(value),
                "ledger_entry_id": str(entry.id),
                "object_id": str(oid) if oid else None,
            },
        )
