from conftest import login
from test_operations_api import auth, prepare_assignment

from backend.identity.models import UserRole


async def test_contacts_crud_search_archive_and_role_boundaries(context):
    headers = {role: auth(await login(context, role)) for role in UserRole}
    data = {
        "name": "Тестовый контакт",
        "position": "Инженер",
        "phone": "+77075551234",
        "department": "Строительство",
        "email": "synthetic@example.invalid",
        "active": True,
    }
    path = "/api/v1/organization/contacts"
    for role in (UserRole.WORKER, UserRole.MASTER, UserRole.BOSS):
        assert (
            await context.client.post(path, headers=headers[role], json=data)
        ).status_code == 403
    created = await context.client.post(path, headers=headers[UserRole.ADMIN], json=data)
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]
    for role in UserRole:
        result = await context.client.get(path, headers=headers[role], params={"q": "Инженер"})
        assert len(result.json()) == 1
    updated = await context.client.put(
        path + "/" + item_id, headers=headers[UserRole.ADMIN], json={**data, "active": False}
    )
    assert updated.status_code == 200
    assert (await context.client.get(path, headers=headers[UserRole.WORKER])).json() == []
    assert len((await context.client.get(path, headers=headers[UserRole.BOSS])).json()) == 1
    assert (
        await context.client.delete(path + "/" + item_id, headers=headers[UserRole.ADMIN])
    ).status_code == 204
    assert (await context.client.get(path, headers=headers[UserRole.ADMIN])).json() == []
    assert (await context.client.get(path)).status_code == 401


async def test_public_directory_filters_profile_no_private_data_and_master_scope(context):
    admin = await login(context, UserRole.ADMIN)
    wid, _, bid, oid = await prepare_assignment(context, admin)
    cid = (await context.client.get("/api/v1/cities", headers=auth(admin))).json()[0]["id"]
    worker = auth(await login(context, UserRole.WORKER))
    master = auth(await login(context, UserRole.MASTER))
    response = await context.client.patch(
        "/api/v1/admin/workers/" + wid, headers=auth(admin), json={"position": "Монтажник"}
    )
    assert response.status_code == 200, response.text
    second = await context.client.post(
        "/api/v1/admin/workers",
        headers=auth(admin),
        json={
            "phone": "+77075551238",
            "full_name": "Другой Работник",
            "employee_id": "PUBLIC-OTHER",
            "passport_number": "SYNTHETIC-SECRET",
            "position": "Крановщик",
        },
    )
    assert second.status_code == 201, second.text
    path = "/api/v1/directory/workers"
    page = await context.client.get(
        path,
        headers=worker,
        params={"city_id": cid, "brigade_id": bid, "position": "Монтажник", "limit": 1},
    )
    assert page.status_code == 200, page.text
    assert [i["id"] for i in page.json()["items"]] == [wid]
    public = await context.client.get(path + "/" + second.json()["id"], headers=worker)
    assert public.status_code == 200, public.text
    assert public.json()["position"] == "Крановщик"
    assert "rating_history" in public.json() and "bonus_history" in public.json()
    forbidden = {
        "phone",
        "passport_number",
        "iban",
        "salary",
        "amount",
        "advance",
        "m3",
        "documents",
    }

    def keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(v) for v in value.values()), set())
        if isinstance(value, list):
            return set().union(*(keys(v) for v in value), set())
        return set()

    assert not (keys(public.json()) & forbidden)
    assert "SYNTHETIC-SECRET" not in public.text and "+77075551238" not in public.text
    assert (
        await context.client.get(path + "/" + second.json()["id"], headers=master)
    ).status_code == 404
    options = await context.client.get(path + "/options", headers=master)
    assert options.status_code == 200 and "Крановщик" not in options.text
    assert (await context.client.get(path)).status_code == 401
