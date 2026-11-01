from io import BytesIO
from uuid import uuid4

import pytest
from conftest import AppContext, login
from PIL import Image
from sqlalchemy import select
from test_employee_photos import picture
from test_equipment_expenses import auth

from backend.identity.models import User, UserRole


@pytest.mark.parametrize("role", list(UserRole))
async def test_equipment_photo_upload_roles(context: AppContext, role):
    response = await context.client.post(
        "/api/v1/admin/equipment-photos",
        headers=auth(await login(context, role)),
        files={"file": ("helmet.png", picture(), "image/png")},
    )
    assert response.status_code == (201 if role == UserRole.ADMIN else 403)


async def test_photo_lifecycle_visibility_and_catalog(context: AppContext):
    admin = auth(await login(context, UserRole.ADMIN))
    worker = auth(await login(context, UserRole.WORKER))
    uploaded = await context.client.post(
        "/api/v1/admin/equipment-photos",
        headers=admin,
        files={"file": ("helmet.png", picture(), "image/png")},
    )
    assert uploaded.status_code == 201
    photo_url = uploaded.json()["photo_url"]
    loaded = await context.client.get("/api/v1" + photo_url, headers=admin)
    assert loaded.status_code == 200
    assert loaded.headers["content-type"] == "image/jpeg"
    assert b"PRIVATE-METADATA" not in loaded.content
    with Image.open(BytesIO(loaded.content)) as image:
        assert image.size == (768, 576)
    assert (await context.client.get("/api/v1" + photo_url)).status_code == 401
    assert (await context.client.get("/api/v1" + photo_url, headers=worker)).status_code == 404
    boss = auth(await login(context, UserRole.BOSS))
    master = auth(await login(context, UserRole.MASTER))
    assert (await context.client.get("/api/v1" + photo_url, headers=boss)).status_code == 200
    assert (await context.client.get("/api/v1" + photo_url, headers=master)).status_code == 403
    created = await context.client.post(
        "/api/v1/admin/equipment",
        headers=admin,
        json={"name": "Helmet with photo", "quantity": 3, "photo_url": photo_url},
    )
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]
    assert created.json()["photo_url"] == photo_url
    renamed = await context.client.patch(
        f"/api/v1/admin/equipment/{item_id}", headers=admin, json={"name": "Renamed helmet"}
    )
    assert renamed.json()["photo_url"] == photo_url
    catalog = await context.client.get("/api/v1/admin/catalog/equipment", headers=admin)
    assert catalog.json()["items"][0]["photo_url"] == photo_url
    async with context.database.session_factory() as session:
        worker_id = await session.scalar(select(User.id).where(User.role == UserRole.WORKER))
    issued = await context.client.post(
        "/api/v1/admin/equipment/issues",
        headers=admin,
        json={
            "equipment_id": item_id,
            "worker_user_id": str(worker_id),
            "quantity": 1,
            "issue_date": "2026-10-31",
        },
    )
    assert issued.status_code == 201, issued.text
    own = await context.client.get("/api/v1/workers/me/equipment", headers=worker)
    assert own.json()[0]["equipment_photo_url"] == photo_url
    assert (await context.client.get("/api/v1" + photo_url, headers=worker)).status_code == 200
    replacement = await context.client.post(
        "/api/v1/admin/equipment-photos",
        headers=admin,
        files={"file": ("new.png", picture(), "image/png")},
    )
    new_url = replacement.json()["photo_url"]
    changed = await context.client.patch(
        f"/api/v1/admin/equipment/{item_id}",
        headers=admin,
        json={"name": "Renamed helmet", "photo_url": new_url},
    )
    assert changed.json()["photo_url"] == new_url
    removed = await context.client.patch(
        f"/api/v1/admin/equipment/{item_id}",
        headers=admin,
        json={"name": "Renamed helmet", "photo_url": None},
    )
    assert removed.json()["photo_url"] is None
    assert removed.json()["available_quantity"] == 2


@pytest.mark.parametrize(
    "content,status",
    [(b"not an image", 422), (b"<svg/>", 422), (b"x" * (5 * 1024 * 1024 + 1), 413)],
)
async def test_equipment_photo_validation(context: AppContext, content, status):
    admin = auth(await login(context, UserRole.ADMIN))
    result = await context.client.post(
        "/api/v1/admin/equipment-photos",
        headers=admin,
        files={"file": ("image.jpg", content, "image/jpeg")},
    )
    assert result.status_code == status


@pytest.mark.parametrize(
    "url", ["https://example.com/picture.jpg", "/photos/other", f"/equipment-photos/{uuid4()}"]
)
async def test_only_uploaded_equipment_images_can_be_attached(context: AppContext, url):
    result = await context.client.post(
        "/api/v1/admin/equipment",
        headers=auth(await login(context, UserRole.ADMIN)),
        json={"name": "Invalid photo", "quantity": 1, "photo_url": url},
    )
    assert result.status_code == 422
