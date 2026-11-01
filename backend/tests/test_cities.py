from uuid import UUID, uuid4

from conftest import AppContext, login
from sqlalchemy import select

from backend.identity.models import AuditLog, UserRole


async def test_city_management_permissions_and_shared_directory(context: AppContext) -> None:
    headers = {}
    for role in UserRole:
        tokens = await login(context, role)
        headers[role] = {"Authorization": f"Bearer {tokens['access_token']}"}
    admin = headers[UserRole.ADMIN]
    payload = {"name": "  Almaty  ", "timezone": "Asia/Almaty"}
    created = await context.client.post("/api/v1/admin/cities", headers=admin, json=payload)
    assert created.status_code == 201
    city = created.json()
    assert city["name"] == "Almaty"
    assert city["timezone"] == "Asia/Almaty"
    endpoint = f"/api/v1/admin/cities/{city['id']}"
    for role in (UserRole.WORKER, UserRole.MASTER, UserRole.BOSS):
        auth = headers[role]
        assert (await context.client.get("/api/v1/admin/cities", headers=auth)).status_code == 403
        assert (
            await context.client.get("/api/v1/admin/catalog/cities", headers=auth)
        ).status_code == 403
        assert (
            await context.client.post("/api/v1/admin/cities", headers=auth, json=payload)
        ).status_code == 403
        assert (
            await context.client.patch(endpoint, headers=auth, json={"name": "Changed"})
        ).status_code == 403
        directory = await context.client.get("/api/v1/cities", headers=auth)
        assert directory.status_code == 200
        assert any(row["id"] == city["id"] for row in directory.json())
    listing = await context.client.get(
        "/api/v1/admin/catalog/cities?q=Almaty&sort=name", headers=admin
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == city["id"]
    details = await context.client.get(f"/api/v1/admin/catalog/cities/{city['id']}", headers=admin)
    assert details.status_code == 200
    assert details.json()["item"]["timezone"] == "Asia/Almaty"

    obj = await context.client.post(
        "/api/v1/admin/objects",
        headers=admin,
        json={
            "name": "City test object",
            "address": "Central street",
            "city_id": city["id"],
        },
    )
    assert obj.status_code == 201
    updated = await context.client.patch(endpoint, headers=admin, json={"name": "Алматы"})
    assert updated.status_code == 200
    assert updated.json()["id"] == city["id"]
    assert updated.json()["timezone"] == "Asia/Almaty"
    objects = (await context.client.get("/api/v1/admin/objects", headers=admin)).json()
    assert next(row for row in objects if row["id"] == obj.json()["id"])["city_name"] == "Алматы"
    async with context.database.session_factory() as session:
        actions = (
            await session.scalars(
                select(AuditLog.action).where(AuditLog.entity_id == UUID(city["id"]))
            )
        ).all()
        assert "CITY_CREATED" in actions
        assert "CITY_UPDATED" in actions


async def test_city_validation_and_conflicts(context: AppContext) -> None:
    tokens = await login(context, UserRole.ADMIN)
    admin = {"Authorization": f"Bearer {tokens['access_token']}"}
    for payload in (
        {"name": "   "},
        {"name": "Test", "timezone": "Unknown/Timezone"},
        {"name": "Test", "role": "BOSS"},
    ):
        assert (
            await context.client.post("/api/v1/admin/cities", headers=admin, json=payload)
        ).status_code == 422
    response = await context.client.post(
        "/api/v1/admin/cities", headers=admin, json={"name": "Unique"}
    )
    assert response.status_code == 201
    endpoint = f"/api/v1/admin/cities/{response.json()['id']}"
    duplicate = await context.client.post(
        "/api/v1/admin/cities", headers=admin, json={"name": " unique "}
    )
    assert duplicate.status_code == 409
    for payload in ({}, {"name": None}, {"name": " "}, {"timezone": "Invalid"}, {"timezone": None}):
        assert (
            await context.client.patch(endpoint, headers=admin, json=payload)
        ).status_code == 422
    assert (
        await context.client.patch(endpoint, headers=admin, json={"name": "Lipetsk"})
    ).status_code == 409
    assert (
        await context.client.patch(
            f"/api/v1/admin/cities/{uuid4()}", headers=admin, json={"name": "Missing"}
        )
    ).status_code == 404
