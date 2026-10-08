"""Financial requests and append-only approved payroll ledger."""

import sqlalchemy as sa

from alembic import op

revision = "20261005_0005"
down_revision = "20261005_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "financial_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "worker_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column(
            "created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("reviewed_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("created_by", "idempotency_key"),
        sa.CheckConstraint("amount > 0", name="ck_financial_request_amount"),
        sa.CheckConstraint(
            "type IN ('SALARY','BONUS','PENALTY','ADVANCE','PAYMENT')",
            name="ck_financial_request_type",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_BOSS','APPROVED','REJECTED','CANCELLED')",
            name="ck_financial_request_status",
        ),
    )
    op.create_index(
        "ix_financial_requests_worker_user_id", "financial_requests", ["worker_user_id"]
    )
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "request_id",
            sa.Uuid(),
            sa.ForeignKey("financial_requests.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "worker_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ledger_entries_worker_user_id", "ledger_entries", ["worker_user_id"])
    op.create_index(
        "uq_ledger_salary_period",
        "ledger_entries",
        ["worker_user_id", "period", "currency"],
        unique=True,
        postgresql_where=sa.text("type = 'SALARY'"),
    )
    op.execute("""CREATE FUNCTION protect_payroll_ledger() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'Payroll ledger entries are append-only'; END;
        $$ LANGUAGE plpgsql""")
    op.execute("""CREATE TRIGGER payroll_ledger_append_only BEFORE UPDATE OR DELETE
        ON ledger_entries FOR EACH ROW EXECUTE FUNCTION protect_payroll_ledger()""")


def downgrade() -> None:
    op.execute("DROP TRIGGER payroll_ledger_append_only ON ledger_entries")
    op.execute("DROP FUNCTION protect_payroll_ledger()")
    op.drop_table("ledger_entries")
    op.drop_table("financial_requests")
