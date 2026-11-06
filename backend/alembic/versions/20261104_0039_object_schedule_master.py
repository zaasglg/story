"""Object schedule and responsible master."""

import sqlalchemy as sa

from alembic import op

revision = "20261104_0039"
down_revision = "20261104_0038"
branch_labels = depends_on = None


def upgrade():
    op.add_column("work_objects", sa.Column("start_date", sa.Date(), nullable=True))
    op.add_column("work_objects", sa.Column("estimated_end_date", sa.Date(), nullable=True))
    op.add_column("work_objects", sa.Column("responsible_master_user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_work_objects_responsible_master",
        "work_objects",
        "users",
        ["responsible_master_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_work_objects_responsible_master_user_id", "work_objects", ["responsible_master_user_id"]
    )


def downgrade():
    op.drop_index("ix_work_objects_responsible_master_user_id", table_name="work_objects")
    op.drop_constraint("fk_work_objects_responsible_master", "work_objects", type_="foreignkey")
    op.drop_column("work_objects", "responsible_master_user_id")
    op.drop_column("work_objects", "estimated_end_date")
    op.drop_column("work_objects", "start_date")
