"""Object schedule and responsible master."""

import sqlalchemy as sa

from alembic import op
from backend.core.migration_schema import add_column, ensure_foreign_key, ensure_index

revision = "20261104_0039"
down_revision = "20261104_0038"
branch_labels = depends_on = None


def upgrade():
    add_column("work_objects", sa.Column("start_date", sa.Date(), nullable=True))
    add_column("work_objects", sa.Column("estimated_end_date", sa.Date(), nullable=True))
    add_column("work_objects", sa.Column("responsible_master_user_id", sa.Uuid(), nullable=True))
    ensure_foreign_key(
        "fk_work_objects_responsible_master",
        "work_objects",
        "users",
        ["responsible_master_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    ensure_index(
        "ix_work_objects_responsible_master_user_id", "work_objects", ["responsible_master_user_id"]
    )


def downgrade():
    op.drop_index("ix_work_objects_responsible_master_user_id", table_name="work_objects")
    op.drop_constraint("fk_work_objects_responsible_master", "work_objects", type_="foreignkey")
    op.drop_column("work_objects", "responsible_master_user_id")
    op.drop_column("work_objects", "estimated_end_date")
    op.drop_column("work_objects", "start_date")
