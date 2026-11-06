"""Object folders and uploaded files."""

import sqlalchemy as sa

from alembic import op

revision = "20261105_0044"
down_revision = "20261105_0043"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "object_files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "object_id",
            sa.Uuid(),
            sa.ForeignKey("work_objects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("object_files.id", ondelete="RESTRICT")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("content", sa.LargeBinary()),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column(
            "created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_object_files_object_id", "object_files", ["object_id"])
    op.create_index("ix_object_files_parent_id", "object_files", ["parent_id"])


def downgrade():
    op.drop_table("object_files")
