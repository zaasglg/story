"""Expense approval version and audit actor-role snapshot; no amounts changed."""

import sqlalchemy as sa

from alembic import op

revision = "20261101_0036"
down_revision = "20261101_0035"
branch_labels = depends_on = None


def upgrade():
    op.add_column("expenses", sa.Column("recipient", sa.String(255)))
    op.add_column(
        "expenses", sa.Column("revision", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("audit_logs", sa.Column("actor_role", sa.String(16)))
    op.add_column("audit_logs", sa.Column("reason", sa.Text()))


def downgrade():
    raise RuntimeError("Financial audit snapshots must not be discarded")
