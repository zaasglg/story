"""Optional evidence for financial requests."""

import sqlalchemy as sa

from alembic import op

revision = "20261106_0046"
down_revision = "20261106_0045"
branch_labels = depends_on = None


def upgrade():
    op.add_column("financial_requests", sa.Column("evidence_url", sa.String(255), nullable=True))


def downgrade():
    op.drop_column("financial_requests", "evidence_url")
