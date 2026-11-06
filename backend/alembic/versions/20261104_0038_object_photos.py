"""Persistent demo object photographs."""

import sqlalchemy as sa

from alembic import op

revision = "20261104_0038"
down_revision = "20261101_0037"
branch_labels = depends_on = None


def upgrade():
    op.add_column("work_objects", sa.Column("photo_url", sa.String(128), nullable=True))
    op.add_column(
        "work_objects",
        sa.Column("photo_is_demo", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_table(
        "work_object_photos",
        sa.Column(
            "object_id",
            sa.Uuid(),
            sa.ForeignKey("work_objects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("work_object_photos")
    op.drop_column("work_objects", "photo_is_demo")
    op.drop_column("work_objects", "photo_url")
