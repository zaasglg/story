"""Reproduce feature tables existing ahead of the recorded Alembic revision."""

import asyncio
import os
import sys
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.engine import make_url


@pytest.mark.skipif(
    not os.environ.get("M1917_TEST_POSTGRES_URL"), reason="Dedicated PostgreSQL server required"
)
@pytest.mark.parametrize("schema_state", ["complete", "partial", "incompatible"])
async def test_existing_feature_schema_is_validated_and_data_preserved(schema_state):
    server = make_url(os.environ["M1917_TEST_POSTGRES_URL"])
    admin = await asyncpg.connect(
        server.set(drivername="postgresql").render_as_string(hide_password=False)
    )
    name = "feature_reconciliation_" + uuid4().hex
    await admin.execute(f'CREATE DATABASE "{name}"')
    url = server.set(drivername="postgresql+asyncpg", database=name).render_as_string(
        hide_password=False
    )

    async def upgrade():
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            "head",
            env={**os.environ, "M1917_DATABASE_URL": url},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await process.communicate()
        return process.returncode, output.decode()

    connection = None
    try:
        status, output = await upgrade()
        assert status == 0, output
        connection = await asyncpg.connect(
            server.set(drivername="postgresql", database=name).render_as_string(hide_password=False)
        )
        city_id, object_id, worker_id = [uuid4() for _ in range(3)]
        await connection.execute(
            "INSERT INTO cities(id,name,timezone,created_at) "
            "VALUES($1,'Synthetic','Europe/Moscow',CURRENT_TIMESTAMP)",
            city_id,
        )
        await connection.execute(
            "INSERT INTO users(id,phone,role,status,auth_version,created_at,updated_at) "
            "VALUES($1,'+77075550888','WORKER','ACTIVE',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            worker_id,
        )
        await connection.execute(
            "INSERT INTO work_objects(id,name,city_id,address,status,created_at,updated_at,"
            "photo_url,photo_is_demo,progress_unit,planned_quantity,"
            "planned_hours,planned_volume_m3) "
            "VALUES($1,'Synthetic',$2,'Synthetic','PLANNED',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,"
            "'/existing-photo',false,'hours',100,17,23)",
            object_id,
            city_id,
        )
        await connection.execute(
            "INSERT INTO work_object_photos(object_id,content,created_at) "
            "VALUES($1,$2,CURRENT_TIMESTAMP)",
            object_id,
            b"existing-photo-bytes",
        )
        await connection.execute(
            "INSERT INTO employee_permits(worker_user_id,code,granted,updated_at) "
            "VALUES($1,'HEIGHT',false,CURRENT_TIMESTAMP)",
            worker_id,
        )
        if schema_state == "partial":
            await connection.execute("DROP TABLE finance_receipts")
            await connection.execute("ALTER TABLE financial_requests DROP COLUMN evidence_url")
        elif schema_state == "incompatible":
            await connection.execute(
                "ALTER TABLE work_objects ALTER COLUMN photo_url TYPE integer USING 1"
            )
        tables = ("work_objects", "work_object_photos", "employee_permits")
        before = {table: await connection.fetch(f"SELECT * FROM {table}") for table in tables}
        # Simulate the server: feature DDL exists, only the news branch is recorded.
        await connection.execute("UPDATE alembic_version SET version_num='20261106_0048'")
        status, output = await upgrade()
        if schema_state == "incompatible":
            assert status != 0
            assert "Incompatible existing column work_objects.photo_url" in output
            expected_revision = "20261106_0048"
        else:
            assert status == 0, output
            expected_revision = "20261106_0049"
            # Rerunning a completed upgrade is also harmless.
            status, output = await upgrade()
            assert status == 0, output
        assert (
            await connection.fetchval("SELECT version_num FROM alembic_version")
            == expected_revision
        )
        for table in tables:
            assert await connection.fetch(f"SELECT * FROM {table}") == before[table]
    finally:
        if connection is not None:
            await connection.close()
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()
