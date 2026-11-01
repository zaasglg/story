"""Add equipment photo storage and optional image references."""

import sqlalchemy as sa

from alembic import op

revision = "20261031_0020"
down_revision = "20261031_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "equipment_photos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "uploaded_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("equipment_items", sa.Column("photo_url", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("equipment_items", "photo_url")
    op.drop_table("equipment_photos")
