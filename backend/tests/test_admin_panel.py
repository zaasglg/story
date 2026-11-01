from uuid import UUID

from conftest import AppContext, login
from sqlalchemy import select

from backend.identity.models import AuditLog, UserRole
from backend.workforce.models import WorkerAssignment


async def test_admin_authorization_editor_and_role_boundaries(context: AppContext) -> None:
    headers = {}
    for role in UserRole:
        tokens = await login(context, role)
        headers[role] = {"Authorization": f"Bearer {tokens['access_token']}"}
    admin = headers[UserRole.ADMIN]
    workers = await context.client.get("/api/v1/admin/workers", headers=admin)
    worker = workers.json()[0]["id"]
    payload = {
        "worker_user_id": worker,
        "valid_from": "2026-10-01",
        "valid_until": "2026-10-31",
        "reference": "Safety induction",
    }
    created = await context.client.post(
        "/api/v1/admin/work-authorizations", headers=admin, json=payload
    )
    assert created.status_code == 201
    endpoint = f"/api/v1/admin/work-authorizations/{created.json()['id']}"
    for role in (UserRole.WORKER, UserRole.MASTER, UserRole.BOSS):
        assert (
            await context.client.get("/api/v1/admin/work-authorizations", headers=headers[role])
        ).status_code == 403
        assert (
            await context.client.patch(endpoint, headers=headers[role], json=payload)
        ).status_code == 403
        assert (
            await context.client.get("/api/v1/admin/masters", headers=headers[role])
        ).status_code == 403
        assert (
            await context.client.delete(
                f"/api/v1/admin/workers/{worker}/assignment", headers=headers[role]
            )
        ).status_code == 403
    masters = await context.client.get("/api/v1/admin/masters", headers=admin)
    assert masters.status_code == 200
    assert masters.json()[0]["role"] == "MASTER"
    assert "phone" not in masters.json()[0]
    listing = await context.client.get("/api/v1/admin/work-authorizations", headers=admin)
    assert listing.json()[0]["id"] == created.json()["id"]
    invalid = await context.client.patch(
        endpoint, headers=admin, json={**payload, "valid_until": "2026-09-01"}
    )
    assert invalid.status_code == 422
    changed = await context.client.patch(
        endpoint, headers=admin, json={**payload, "valid_until": "2026-12-31"}
    )
    assert changed.status_code == 200
    assert changed.json()["valid_until"] == "2026-12-31"
    assert (await context.client.post(endpoint + "/revoke", headers=admin)).status_code == 200
    assert (await context.client.patch(endpoint, headers=admin, json=payload)).status_code == 409
    listing = await context.client.get("/api/v1/admin/work-authorizations", headers=admin)
    assert listing.json()[0]["revoked_at"] is not None
    async with context.database.session_factory() as session:
        audited = await session.scalar(
            select(AuditLog).where(AuditLog.action == "WORK_AUTHORIZATION_UPDATED")
        )
        assert audited is not None


async def test_end_assignment_preserves_history_and_allows_reassignment(
    context: AppContext,
) -> None:
    tokens = await login(context, UserRole.ADMIN)
    admin = {"Authorization": f"Bearer {tokens['access_token']}"}
    workers = (await context.client.get("/api/v1/admin/workers", headers=admin)).json()
    worker = workers[0]["id"]
    city = (await context.client.get("/api/v1/cities", headers=admin)).json()[0]["id"]
    master = (await context.client.get("/api/v1/admin/masters", headers=admin)).json()[0]["id"]
    brigade = await context.client.post(
        "/api/v1/admin/brigades",
        headers=admin,
        json={
            "name": "Panel brigade",
            "city_id": city,
            "master_user_id": master,
        },
    )
    endpoint = f"/api/v1/admin/workers/{worker}/assignment"
    assigned = await context.client.post(
        endpoint,
        headers=admin,
        json={
            "brigade_id": brigade.json()["id"],
        },
    )
    assert assigned.status_code == 200, assigned.text
    replaced = await context.client.post(
        endpoint, headers=admin, json={"brigade_id": brigade.json()["id"]}
    )
    assert replaced.status_code == 200, replaced.text
    assert (await context.client.delete(endpoint, headers=admin)).status_code == 204
    assert (await context.client.delete(endpoint, headers=admin)).status_code == 204
    workers = (await context.client.get("/api/v1/admin/workers", headers=admin)).json()
    assert workers[0]["assignment"] is None
    async with context.database.session_factory() as session:
        original = await session.scalar(
            select(WorkerAssignment).where(WorkerAssignment.worker_user_id == UUID(worker))
        )
        assert original is not None and original.effective_to is not None
    reassigned = await context.client.post(
        endpoint,
        headers=admin,
        json={
            "brigade_id": brigade.json()["id"],
        },
    )
    assert reassigned.status_code == 200, reassigned.text


async def test_worker_editor_clears_optional_data_without_revoking_unchanged_status(
    context: AppContext,
) -> None:
    tokens = await login(context, UserRole.ADMIN)
    worker_tokens = await login(context, UserRole.WORKER)
    admin = {"Authorization": f"Bearer {tokens['access_token']}"}
    worker_auth = {"Authorization": f"Bearer {worker_tokens['access_token']}"}
    workers = (await context.client.get("/api/v1/admin/workers", headers=admin)).json()
    assert workers[0]["city_id"] is not None
    endpoint = f"/api/v1/admin/workers/{workers[0]['id']}"
    changed = await context.client.patch(
        endpoint,
        headers=admin,
        json={
            "status": "ACTIVE",
            "passport_number": "TEST-PASSPORT",
        },
    )
    assert changed.status_code == 200
    assert (await context.client.get("/api/v1/me", headers=worker_auth)).status_code == 200
    cleared = await context.client.patch(
        endpoint,
        headers=admin,
        json={
            "city_id": None,
            "passport_number": None,
        },
    )
    assert cleared.status_code == 200
    assert cleared.json()["city_id"] is None
    assert cleared.json()["city"] is None
    assert cleared.json()["passport_number"] is None
