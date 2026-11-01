"""Private executive bank accounts, separate from company accounts."""

import sqlalchemy as sa

from alembic import op

revision = "20261101_0025"
down_revision = "20261101_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "personal_bank_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("details_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_personal_bank_accounts_owner_user_id", "personal_bank_accounts", ["owner_user_id"]
    )


def downgrade() -> None:
    op.drop_table("personal_bank_accounts")
