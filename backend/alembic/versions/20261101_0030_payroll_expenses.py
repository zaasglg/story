"""Link future payroll costs without recalculating historical ledger/expenses."""

import sqlalchemy as sa

from alembic import op

revision = "20261101_0030"
down_revision = "20261101_0029"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "ledger_entries",
        sa.Column("cost_integrated", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    with op.batch_alter_table("expenses") as batch:
        batch.add_column(sa.Column("payroll_entry_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("payroll_source_key", sa.String(128), nullable=True))
        batch.create_foreign_key(
            "fk_expense_payroll_entry",
            "ledger_entries",
            ["payroll_entry_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_unique_constraint("uq_expense_payroll_source", ["payroll_source_key"])
        batch.drop_constraint("ck_expense_positive_amount", type_="check")
        batch.create_check_constraint(
            "ck_expense_positive_amount", "amount > 0 OR payroll_entry_id IS NOT NULL"
        )


def downgrade():
    raise RuntimeError(
        "Payroll links must be reconciled before downgrade; "
        "automatic loss of cost history is forbidden"
    )
