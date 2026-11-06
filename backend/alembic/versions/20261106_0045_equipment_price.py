"""Equipment unit price and currency."""

import sqlalchemy as sa

from alembic import op
from backend.core.migration_schema import add_column

revision = "20261106_0045"
down_revision = "20261105_0044"
branch_labels = depends_on = None


def upgrade():
    add_column("equipment_items", sa.Column("price", sa.Numeric(12, 2), nullable=True))
    add_column(
        "equipment_items", sa.Column("currency", sa.String(3), nullable=False, server_default="RUB")
    )


def downgrade():
    op.drop_column("equipment_items", "currency")
    op.drop_column("equipment_items", "price")
