"""Append-only payroll corrections."""

import sqlalchemy as sa

from alembic import op

revision = "20261005_0006"
down_revision = "20261005_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ledger_corrections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "ledger_entry_id",
            sa.Uuid(),
            sa.ForeignKey("ledger_entries.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("old_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("new_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ledger_entry_id", "revision"),
        sa.UniqueConstraint("actor_user_id", "idempotency_key"),
        sa.CheckConstraint("old_amount >= 0 AND new_amount >= 0", name="ck_correction_amounts"),
    )
    op.create_index(
        "ix_ledger_corrections_ledger_entry_id", "ledger_corrections", ["ledger_entry_id"]
    )
    op.execute(
        "CREATE TRIGGER payroll_corrections_append_only BEFORE UPDATE OR DELETE "
        "ON ledger_corrections FOR EACH ROW EXECUTE FUNCTION protect_payroll_ledger()"
    )


def downgrade() -> None:
    op.drop_table("ledger_corrections")
