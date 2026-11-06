from uuid import UUID

from conftest import AppContext, login
from sqlalchemy import select

from backend.identity.models import AccountStatus, User, UserRole


async def test_object_dates_master_roundtrip_and_partial_validation(context: AppContext):
    boss = {"Authorization": f"Bearer {(await login(context, UserRole.BOSS))['access_token']}"}
    city = (await context.client.get("/api/v1/cities", headers=boss)).json()[0]["id"]
    masters = await context.client.get("/api/v1/boss/masters", headers=boss)
    assert masters.status_code == 200
    master = masters.json()[0]
    payload = {
        "name": "Scheduled object",
        "city_id": city,
        "address": "Test address",
        "start_date": "2026-11-01",
        "estimated_end_date": "2026-12-31",
        "responsible_master_user_id": master["id"],
    }
    endpoint = "/api/v1/boss/objects"
    created = await context.client.post(endpoint, headers=boss, json=payload)
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["start_date"] == "2026-11-01"
    assert item["estimated_end_date"] == "2026-12-31"
    assert item["responsible_master_name"] == master["full_name"]
    catalog = await context.client.get("/api/v1/admin/catalog/objects/" + item["id"], headers=boss)
    assert catalog.status_code == 200
    assert catalog.json()["item"]["responsible_master_user_id"] == master["id"]
    invalid = await context.client.patch(
        endpoint + "/" + item["id"], headers=boss, json={"estimated_end_date": "2026-10-01"}
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_object_dates"
    clear = await context.client.patch(
        endpoint + "/" + item["id"],
        headers=boss,
        json={"responsible_master_user_id": None, "start_date": None, "estimated_end_date": None},
    )
    assert clear.status_code == 200
    assert clear.json()["responsible_master_name"] is None
    assert clear.json()["start_date"] is None
    bad_dates = await context.client.post(
        endpoint, headers=boss, json={**payload, "estimated_end_date": "2026-10-01"}
    )
    assert bad_dates.status_code == 422
    async with context.database.session_factory() as session:
        for role in (UserRole.WORKER, UserRole.ADMIN, UserRole.BOSS):
            user = await session.scalar(select(User).where(User.role == role))
            response = await context.client.patch(
                endpoint + "/" + item["id"],
                headers=boss,
                json={"responsible_master_user_id": str(user.id)},
            )
            assert response.status_code == 422
        user = await session.get(User, UUID(master["id"]))
        user.status = AccountStatus.BLOCKED
        await session.commit()
    blocked = await context.client.patch(
        endpoint + "/" + item["id"], headers=boss, json={"responsible_master_user_id": master["id"]}
    )
    assert blocked.status_code == 422


async def test_admin_cannot_assign_master_or_change_schedule(context: AppContext):
    boss = {"Authorization": f"Bearer {(await login(context, UserRole.BOSS))['access_token']}"}
    admin = {"Authorization": f"Bearer {(await login(context, UserRole.ADMIN))['access_token']}"}
    city = (await context.client.get("/api/v1/cities", headers=boss)).json()[0]["id"]
    item = (
        await context.client.post(
            "/api/v1/boss/objects",
            headers=boss,
            json={"name": "Read only", "city_id": city, "address": "Test"},
        )
    ).json()
    for prefix in ("admin", "boss"):
        assert (
            await context.client.patch(
                f"/api/v1/{prefix}/objects/{item['id']}",
                headers=admin,
                json={"start_date": "2026-11-01"},
            )
        ).status_code == 403
    assert (await context.client.get("/api/v1/boss/masters", headers=admin)).status_code == 403
    listing = (await context.client.get("/api/v1/admin/objects", headers=admin)).json()
    assert listing[0]["start_date"] is None
