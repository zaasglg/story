"""Store the unit and planned quantity for object completion."""

import sqlalchemy as sa

from alembic import op
from backend.core.migration_schema import add_column, ensure_check

revision = "20261104_0040"
down_revision = "20261104_0039"
branch_labels = depends_on = None


def upgrade():
    add_column("work_objects", sa.Column("progress_unit", sa.String(8), nullable=True))
    add_column("work_objects", sa.Column("planned_quantity", sa.Numeric(14, 2), nullable=True))
    ensure_check(
        "ck_work_objects_progress_plan",
        "work_objects",
        "(progress_unit IS NULL AND planned_quantity IS NULL) OR "
        "(progress_unit IS NOT NULL AND planned_quantity IS NOT NULL AND "
        "progress_unit IN ('hours', 'm3') AND planned_quantity > 0)",
    )


def downgrade():
    op.drop_constraint("ck_work_objects_progress_plan", "work_objects", type_="check")
    op.drop_column("work_objects", "planned_quantity")
    op.drop_column("work_objects", "progress_unit")
