"""Private manager notes."""

import sqlalchemy as sa

from alembic import op
from backend.core.migration_schema import create_table, ensure_index

revision = "20261106_0047"
down_revision = "20261106_0046"
branch_labels = depends_on = None


def upgrade():
    create_table(
        "personal_notes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    ensure_index("ix_personal_notes_owner_id", "personal_notes", ["owner_id"])


def downgrade():
    op.drop_table("personal_notes")
