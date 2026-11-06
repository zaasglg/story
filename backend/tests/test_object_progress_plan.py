from conftest import AppContext, login

from backend.identity.models import UserRole


async def test_object_progress_plan_roundtrip(context: AppContext):
    headers = {"Authorization": f"Bearer {(await login(context, UserRole.BOSS))['access_token']}"}
    city = (await context.client.get("/api/v1/cities", headers=headers)).json()[0]["id"]
    endpoint = "/api/v1/boss/objects"
    payload = dict(
        name="Plan", city_id=city, address="Test", progress_unit="hours", planned_quantity="500"
    )
    created = await context.client.post(endpoint, headers=headers, json=payload)
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["progress_unit"] == "hours"
    assert float(item["planned_quantity"]) == 500
    detail = endpoint + "/" + item["id"]
    changed = await context.client.patch(
        detail,
        headers=headers,
        json={
            "progress_unit": "m3",
            "planned_quantity": "750.25",
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["progress_unit"] == "m3"
    assert float(changed.json()["planned_quantity"]) == 750.25
    unchanged = await context.client.patch(detail, headers=headers, json={"name": "Updated"})
    assert unchanged.json()["progress_unit"] == "m3"
    catalog = await context.client.get(
        "/api/v1/admin/catalog/objects/" + item["id"], headers=headers
    )
    assert float(catalog.json()["item"]["planned_quantity"]) == 750.25
    for invalid in [
        {"progress_unit": "days", "planned_quantity": 500},
        {"progress_unit": "hours"},
        {"planned_quantity": 500},
        {"progress_unit": None, "planned_quantity": 500},
        *[
            {"progress_unit": "m3", "planned_quantity": value}
            for value in [0, -1, "NaN", "Infinity", "1.234"]
        ],
    ]:
        response = await context.client.patch(detail, headers=headers, json=invalid)
        assert response.status_code == 422, response.text


async def test_independent_hour_and_volume_plans(context: AppContext):
    headers = {"Authorization": f"Bearer {(await login(context, UserRole.BOSS))['access_token']}"}
    city = (await context.client.get("/api/v1/cities", headers=headers)).json()[0]["id"]
    endpoint = "/api/v1/boss/objects"
    response = await context.client.post(
        endpoint,
        headers=headers,
        json={
            "name": "Dual plan",
            "address": "Test",
            "city_id": city,
            "planned_hours": "500",
            "planned_volume_m3": "750.25",
        },
    )
    assert response.status_code == 201, response.text
    item = response.json()
    assert float(item["planned_hours"]) == 500
    assert float(item["planned_volume_m3"]) == 750.25
    detail = endpoint + "/" + item["id"]
    response = await context.client.patch(detail, headers=headers, json={"planned_hours": None})
    assert response.status_code == 200
    assert response.json()["planned_hours"] is None
    assert float(response.json()["planned_volume_m3"]) == 750.25
    for field in ["planned_hours", "planned_volume_m3"]:
        for value in [0, -10, "NaN", "1.234"]:
            response = await context.client.patch(detail, headers=headers, json={field: value})
            assert response.status_code == 422
