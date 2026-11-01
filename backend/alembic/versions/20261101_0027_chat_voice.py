"""Authenticated voice messages in existing chat threads."""

import sqlalchemy as sa

from alembic import op

revision = "20261101_0027"
down_revision = "20261101_0026"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("chat_messages", sa.Column("voice_content", sa.LargeBinary(), nullable=True))
    op.add_column("chat_messages", sa.Column("voice_mime", sa.String(32), nullable=True))
    op.add_column("chat_messages", sa.Column("voice_duration_ms", sa.Integer(), nullable=True))
    op.add_column("chat_messages", sa.Column("voice_sha256", sa.String(64), nullable=True))


def downgrade():
    for column in ("voice_sha256", "voice_duration_ms", "voice_mime", "voice_content"):
        op.drop_column("chat_messages", column)
