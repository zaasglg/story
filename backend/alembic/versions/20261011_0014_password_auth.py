"""Password authentication; invalidate outstanding OTPs and sessions."""

import sqlalchemy as sa

from alembic import op

revision = "20261011_0014"
down_revision = "20261006_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(255), nullable=True))
    op.execute(
        "UPDATE otp_challenges SET consumed_at = CURRENT_TIMESTAMP WHERE consumed_at IS NULL"
    )
    op.execute(
        "UPDATE auth_sessions SET revoked_at = CURRENT_TIMESTAMP, "
        "revoke_reason = 'password_auth_migration' WHERE revoked_at IS NULL"
    )
    op.execute("UPDATE users SET auth_version = auth_version + 1")


def downgrade() -> None:
    op.drop_column("users", "password_hash")
