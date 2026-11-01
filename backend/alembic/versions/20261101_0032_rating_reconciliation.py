"""Append reversals for historical invalid attendance awards; never remove events.

Schema-local Core tables deliberately avoid importing evolving runtime ORM models.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from alembic import op

revision = "20261101_0032"
down_revision = "20261101_0031"
branch_labels = depends_on = None


def upgrade():
    connection = op.get_bind()
    metadata = sa.MetaData()

    def table(name):
        # SQLite reflects UUID storage as CHAR; keep UUID processors explicitly
        # so migrated source keys and inserts match PostgreSQL and ORM semantics.
        uuid_names = {
            "id",
            "worker_user_id",
            "created_by",
            "actor_user_id",
            "entity_id",
            "brigade_id",
            "object_id",
            "city_id",
            "user_id",
            "master_user_id",
            "updated_by",
        }
        inspector = sa.inspect(connection)
        primary = set(inspector.get_pk_constraint(name)["constrained_columns"])
        overrides = [
            sa.Column(column["name"], sa.Uuid(), primary_key=column["name"] in primary)
            for column in inspector.get_columns(name)
            if column["name"] in uuid_names
        ]
        return sa.Table(name, metadata, *overrides, autoload_with=connection, extend_existing=True)

    rating, attendance, audit = (
        table("rating_events"),
        table("attendance_records"),
        table("audit_logs"),
    )
    assignment, objects, cities, profiles = (
        table(name)
        for name in ("worker_assignments", "work_objects", "cities", "employee_profiles")
    )
    now = datetime.now(UTC)
    workers = (
        connection.execute(
            sa.select(rating.c.worker_user_id)
            .where(rating.c.event_type.in_(["ABSENCE", "STREAK_14"]))
            .distinct()
        )
        .scalars()
        .all()
    )
    for wid in workers:
        events = [
            SimpleNamespace(**dict(row))
            for row in connection.execute(
                sa.select(rating).where(rating.c.worker_user_id == wid)
            ).mappings()
        ]
        cancelled = {
            event.source_key.removeprefix("REVERSE:")
            for event in events
            if event.event_type == "ATTENDANCE_REVERSED" and event.source_key.startswith("REVERSE:")
        }
        zone = connection.execute(
            sa.select(cities.c.timezone)
            .select_from(
                assignment.join(objects, objects.c.id == assignment.c.object_id).join(
                    cities, cities.c.id == objects.c.city_id
                )
            )
            .where(
                assignment.c.worker_user_id == wid,
                assignment.c.effective_to.is_(None),
                assignment.c.archived_at.is_(None),
            )
        ).scalar()
        if zone is None:
            zone = connection.execute(
                sa.select(cities.c.timezone)
                .select_from(cities.join(profiles, profiles.c.city_id == cities.c.id))
                .where(profiles.c.user_id == wid)
            ).scalar()
        today = now.astimezone(ZoneInfo(zone or "Europe/Moscow")).date()
        rows = connection.execute(
            sa.select(attendance)
            .where(
                attendance.c.worker_user_id == wid,
                attendance.c.archived_at.is_(None),
                attendance.c.work_date <= today,
            )
            .order_by(attendance.c.work_date)
        ).mappings()
        desired, streak, previous = set(), 0, None
        for row in rows:
            if row["status"] == "ABSENT":
                desired.add(("ABSENCE", row["work_date"]))
            if row["status"] not in ("PRESENT", "LATE"):
                streak, previous = 0, None
                continue
            streak = (
                streak + 1 if previous and row["work_date"] == previous + timedelta(days=1) else 1
            )
            previous = row["work_date"]
            if streak % 14 == 0:
                desired.add(("STREAK_14", row["work_date"]))
        for event in events:
            if (
                event.event_type not in ("ABSENCE", "STREAK_14")
                or str(event.id) in cancelled
                or (event.event_type, event.event_date) in desired
            ):
                continue
            connection.execute(
                rating.insert().values(
                    id=uuid4(),
                    worker_user_id=wid,
                    event_date=event.event_date,
                    event_type="ATTENDANCE_REVERSED",
                    delta=-Decimal(str(event.delta)),
                    reason="Historical attendance correction",
                    source_key=f"REVERSE:{event.id}",
                    created_by=event.created_by,
                    created_at=now,
                )
            )
            connection.execute(
                audit.insert().values(
                    id=uuid4(),
                    actor_user_id=None,
                    action="RATING_EVENT_REVERSED",
                    entity_type="rating_event",
                    entity_id=event.id,
                    created_at=now,
                    succeeded=True,
                    new_value={
                        "reason": "historical_attendance_reconciliation",
                        "source": revision,
                    },
                )
            )


def downgrade():
    raise RuntimeError("Append-only rating reconciliation cannot be downgraded by deleting history")
