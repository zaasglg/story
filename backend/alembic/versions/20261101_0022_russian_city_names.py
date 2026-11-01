"""Use Russian city names without changing city identifiers or relationships."""

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "20261101_0022"
down_revision = "20261101_0021"
branch_labels = None
depends_on = None

CITY_NAMES = {
    "Lipetsk": "Липецк",
    "Kursk": "Курск",
    "Voronezh": "Воронеж",
    "Belgorod": "Белгород",
    "Oryol": "Орёл",
    "Orel": "Орёл",
}


def upgrade() -> None:
    connection = op.get_bind()
    cities = sa.table("cities", sa.column("name", sa.String()))
    profiles = sa.table("employee_profiles", sa.column("city", sa.String()))
    changed = 0
    for old, new in CITY_NAMES.items():
        # Existing unique constraints abort the transaction on conflicting cities.
        result = connection.execute(sa.update(cities).where(cities.c.name == old).values(name=new))
        changed += result.rowcount
        connection.execute(sa.update(profiles).where(profiles.c.city == old).values(city=new))
    if changed:
        audit = sa.table(
            "audit_logs",
            sa.column("id", sa.Uuid()),
            sa.column("action", sa.String()),
            sa.column("entity_type", sa.String()),
            sa.column("new_value", sa.JSON()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("succeeded", sa.Boolean()),
        )
        connection.execute(
            sa.insert(audit).values(
                id=uuid4(),
                action="CITY_NAMES_LOCALIZED",
                entity_type="city",
                new_value={"renamed_cities": changed},
                created_at=datetime.now(UTC),
                succeeded=True,
            )
        )


def downgrade() -> None:
    # Renaming data is not reversed automatically; a backup is retained.
    pass
