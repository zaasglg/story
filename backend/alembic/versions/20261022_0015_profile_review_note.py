"""Profile decision notes visible to the requester."""

import sqlalchemy as sa

from alembic import op

revision = "20261022_0015"
down_revision = "20261011_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("profile_change_requests", sa.Column("review_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("profile_change_requests", "review_note")
