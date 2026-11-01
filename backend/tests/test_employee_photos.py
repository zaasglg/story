from io import BytesIO
from uuid import uuid4

import pytest
from conftest import AppContext, login
from PIL import Image, PngImagePlugin

from backend.identity.models import UserRole


def picture() -> bytes:
    output = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", "PRIVATE-METADATA")
    Image.new("RGBA", (1200, 900), (20, 100, 200, 128)).save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


@pytest.mark.parametrize("role", list(UserRole))
async def test_upload_permission_and_durable_normalized_photo(context: AppContext, role):
    tokens = await login(context, role)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    response = await context.client.post(
        "/api/v1/admin/worker-photos",
        headers=headers,
        files={"file": ("user.png", picture(), "image/png")},
    )
    if role != UserRole.ADMIN:
        assert response.status_code == 403
        return
    assert response.status_code == 201, response.text
    url = "/api/v1" + response.json()["photo_url"]
    assert (await context.client.get(url)).status_code == 401
    for viewer in UserRole:
        viewer_tokens = await login(context, viewer)
        loaded = await context.client.get(
            url, headers={"Authorization": f"Bearer {viewer_tokens['access_token']}"}
        )
        if viewer == UserRole.MASTER:
            assert loaded.status_code == 404
            continue
        assert loaded.status_code == 200
        assert loaded.headers["content-type"] == "image/jpeg"
        assert b"PRIVATE-METADATA" not in loaded.content
        with Image.open(BytesIO(loaded.content)) as image:
            assert image.size == (768, 576)
            assert image.mode == "RGB"
    created = await context.client.post(
        "/api/v1/admin/workers",
        headers=headers,
        json={
            "phone": "+77075550808",
            "full_name": "Worker with photo",
            "employee_id": "PHOTO-001",
            "photo_url": response.json()["photo_url"],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["photo_url"] == response.json()["photo_url"]
    updated = await context.client.patch(
        f"/api/v1/admin/workers/{created.json()['id']}", headers=headers, json={"photo_url": None}
    )
    assert updated.status_code == 200
    assert updated.json()["photo_url"] is None


@pytest.mark.parametrize(
    "content,status",
    [
        (b"<svg><script>alert(1)</script></svg>", 422),
        (b"not an image", 422),
        (b"", 422),
        (b"x" * (5 * 1024 * 1024 + 1), 413),
    ],
)
async def test_photo_rejects_invalid_or_oversized_files(context: AppContext, content, status):
    tokens = await login(context, UserRole.ADMIN)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    response = await context.client.post(
        "/api/v1/admin/worker-photos",
        headers=headers,
        files={"file": ("fake.jpg", content, "image/jpeg")},
    )
    assert response.status_code == status
    missing = await context.client.get(f"/api/v1/photos/{uuid4()}", headers=headers)
    assert missing.status_code == 404
