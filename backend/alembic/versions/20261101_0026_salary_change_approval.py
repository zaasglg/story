"""Salary adjustments proposed by administrators and approved by executives."""

import sqlalchemy as sa

from alembic import op

revision = "20261101_0026"
down_revision = "20261101_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("financial_requests", sa.Column("ledger_entry_id", sa.Uuid()))
    op.add_column("financial_requests", sa.Column("expected_amount", sa.Numeric(14, 2)))
    op.add_column("financial_requests", sa.Column("expected_revision", sa.Integer()))
    op.create_foreign_key(
        "fk_salary_change_ledger_entry",
        "financial_requests",
        "ledger_entries",
        ["ledger_entry_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint("ck_financial_request_type", "financial_requests", type_="check")
    op.drop_constraint("ck_financial_request_amount", "financial_requests", type_="check")
    op.create_check_constraint(
        "ck_financial_request_type",
        "financial_requests",
        "type IN ('SALARY','BONUS','PENALTY','ADVANCE','PAYMENT','SALARY_CHANGE')",
    )
    op.create_check_constraint(
        "ck_financial_request_amount",
        "financial_requests",
        "(type = 'SALARY_CHANGE' AND amount >= 0) OR (type != 'SALARY_CHANGE' AND amount > 0)",
    )
    op.create_check_constraint(
        "ck_salary_change_snapshot",
        "financial_requests",
        "type != 'SALARY_CHANGE' OR (ledger_entry_id IS NOT NULL AND expected_amount IS NOT NULL "
        "AND expected_amount >= 0 AND expected_revision IS NOT NULL AND expected_revision >= 0)",
    )
    op.create_index(
        "uq_pending_salary_change",
        "financial_requests",
        ["ledger_entry_id"],
        unique=True,
        postgresql_where=sa.text("type = 'SALARY_CHANGE' AND status = 'PENDING_BOSS'"),
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text("SELECT EXISTS(SELECT 1 FROM financial_requests WHERE type = 'SALARY_CHANGE')")
        )
        .scalar()
    ):
        raise RuntimeError("Salary-change history cannot be represented by the previous schema")
    op.drop_index("uq_pending_salary_change", table_name="financial_requests")
    op.drop_constraint("ck_salary_change_snapshot", "financial_requests", type_="check")
    op.drop_constraint("ck_financial_request_type", "financial_requests", type_="check")
    op.drop_constraint("ck_financial_request_amount", "financial_requests", type_="check")
    op.create_check_constraint(
        "ck_financial_request_type",
        "financial_requests",
        "type IN ('SALARY','BONUS','PENALTY','ADVANCE','PAYMENT')",
    )
    op.create_check_constraint("ck_financial_request_amount", "financial_requests", "amount > 0")
    op.drop_constraint("fk_salary_change_ledger_entry", "financial_requests", type_="foreignkey")
    op.drop_column("financial_requests", "expected_revision")
    op.drop_column("financial_requests", "expected_amount")
    op.drop_column("financial_requests", "ledger_entry_id")
