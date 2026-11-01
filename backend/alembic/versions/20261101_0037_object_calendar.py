"""Object-wide approved non-working days; no historical attendance backfill."""

import sqlalchemy as sa

from alembic import op

revision = "20261101_0037"
down_revision = "20261101_0036"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "object_calendar_days",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "object_id",
            sa.Uuid(),
            sa.ForeignKey("work_objects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("is_working_day", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "updated_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("object_id", "work_date"),
    )
    op.create_index("ix_object_calendar_days_work_date", "object_calendar_days", ["work_date"])


def downgrade():
    raise RuntimeError("Approved calendar and its history must be retained")
