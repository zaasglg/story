from uuid import uuid4

import pytest
from conftest import AppContext, login

from backend.identity.models import UserRole


@pytest.mark.parametrize("role", list(UserRole))
async def test_object_write_permissions_on_both_routes(context: AppContext, role: UserRole):
    boss = {"Authorization": f"Bearer {(await login(context, UserRole.BOSS))['access_token']}"}
    headers = {"Authorization": f"Bearer {(await login(context, role))['access_token']}"}
    city = (await context.client.get("/api/v1/cities", headers=boss)).json()[0]["id"]
    for prefix in ("admin", "boss"):
        endpoint = f"/api/v1/{prefix}/objects"
        created = await context.client.post(
            endpoint,
            headers=headers,
            json={"name": "Crew", "city_id": city, "address": "Test address"},
        )
        assert created.status_code == (201 if role == UserRole.BOSS else 403)
        item_id = created.json()["id"] if role == UserRole.BOSS else str(uuid4())
        updated = await context.client.patch(
            f"{endpoint}/{item_id}", headers=headers, json={"name": "Updated crew"}
        )
        assert updated.status_code == (200 if role == UserRole.BOSS else 403)
        listing = await context.client.get(endpoint, headers=headers)
        assert listing.status_code == (200 if role in (UserRole.ADMIN, UserRole.BOSS) else 403)
        deleted = await context.client.delete(f"{endpoint}/{item_id}", headers=headers)
        assert deleted.status_code == (204 if role == UserRole.BOSS else 403)
