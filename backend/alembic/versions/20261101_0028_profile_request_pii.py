"""Encrypt passport values in every historical profile request without deleting history."""

import sqlalchemy as sa

from alembic import op
from backend.core.config import get_settings
from backend.core.crypto import PiiCipher
from backend.workforce.request_pii import protect_changes

revision = "20261101_0028"
down_revision = "20261101_0027"
branch_labels = depends_on = None


def upgrade():
    bind = op.get_bind()
    table = sa.table(
        "profile_change_requests", sa.column("id", sa.Uuid()), sa.column("changes", sa.JSON())
    )
    cipher = PiiCipher(get_settings().pii_encryption_key.get_secret_value())
    for row in bind.execute(sa.select(table.c.id, table.c.changes)).mappings():
        if "passport_number" in row["changes"]:
            bind.execute(
                table.update()
                .where(table.c.id == row["id"])
                .values(changes=protect_changes(row["changes"], cipher))
            )


def downgrade():
    raise RuntimeError(
        "PII migration cannot safely downgrade to plaintext. Restore a protected backup instead."
    )
