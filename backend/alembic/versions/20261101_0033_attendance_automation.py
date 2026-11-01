"""Explicit schedule approval and immutable rating snapshots, no historical replay."""

import sqlalchemy as sa

from alembic import op

revision = "20261101_0033"
down_revision = "20261101_0032"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "work_schedules",
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "work_schedules",
        sa.Column("is_working_day", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("rating_events", sa.Column("rating_before", sa.Numeric(4, 2)))
    op.add_column("rating_events", sa.Column("rating_after", sa.Numeric(4, 2)))
    op.add_column(
        "rating_events", sa.Column("source", sa.String(16), nullable=False, server_default="LEGACY")
    )
    op.alter_column("rating_events", "created_by", nullable=True)


def downgrade():
    raise RuntimeError("Rating snapshots are audit history; restore a protected backup instead")
