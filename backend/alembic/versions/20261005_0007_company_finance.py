"""Boss revenue, expense confirmation and encrypted company banks."""

import sqlalchemy as sa

from alembic import op

revision = "20261005_0007"
down_revision = "20261005_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "expenses", sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT")
    )
    op.create_check_constraint("ck_expenses_status", "expenses", "status IN ('DRAFT','APPROVED')")
    op.create_table(
        "revenues",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("object_id", sa.Uuid(), sa.ForeignKey("work_objects.id", ondelete="RESTRICT")),
        sa.Column(
            "city_id", sa.Uuid(), sa.ForeignKey("cities.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("revenue_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("category", sa.String(128), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column("attachment_url", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "updated_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("amount > 0", name="ck_revenue_positive_amount"),
        sa.CheckConstraint("status IN ('DRAFT','APPROVED')", name="ck_revenues_status"),
    )
    for column in ("city_id", "object_id", "revenue_date"):
        op.create_index(f"ix_revenues_{column}", "revenues", [column])
    op.create_table(
        "company_bank_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("iban_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("company_bank_accounts")
    op.drop_table("revenues")
    op.drop_constraint("ck_expenses_status", "expenses", type_="check")
    op.drop_column("expenses", "status")
