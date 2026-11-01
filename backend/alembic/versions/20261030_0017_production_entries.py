"""Add worker production entries.

Revision ID: 20261030_0017
Revises: 20261030_0016
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261030_0017"
down_revision: str | None = "20261030_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "production_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("worker_user_id", sa.Uuid(), nullable=False),
        sa.Column("volume_m3", sa.Numeric(12, 2), nullable=False),
        sa.Column("note", sa.String(240), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["worker_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_production_entries_worker_user_id", "production_entries", ["worker_user_id"]
    )
    op.create_index("ix_production_entries_recorded_at", "production_entries", ["recorded_at"])


def downgrade() -> None:
    op.drop_index("ix_production_entries_recorded_at", table_name="production_entries")
    op.drop_index("ix_production_entries_worker_user_id", table_name="production_entries")
    op.drop_table("production_entries")
