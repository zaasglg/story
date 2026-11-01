"""Store the user-confirmed object location alongside its address."""

import sqlalchemy as sa

from alembic import op

revision = "20261101_0023"
down_revision = "20261101_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("work_objects", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("work_objects", sa.Column("longitude", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("work_objects", "longitude")
    op.drop_column("work_objects", "latitude")
