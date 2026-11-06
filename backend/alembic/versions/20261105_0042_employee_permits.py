"""Add five employee work permits and grant them to existing workers and masters."""

import sqlalchemy as sa

from alembic import op

revision = "20261105_0042"
down_revision = "20261104_0041"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "employee_permits",
        sa.Column(
            "worker_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("code", sa.String(24), primary_key=True),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
    )
    op.execute("""
        INSERT INTO employee_permits (worker_user_id, code, granted, updated_at)
        SELECT users.id, permits.code, true, CURRENT_TIMESTAMP
        FROM users CROSS JOIN (
            VALUES ('GROUND'), ('HEIGHT'), ('ENCLOSED'), ('MACHINERY'), ('TOOLS')
        ) AS permits(code)
        WHERE users.role IN ('WORKER', 'MASTER')
    """)


def downgrade():
    op.drop_table("employee_permits")
