"""Persist employee photo uploads in the application database."""

import sqlalchemy as sa

from alembic import op

revision = "20261030_0016"
down_revision = "20261022_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employee_photos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "uploaded_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("employee_photos")
