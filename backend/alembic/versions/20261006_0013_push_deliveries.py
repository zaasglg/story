"""Per-device push submission outcomes."""

import sqlalchemy as sa

from alembic import op

revision = "20261006_0013"
down_revision = "20261006_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "push_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "notification_id",
            sa.Uuid(),
            sa.ForeignKey("notifications.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.Uuid(),
            sa.ForeignKey("push_devices.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(32)),
        sa.UniqueConstraint("notification_id", "device_id", "token_hash"),
    )
    op.create_index("ix_push_deliveries_notification_id", "push_deliveries", ["notification_id"])


def downgrade() -> None:
    op.drop_table("push_deliveries")
