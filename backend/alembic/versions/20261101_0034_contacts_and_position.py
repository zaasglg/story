"""Independent contact directory and public position (no private data backfill)."""

import sqlalchemy as sa

from alembic import op

revision = "20261101_0034"
down_revision = "20261101_0033"
branch_labels = depends_on = None


def upgrade():
    op.add_column("employee_profiles", sa.Column("position", sa.String(128)))
    op.create_table(
        "organization_contacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("position", sa.String(128), nullable=False),
        sa.Column("department", sa.String(128)),
        sa.Column("phone", sa.String(64), nullable=False),
        sa.Column("additional_phone", sa.String(64)),
        sa.Column("email", sa.String(255)),
        sa.Column("responsibilities", sa.Text()),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    raise RuntimeError("Contact history must not be deleted by downgrade")
