"""Snapshot production object/timezone; legacy rows resolve historical assignments on read."""

import sqlalchemy as sa

from alembic import op

revision = "20261101_0029"
down_revision = "20261101_0028"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("production_entries") as batch:
        batch.add_column(sa.Column("object_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("timezone", sa.String(64), nullable=True))
        batch.create_foreign_key(
            "fk_production_object", "work_objects", ["object_id"], ["id"], ondelete="RESTRICT"
        )


def downgrade():
    with op.batch_alter_table("production_entries") as batch:
        batch.drop_constraint("fk_production_object", type_="foreignkey")
        batch.drop_column("timezone")
        batch.drop_column("object_id")
