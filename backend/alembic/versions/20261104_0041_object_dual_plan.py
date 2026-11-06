"""Separate hour and volume plans, preserving existing plans."""

import sqlalchemy as sa

from alembic import op
from backend.core.migration_schema import add_column, ensure_check

revision = "20261104_0041"
down_revision = "20261104_0040"
branch_labels = depends_on = None


def upgrade():
    added = set()
    for name in ("planned_hours", "planned_volume_m3"):
        if add_column("work_objects", sa.Column(name, sa.Numeric(14, 2), nullable=True)):
            added.add(name)
    if "planned_hours" in added:
        op.execute(
            "UPDATE work_objects SET planned_hours = planned_quantity WHERE progress_unit = 'hours'"
        )
    if "planned_volume_m3" in added:
        op.execute(
            "UPDATE work_objects SET planned_volume_m3 = planned_quantity "
            "WHERE progress_unit = 'm3'"
        )
    ensure_check(
        "ck_object_planned_hours", "work_objects", "planned_hours IS NULL OR planned_hours > 0"
    )
    ensure_check(
        "ck_object_planned_volume",
        "work_objects",
        "planned_volume_m3 IS NULL OR planned_volume_m3 > 0",
    )


def downgrade():
    op.drop_constraint("ck_object_planned_volume", "work_objects", type_="check")
    op.drop_constraint("ck_object_planned_hours", "work_objects", type_="check")
    op.drop_column("work_objects", "planned_volume_m3")
    op.drop_column("work_objects", "planned_hours")
