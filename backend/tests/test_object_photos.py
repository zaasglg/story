from io import BytesIO

from conftest import AppContext, login
from PIL import Image
from sqlalchemy import select

from backend import seed_object_photos
from backend.identity.models import UserRole
from backend.workforce.models import WorkObjectPhoto


async def test_demo_photos_persist_are_idempotent_and_respect_read_permissions(
    context: AppContext, monkeypatch
):
    boss = {"Authorization": f"Bearer {(await login(context, UserRole.BOSS))['access_token']}"}
    city = (await context.client.get("/api/v1/cities", headers=boss)).json()[0]["id"]
    created = await context.client.post(
        "/api/v1/boss/objects",
        headers=boss,
        json={
            "name": "Демо склад",
            "address": "Test",
            "city_id": city,
        },
    )
    assert created.status_code == 201
    assert created.json()["photo_url"] is None
    monkeypatch.setattr(seed_object_photos, "get_settings", lambda: context.settings)
    await seed_object_photos.seed_object_photos()
    await seed_object_photos.seed_object_photos()
    rows = (await context.client.get("/api/v1/boss/objects", headers=boss)).json()
    assert rows[0]["photo_is_demo"] is True
    path = rows[0]["photo_url"]
    async with context.database.session_factory() as session:
        photos = (await session.scalars(select(WorkObjectPhoto))).all()
        assert len(photos) == 1
        content = photos[0].content
    assert content.startswith(b"\x89PNG")
    for role in UserRole:
        tokens = await login(context, role)
        response = await context.client.get(
            "/api/v1" + path,
            headers={
                "Authorization": f"Bearer {tokens['access_token']}",
            },
        )
        assert response.status_code == (200 if role in (UserRole.ADMIN, UserRole.BOSS) else 403)
        if response.status_code == 200:
            assert response.content == content
            assert response.headers["content-type"] == "image/png"
    assert (await context.client.get("/api/v1" + path)).status_code == 401


async def test_boss_can_upload_replace_and_remove_real_object_photo(context: AppContext):
    boss = {"Authorization": f"Bearer {(await login(context, UserRole.BOSS))['access_token']}"}
    city = (await context.client.get("/api/v1/cities", headers=boss)).json()[0]["id"]
    created = await context.client.post(
        "/api/v1/boss/objects",
        headers=boss,
        json={"name": "Фото объект", "address": "Test", "city_id": city},
    )
    object_id = created.json()["id"]
    source = BytesIO()
    Image.new("RGB", (24, 16), "navy").save(source, format="PNG")

    uploaded = await context.client.post(
        f"/api/v1/boss/objects/{object_id}/photo",
        headers=boss,
        files={"file": ("object.png", source.getvalue(), "image/png")},
    )
    assert uploaded.status_code == 201
    assert uploaded.json() == {"photo_url": f"/object-photos/{object_id}"}
    photo = await context.client.get(f"/api/v1/object-photos/{object_id}", headers=boss)
    assert photo.status_code == 200
    assert photo.headers["content-type"] == "image/jpeg"
    assert photo.content.startswith(b"\xff\xd8")
    item = next(
        row
        for row in (await context.client.get("/api/v1/boss/objects", headers=boss)).json()
        if row["id"] == object_id
    )
    assert item["photo_url"] == f"/object-photos/{object_id}"
    assert item["photo_is_demo"] is False

    removed = await context.client.delete(f"/api/v1/boss/objects/{object_id}/photo", headers=boss)
    assert removed.status_code == 204
    assert (
        await context.client.get(f"/api/v1/object-photos/{object_id}", headers=boss)
    ).status_code == 404
