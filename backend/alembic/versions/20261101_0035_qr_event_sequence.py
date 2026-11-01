"""Cursor for existing immutable QR events; no duplicate event table."""

import sqlalchemy as sa

from alembic import op

revision = "20261101_0035"
down_revision = "20261101_0034"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "shift_scan_events",
        sa.Column("event_sequence", sa.BigInteger(), sa.Identity(), nullable=False),
    )
    op.create_unique_constraint("uq_shift_event_sequence", "shift_scan_events", ["event_sequence"])


def downgrade():
    raise RuntimeError("Do not invalidate saved QR event cursors")
