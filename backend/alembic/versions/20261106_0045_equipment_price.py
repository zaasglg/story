"""Equipment unit price and currency."""

import sqlalchemy as sa

from alembic import op

revision = "20261106_0045"
down_revision = "20261105_0044"
branch_labels = depends_on = None


def upgrade():
    op.add_column("equipment_items", sa.Column("price", sa.Numeric(12, 2), nullable=True))
    op.add_column(
        "equipment_items", sa.Column("currency", sa.String(3), nullable=False, server_default="RUB")
    )


def downgrade():
    op.drop_column("equipment_items", "currency")
    op.drop_column("equipment_items", "price")
