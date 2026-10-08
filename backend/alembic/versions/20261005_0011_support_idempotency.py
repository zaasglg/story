"""Prevent duplicate support tickets on client retries."""

import sqlalchemy as sa

from alembic import op

revision = "20261005_0011"
down_revision = "20261005_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("support_tickets", sa.Column("client_request_id", sa.String(128)))
    op.create_unique_constraint(
        "uq_support_request_key", "support_tickets", ["requester_user_id", "client_request_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_support_request_key", "support_tickets", type_="unique")
    op.drop_column("support_tickets", "client_request_id")
