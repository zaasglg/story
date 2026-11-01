"""Upgrade a populated, disposable 0027 database; never target a working database."""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.engine import make_url


@pytest.mark.skipif(
    not os.environ.get("M1917_TEST_POSTGRES_URL"), reason="Dedicated PostgreSQL server required"
)
async def test_defect_migrations_preserve_history_and_financial_values():
    server = make_url(os.environ["M1917_TEST_POSTGRES_URL"])
    admin = await asyncpg.connect(
        server.set(drivername="postgresql").render_as_string(hide_password=False)
    )
    name = "defect_migrations_" + uuid4().hex
    await admin.execute(f'CREATE DATABASE "{name}"')
    url = server.set(drivername="postgresql+asyncpg", database=name).render_as_string(
        hide_password=False
    )

    async def migrate(revision):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            revision,
            env={**os.environ, "M1917_DATABASE_URL": url},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output = await process.communicate()
        assert process.returncode == 0, output[0].decode()

    connection = None
    try:
        await migrate("20261101_0027")
        connection = await asyncpg.connect(
            server.set(drivername="postgresql", database=name).render_as_string(hide_password=False)
        )
        uid, cid, rid, event_id, request_id, entry_id, expense_id = [uuid4() for _ in range(7)]
        now = datetime.now(UTC)
        await connection.execute(
            "INSERT INTO users(id, phone, role, status, auth_version, created_at, "
            "updated_at) VALUES($1, '+77075550888', 'WORKER', 'ACTIVE', 0, $2, $2) ",
            uid,
            now,
        )
        await connection.execute(
            "INSERT INTO cities(id, name, timezone, created_at) VALUES($1, 'Synthetic', "
            "'Europe/Moscow', $2) ",
            cid,
            now,
        )
        await connection.execute(
            "INSERT INTO employee_profiles(id, user_id, full_name, employee_id, city_id, "
            "created_at, updated_at) VALUES($1, $2, 'Synthetic', 'MIGRATION-ONLY', $3, $4, "
            "$4) ",
            uuid4(),
            uid,
            cid,
            now,
        )
        await connection.execute(
            "INSERT INTO profile_change_requests(id, worker_user_id, changes, status, "
            "created_at, updated_at) VALUES($1, $2, $3, 'REJECTED', $4, $4) ",
            rid,
            uid,
            json.dumps(
                {"passport_number": "SYNTHETIC-MIGRATION-PASSPORT", "full_name": "Synthetic"}
            ),
            now,
        )
        await connection.execute(
            "INSERT INTO rating_events(id, worker_user_id, event_date, event_type, delta, "
            "reason, source_key, created_by, created_at) VALUES($1, $2, '2026-01-01', "
            "'ABSENCE', -.1, 'Synthetic', 'ABSENCE:2026-01-01', $2, $3) ",
            event_id,
            uid,
            now,
        )
        await connection.execute(
            "INSERT INTO financial_requests(id, worker_user_id, type, amount, currency, "
            "period, reason, status, idempotency_key, created_by, created_at) VALUES($1, "
            "$2, 'BONUS', 123, 'RUB', '2026-01-01', 'Synthetic', 'APPROVED', "
            "'migration-only', $2, $3) ",
            request_id,
            uid,
            now,
        )
        await connection.execute(
            "INSERT INTO ledger_entries(id, request_id, worker_user_id, type, amount, "
            "currency, period, created_at) VALUES($1, $2, $3, 'BONUS', 123, 'RUB', "
            "'2026-01-01', $4) ",
            entry_id,
            request_id,
            uid,
            now,
        )
        await connection.execute(
            "INSERT INTO expenses(id, city_id, expense_date, amount, currency, category, "
            "status, created_by, updated_by, created_at, updated_at) VALUES($1, $2, "
            "'2026-01-01', 456, 'RUB', 'salary', 'APPROVED', $3, $3, $4, $4) ",
            expense_id,
            cid,
            uid,
            now,
        )
        financial_before = await connection.fetch("SELECT id,amount,currency,status FROM expenses")
        ledger_before = await connection.fetch("SELECT id,amount,currency FROM ledger_entries")
        await migrate("20261101_0032")
        changes = json.loads(
            await connection.fetchval(
                "SELECT changes FROM profile_change_requests WHERE id=$1", rid
            )
        )
        assert "SYNTHETIC-MIGRATION-PASSPORT" not in json.dumps(changes)
        assert changes["full_name"] == "Synthetic"
        assert (
            await connection.fetchval("SELECT status FROM profile_change_requests WHERE id=$1", rid)
        ) == "REJECTED"
        assert (
            await connection.fetch("SELECT id,amount,currency,status FROM expenses")
            == financial_before
        )
        assert (
            await connection.fetch("SELECT id,amount,currency FROM ledger_entries") == ledger_before
        )
        assert (
            await connection.fetchval(
                "SELECT cost_integrated FROM ledger_entries WHERE id=$1", entry_id
            )
            is False
        )
        assert await connection.fetchval("SELECT count(*) FROM rating_events") == 2
        assert (
            await connection.fetchval(
                "SELECT actor_user_id FROM audit_logs WHERE action='RATING_EVENT_REVERSED'"
            )
            is None
        )
        assert (
            await connection.fetchval("SELECT count(*) FROM rating_events WHERE id=$1", event_id)
            == 1
        )
        assert (
            await connection.fetchval("SELECT version_num FROM alembic_version") == "20261101_0032"
        )
        await migrate("20261101_0032")
        assert await connection.fetchval("SELECT count(*) FROM rating_events") == 2
        # The new revisions must also upgrade the populated old database without
        # altering original monetary values or inventing historical rating snapshots.
        await migrate("head")
        assert (
            await connection.fetchval("SELECT version_num FROM alembic_version") == "20261101_0037"
        )
        assert (
            await connection.fetch("SELECT id,amount,currency,status FROM expenses")
            == financial_before
        )
        assert (
            await connection.fetch("SELECT id,amount,currency FROM ledger_entries") == ledger_before
        )
        assert (
            await connection.fetchval(
                "SELECT rating_before FROM rating_events WHERE id=$1", event_id
            )
            is None
        )
        assert (
            await connection.fetchval("SELECT revision FROM expenses WHERE id=$1", expense_id) == 0
        )
        await migrate("head")
        # Exercise runtime against migrated PostgreSQL including append-only triggers,
        # not just metadata.create_all schemas used by isolated API fixtures.
        from decimal import Decimal

        from backend.access.models import RatingEvent
        from backend.access.rating import append_rating_event
        from backend.core.database import Database

        database = Database(url)
        try:
            async with database.session_factory() as session:
                event = RatingEvent(
                    worker_user_id=uid,
                    event_date=now.date(),
                    event_type="NO_GLASSES",
                    delta=Decimal("-0.2"),
                    reason="Synthetic trigger test",
                    source_key="synthetic-trigger-event",
                    created_by=uid,
                    created_at=now,
                )
                await append_rating_event(session, event)
                await session.commit()
                assert event.rating_before == Decimal("5")
                assert event.rating_after == Decimal("4.8")
        finally:
            await database.dispose()
    finally:
        if connection:
            await connection.close()
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()
