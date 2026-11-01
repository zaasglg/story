"""Preserve QR attendance context and actor history."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261031_0019"
down_revision: str | None = "20261030_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shift_scan_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "attendance_id",
            sa.Uuid(),
            sa.ForeignKey("attendance_records.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "worker_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "brigade_id",
            sa.Uuid(),
            sa.ForeignKey("brigades.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "object_id",
            sa.Uuid(),
            sa.ForeignKey("work_objects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "city_id", sa.Uuid(), sa.ForeignKey("cities.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("worker_name", sa.String(255), nullable=False),
        sa.Column("actor_name", sa.String(255), nullable=False),
        sa.Column("brigade_name", sa.String(128), nullable=False),
        sa.Column("object_name", sa.String(255), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("attendance_id", "action"),
    )
    for column in ("attendance_id", "worker_user_id", "work_date"):
        op.create_index(f"ix_shift_scan_events_{column}", "shift_scan_events", [column])


def downgrade() -> None:
    op.drop_table("shift_scan_events")
