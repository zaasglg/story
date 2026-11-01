"""Encrypted worker salary account."""

import sqlalchemy as sa

from alembic import op

revision = "20261101_0024"
down_revision = "20261101_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("employee_private_data", sa.Column("salary_account_encrypted", sa.Text()))


def downgrade() -> None:
    op.drop_column("employee_private_data", "salary_account_encrypted")
