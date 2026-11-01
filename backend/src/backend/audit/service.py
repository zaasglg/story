from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.core.time import utc_now
from backend.identity.models import AuditLog


def add_audit_event(
    session: AsyncSession,
    *,
    action: str,
    entity_type: str,
    actor_user_id: UUID | None = None,
    entity_id: UUID | None = None,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    request_id: str | None = None,
    ip_hash: str | None = None,
    succeeded: bool = True,
) -> None:
    session.add(
        AuditLog(
            actor_user_id=actor_user_id,
            actor_role=session.info.get("audit_actor_role") if actor_user_id else "SYSTEM",
            reason=(new_value or {}).get("reason")
            or (metadata or {}).get("reason")
            or session.info.get("audit_reason"),
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_value=safe_audit_data(old_value),
            new_value=safe_audit_data(new_value),
            metadata_json=safe_audit_data(metadata),
            created_at=utc_now(),
            request_id=request_id,
            ip_hash=ip_hash,
            succeeded=succeeded,
        )
    )


# Explicit whitelist: audit may show financial changes, never credentials, PII
# documents, account details or private chat bodies.
SAFE_FIELDS = frozenset(
    """id full_name employee_id city city_id object_id brigade_id worker_user_id
master_user_id status role archived_at effective_from effective_to name address timezone
latitude longitude work_date check_in check_out hours m3 rate_per_m3 daily_earnings currency
amount old_amount new_amount expected_amount expected_revision period type category comment
expense_date revenue_date valid_from valid_until reference revoked_at quantity available_quantity
issue_date return_date equipment_id shift_start shift_end revision ledger_entry_id request_id
source fields date reason permission financial_request_id source_key event_type delta
cost_integrated approved is_working_day position recipient
payroll_entry_id payroll_source_key""".split()
)


def safe_audit_data(value):
    if value is None:
        return None
    if isinstance(value, dict):
        return {key: safe_audit_data(item) for key, item in value.items() if key in SAFE_FIELDS}
    if isinstance(value, (list, tuple)):
        return [safe_audit_data(item) for item in value]
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


# The listener is shared by AsyncSession's underlying synchronous Session. It
# runs in the same transaction: rollbacks also remove the corresponding audit.


AUDITED_TABLES = frozenset(
    """users employee_profiles cities brigades work_objects worker_assignments
attendance_records work_days work_schedules object_calendar_days tariff_history
expenses revenues financial_requests
ledger_entries ledger_corrections equipment_items equipment_issues equipment_movements
work_authorizations rating_events""".split()
)


@event.listens_for(Session, "before_flush")
def capture_domain_changes(session, flush_context, instances):
    actor = session.info.get("audit_actor_id")
    if actor is None:
        return
    for item in list(session.new) + list(session.dirty) + list(session.deleted):
        table = getattr(item, "__tablename__", "")
        if table not in AUDITED_TABLES:
            continue
        state = inspect(item)
        is_new = item in session.new
        before, after = {}, {}
        for column in state.mapper.column_attrs:
            key = column.key
            if key not in SAFE_FIELDS or key == "id":
                continue
            history = state.attrs[key].history
            financial = table in {
                "expenses",
                "revenues",
                "financial_requests",
                "ledger_entries",
                "ledger_corrections",
            }
            if not is_new and not history.has_changes() and not financial:
                continue
            if not is_new:
                before[key] = (
                    history.deleted[0]
                    if history.deleted
                    else (getattr(item, key) if not history.has_changes() else None)
                )
            after[key] = getattr(item, key)
        if table in {
            "expenses",
            "revenues",
            "financial_requests",
            "ledger_entries",
            "ledger_corrections",
        }:
            after["reason"] = (
                getattr(item, "reason", None)
                or session.info.get("audit_reason")
                or getattr(item, "comment", None)
                or "Причина не указана"
            )
            after["type"] = getattr(item, "type", None) or getattr(item, "category", table)
        if not after and item not in session.deleted:
            continue
        if item.id is None:
            item.id = uuid4()
        add_audit_event(
            session,
            actor_user_id=actor,
            action="DATA_CREATED" if is_new else "DATA_CHANGED",
            entity_type=table,
            entity_id=item.id,
            old_value=safe_audit_data(before),
            new_value=safe_audit_data(after),
            request_id=session.info.get("audit_request_id"),
        )
