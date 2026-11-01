"""Retain designated master on the immutable scan snapshot, without guessing legacy data."""

import sqlalchemy as sa

from alembic import op

revision = "20261101_0031"
down_revision = "20261101_0030"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("shift_scan_events") as batch:
        batch.add_column(sa.Column("master_user_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_shift_event_master", "users", ["master_user_id"], ["id"], ondelete="RESTRICT"
        )


def downgrade():
    with op.batch_alter_table("shift_scan_events") as batch:
        batch.drop_constraint("fk_shift_event_master", type_="foreignkey")
        batch.drop_column("master_user_id")
