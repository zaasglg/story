"""Add optional images to announcement posts."""

import sqlalchemy as sa

from alembic import op

revision = "20261106_0048"
down_revision = "20261106_0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "post_images",
        sa.Column(
            "post_id",
            sa.Uuid(),
            sa.ForeignKey("announcements.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("content", sa.LargeBinary(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("post_images")
