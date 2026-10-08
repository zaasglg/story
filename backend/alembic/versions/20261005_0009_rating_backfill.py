"""Preserve previously recorded absences in permanent rating history."""

from alembic import op

revision = "20261005_0009"
down_revision = "20261005_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""INSERT INTO rating_events
        (id, worker_user_id, event_date, event_type, delta, reason,
         source_key, created_by, created_at)
        SELECT gen_random_uuid(), worker_user_id, work_date, 'ABSENCE', -0.1,
        'Recorded absence', 'ABSENCE:' || work_date::text, updated_by, CURRENT_TIMESTAMP
        FROM attendance_records WHERE status = 'ABSENT'
        ON CONFLICT (worker_user_id, source_key) DO NOTHING""")


def downgrade() -> None:
    # Permanent violation history must survive a downgrade of the backfill revision.
    pass
