import importlib.util
import json
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from conftest import login
from sqlalchemy import select

from backend.identity.models import UserRole
from backend.workforce.models import ProfileChangeRequest


async def test_passport_request_encrypted_retry_review_and_read(context):
    worker = await login(context, UserRole.WORKER)
    admin = await login(context, UserRole.ADMIN)
    wh = {"Authorization": f"Bearer {worker['access_token']}"}
    ah = {"Authorization": f"Bearer {admin['access_token']}"}
    payload = {"passport_number": "SYNTHETIC-D01-ONLY", "reason": "Test correction"}
    response = await context.client.post(
        "/api/v1/workers/me/change-request", headers=wh, json=payload
    )
    assert response.status_code == 201
    rid = response.json()["id"]
    async with context.database.session_factory() as session:
        row = await session.scalar(select(ProfileChangeRequest))
        assert payload["passport_number"] not in json.dumps(row.changes)
    replay = await context.client.post(
        "/api/v1/workers/me/change-request", headers=wh, json=payload
    )
    assert replay.json()["id"] == rid
    for path, headers in [("/workers/me/change-requests", wh), ("/admin/change-requests", ah)]:
        read = await context.client.get("/api/v1" + path, headers=headers)
        assert read.json()[0]["changes"]["passport_number"] == payload["passport_number"]
    catalog = await context.client.get("/api/v1/admin/catalog/change-requests", headers=ah)
    assert catalog.json()["items"][0]["changes"]["passport_number"] == payload["passport_number"]
    decided = await context.client.post(
        f"/api/v1/admin/change-requests/{rid}/decision", headers=ah, json={"approve": True}
    )
    assert decided.status_code == 200
    profile = await context.client.get("/api/v1/workers/me", headers=wh)
    assert profile.json()["passport_number"] == payload["passport_number"]


async def test_existing_profile_requests_are_encrypted_without_deleting_history(context):
    worker = await login(context, UserRole.WORKER)
    headers = {"Authorization": f"Bearer {worker['access_token']}"}
    await context.client.post(
        "/api/v1/workers/me/change-request", headers=headers, json={"full_name": "Synthetic"}
    )
    async with context.database.session_factory() as session:
        row = await session.scalar(select(ProfileChangeRequest))
        row.changes = {"passport_number": "SYNTHETIC-LEGACY", "full_name": "Synthetic"}
        rid, created = row.id, row.created_at
        await session.commit()
    path = Path(__file__).parents[1] / "alembic/versions/20261101_0028_profile_request_pii.py"
    spec = importlib.util.spec_from_file_location("pii_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def apply(connection):
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()

    for _ in range(2):
        async with context.database.engine.begin() as connection:
            await connection.run_sync(apply)
    async with context.database.session_factory() as session:
        row = await session.get(ProfileChangeRequest, rid)
        assert "SYNTHETIC-LEGACY" not in json.dumps(row.changes)
        assert row.created_at == created
        assert row.changes["full_name"] == "Synthetic"
