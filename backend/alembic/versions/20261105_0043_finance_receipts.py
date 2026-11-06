"""Store uploaded receipt files."""

import sqlalchemy as sa

from alembic import op

revision = "20261105_0043"
down_revision = "20261105_0042"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "finance_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("finance_receipts")
