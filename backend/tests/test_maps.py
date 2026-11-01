import httpx
from conftest import login

from backend.core.config import Settings
from backend.identity.models import UserRole
from backend.workforce.maps import AddressGeocoder


async def test_geocoder_normalizes_and_caches():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"features": [{
            "geometry": {"coordinates": [39.6, 52.6]},
            "properties": {"city": "Липецк", "street": "Строителей", "housenumber": "10"},
        }]})

    service = AddressGeocoder(Settings())
    await service.client.aclose()
    service.client = httpx.AsyncClient(
        base_url="https://example.test", transport=httpx.MockTransport(respond)
    )
    try:
        result = await service.lookup("/api/", {"q": "Липецк"})
        assert result == [{"address": "Липецк, Строителей 10", "latitude": 52.6,
                           "longitude": 39.6}]
        assert await service.lookup("/api/", {"q": "Липецк"}) == result
        assert len(calls) == 1
    finally:
        await service.client.aclose()


async def test_map_configuration_permissions(context):
    for role in UserRole:
        tokens = await login(context, role)
        response = await context.client.get("/api/v1/admin/maps/config", headers={
            "Authorization": f"Bearer {tokens['access_token']}"})
        assert response.status_code == (200 if role == UserRole.ADMIN else 403)


async def test_object_coordinates_roundtrip_and_address_change(context):
    tokens = await login(context, UserRole.ADMIN)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    cities = await context.client.get("/api/v1/cities", headers=headers)
    response = await context.client.post("/api/v1/admin/objects", headers=headers, json={
        "name": "Объект на карте", "city_id": cities.json()[0]["id"],
        "address": "Липецк, Строителей, 10", "latitude": 52.6, "longitude": 39.6,
    })
    assert response.status_code == 201, response.text
    url = f"/api/v1/admin/objects/{response.json()['id']}"
    assert response.json()["latitude"] == 52.6
    unchanged = await context.client.patch(url, headers=headers, json={"name": "Новое имя"})
    assert unchanged.json()["longitude"] == 39.6
    for invalid in ({"latitude": 12}, {"latitude": 91, "longitude": 12}):
        assert (await context.client.patch(url, headers=headers, json=invalid)).status_code == 422
    changed = await context.client.patch(url, headers=headers, json={"address": "Другой адрес"})
    assert changed.status_code == 200
    assert changed.json()["latitude"] is None
    assert changed.json()["longitude"] is None
