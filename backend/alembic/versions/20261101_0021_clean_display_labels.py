"""Remove seed labels from displayed data, preserving record identity and history."""

import re
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "20261101_0021"
down_revision = "20261031_0020"
branch_labels = None
depends_on = None

DISPLAY_COLUMNS = {
    "announcements": ("title", "body"),
    "attendance_records": ("comment",),
    "brigades": ("name",),
    "chat_messages": ("body",),
    "employee_profiles": ("employee_id", "full_name"),
    "equipment_items": ("name", "serial_number"),
    "expenses": ("comment",),
    "financial_requests": ("reason",),
    "notifications": ("title", "body"),
    "profile_change_requests": ("reason",),
    "revenues": ("comment",),
    "stock_movements": ("reason",),
    "support_tickets": ("subject", "body"),
    "work_authorizations": ("reference",),
    "work_objects": ("name", "address"),
}


def clean_label(value: str, column: str) -> str:
    roles = {
        "Development Admin": "Администратор",
        "Development Boss": "Руководитель",
        "Development Master": "Мастер",
        "Development Worker": "Сотрудник",
    }
    if value in roles and column == "full_name":
        return roles[value]
    if value == "Демонстрационная запись":
        return "Отметка посещаемости"
    if column == "employee_id":
        return re.sub(r"^DEMO-", "EMP-", value, flags=re.IGNORECASE)
    return re.sub(r"\b(?:демо|demo)\b\s*[·:—-]?\s*", "", value, flags=re.IGNORECASE).strip()


def upgrade() -> None:
    connection = op.get_bind()
    counts = {}
    for name, columns in DISPLAY_COLUMNS.items():
        table = sa.table(
            name, sa.column("id", sa.Uuid()), *(sa.column(c, sa.Text()) for c in columns)
        )
        conditions = [
            condition
            for c in columns
            for condition in (
                table.c[c].ilike("%demo%"),
                table.c[c].contains("ДЕМО"),
                table.c[c].contains("демо"),
                table.c[c].contains("Демонстрационная"),
                table.c[c].like("Development %"),
            )
        ]
        changed = 0
        for row in connection.execute(sa.select(table).where(sa.or_(*conditions))).mappings():
            updates = {
                c: clean_label(row[c], c)
                for c in columns
                if row[c] is not None and clean_label(row[c], c) != row[c]
            }
            if updates:
                # A uniqueness conflict aborts the entire migration transaction.
                connection.execute(
                    sa.update(table).where(table.c.id == row["id"]).values(**updates)
                )
                changed += 1
        if changed:
            counts[name] = changed
    if counts:
        audit = sa.table(
            "audit_logs",
            sa.column("id", sa.Uuid()),
            sa.column("action", sa.String()),
            sa.column("entity_type", sa.String()),
            sa.column("new_value", sa.JSON()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("succeeded", sa.Boolean()),
        )
        connection.execute(
            sa.insert(audit).values(
                id=uuid4(),
                action="DISPLAY_LABELS_NORMALIZED",
                entity_type="database",
                new_value={"changed_rows": counts},
                created_at=datetime.now(UTC),
                succeeded=True,
            )
        )


def downgrade() -> None:
    # Data-only normalization; restoring old labels requires the database backup.
    pass
