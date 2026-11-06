from conftest import login
from test_equipment_expenses import auth

from backend.identity.models import UserRole


async def test_admin_can_grant_master_authorization_and_catalog_exposes_identity(context):
    admin = auth(await login(context, UserRole.ADMIN))
    await login(context, UserRole.MASTER)
    people = await context.client.get("/api/v1/admin/authorization-employees", headers=admin)
    assert people.status_code == 200
    assert set(row["role"] for row in people.json()) == {"MASTER", "WORKER"}
    master = next(row for row in people.json() if row["role"] == "MASTER")
    granted = await context.client.post(
        "/api/v1/admin/work-authorizations",
        headers=admin,
        json={
            "worker_user_id": master["id"],
            "valid_from": "2026-11-01",
            "valid_until": "2027-11-01",
            "reference": "Master document",
        },
    )
    assert granted.status_code == 201, granted.text
    result = await context.client.get("/api/v1/admin/catalog/work-authorizations", headers=admin)
    row = next(row for row in result.json()["items"] if row["id"] == granted.json()["id"])
    assert row["worker_role"] == "MASTER"
    assert row["worker_photo_url"] == master["photo_url"]
    assert result.json()["labels"][master["id"]] == master["full_name"]
    worker = auth(await login(context, UserRole.WORKER))
    denied = await context.client.get("/api/v1/admin/authorization-employees", headers=worker)
    assert denied.status_code == 403
