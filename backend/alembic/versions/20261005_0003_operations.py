"""Add schedules, attendance, tariffs, and work days.

Revision ID: 20261005_0003
Revises: 20261005_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261005_0003"
down_revision: str | None = "20261005_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def audit_columns() -> list[sa.Column]:
    return [
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    ]


def audit_foreign_keys() -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
    ]


def upgrade() -> None:
    op.create_table(
        "tariff_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("rate_per_m3", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("rate_per_m3 > 0", name="ck_tariff_history_positive_rate"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["object_id"], ["work_objects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tariff_history_object_id", "tariff_history", ["object_id"])
    op.create_index(
        "uq_tariff_history_active_object",
        "tariff_history",
        ["object_id"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL AND archived_at IS NULL"),
    )

    op.create_table(
        "work_schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("worker_user_id", sa.Uuid(), nullable=False),
        sa.Column("brigade_id", sa.Uuid(), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("shift_start", sa.Time(), nullable=False),
        sa.Column("shift_end", sa.Time(), nullable=False),
        *audit_columns(),
        *audit_foreign_keys(),
        sa.ForeignKeyConstraint(["brigade_id"], ["brigades.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["object_id"], ["work_objects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["worker_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("worker_user_id", "work_date", name="uq_work_schedule_worker_date"),
    )
    op.create_index("ix_work_schedules_worker_user_id", "work_schedules", ["worker_user_id"])
    op.create_index("ix_work_schedules_work_date", "work_schedules", ["work_date"])

    op.create_table(
        "attendance_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("worker_user_id", sa.Uuid(), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("check_in", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_out", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(7), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        *audit_columns(),
        *audit_foreign_keys(),
        sa.CheckConstraint(
            "status IN ('PRESENT', 'ABSENT', 'LATE', 'EXCUSED')",
            name="ck_attendance_records_status",
        ),
        sa.ForeignKeyConstraint(["worker_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("worker_user_id", "work_date", name="uq_attendance_worker_date"),
    )
    op.create_index(
        "ix_attendance_records_worker_user_id", "attendance_records", ["worker_user_id"]
    )
    op.create_index("ix_attendance_records_work_date", "attendance_records", ["work_date"])

    op.create_table(
        "work_days",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("worker_user_id", sa.Uuid(), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("brigade_id", sa.Uuid(), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("check_in", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_out", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hours", sa.Numeric(8, 2), nullable=False),
        sa.Column("m3", sa.Numeric(12, 2), nullable=False),
        sa.Column("rate_per_m3", sa.Numeric(12, 2), nullable=False),
        sa.Column("daily_earnings", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        *audit_columns(),
        *audit_foreign_keys(),
        sa.CheckConstraint("hours >= 0 AND hours <= 24", name="ck_work_days_hours"),
        sa.CheckConstraint("m3 >= 0", name="ck_work_days_m3"),
        sa.CheckConstraint("rate_per_m3 > 0", name="ck_work_days_rate"),
        sa.CheckConstraint("daily_earnings >= 0", name="ck_work_days_earnings"),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'APPROVED', 'REJECTED')",
            name="ck_work_days_status",
        ),
        sa.ForeignKeyConstraint(["brigade_id"], ["brigades.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["object_id"], ["work_objects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["worker_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "worker_user_id", "object_id", "work_date", name="uq_work_days_worker_object_date"
        ),
    )
    op.create_index("ix_work_days_worker_user_id", "work_days", ["worker_user_id"])
    op.create_index("ix_work_days_object_id", "work_days", ["object_id"])
    op.create_index("ix_work_days_brigade_id", "work_days", ["brigade_id"])
    op.create_index("ix_work_days_work_date", "work_days", ["work_date"])


def downgrade() -> None:
    for index in (
        "ix_work_days_work_date",
        "ix_work_days_brigade_id",
        "ix_work_days_object_id",
        "ix_work_days_worker_user_id",
    ):
        op.drop_index(index, table_name="work_days")
    op.drop_table("work_days")
    op.drop_index("ix_attendance_records_work_date", table_name="attendance_records")
    op.drop_index("ix_attendance_records_worker_user_id", table_name="attendance_records")
    op.drop_table("attendance_records")
    op.drop_index("ix_work_schedules_work_date", table_name="work_schedules")
    op.drop_index("ix_work_schedules_worker_user_id", table_name="work_schedules")
    op.drop_table("work_schedules")
    op.drop_index("uq_tariff_history_active_object", table_name="tariff_history")
    op.drop_index("ix_tariff_history_object_id", table_name="tariff_history")
    op.drop_table("tariff_history")
