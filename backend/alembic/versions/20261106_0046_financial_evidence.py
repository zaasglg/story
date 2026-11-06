"""Optional evidence for financial requests."""

import sqlalchemy as sa

from alembic import op
from backend.core.migration_schema import add_column

revision = "20261106_0046"
down_revision = "20261106_0045"
branch_labels = depends_on = None


def upgrade():
    add_column("financial_requests", sa.Column("evidence_url", sa.String(255), nullable=True))


def downgrade():
    op.drop_column("financial_requests", "evidence_url")
