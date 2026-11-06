import pytest
from conftest import AppContext, login

from backend.identity.models import UserRole


async def setup_object(context):
    headers = {"Authorization": f"Bearer {(await login(context, UserRole.BOSS))['access_token']}"}
    city = (await context.client.get("/api/v1/cities", headers=headers)).json()[0]["id"]
    obj = (
        await context.client.post(
            "/api/v1/boss/objects",
            headers=headers,
            json={"name": "Файлы объекта", "address": "Test", "city_id": city},
        )
    ).json()
    return headers, f"/api/v1/boss/objects/{obj['id']}/files"


async def test_object_file_workflow(context: AppContext):
    headers, base = await setup_object(context)
    folder = await context.client.post(
        base + "/folders", headers=headers, json={"name": "Договоры"}
    )
    assert folder.status_code == 201, folder.text
    folder_id = folder.json()["id"]
    nested = await context.client.post(
        base + "/folders", headers=headers, json={"name": "2026", "parent_id": folder_id}
    )
    assert nested.status_code == 201
    child_id = nested.json()["id"]
    duplicate = await context.client.post(
        base + "/folders", headers=headers, json={"name": "Договоры"}
    )
    assert duplicate.status_code == 409
    cycle = await context.client.patch(
        base + "/" + folder_id, headers=headers, json={"parent_id": child_id}
    )
    assert cycle.status_code == 422
    content = b"example binary document"
    uploaded = await context.client.post(
        base + "/upload",
        headers=headers,
        data={"parent_id": child_id},
        files={"file": ("Договор.docx", content)},
    )
    assert uploaded.status_code == 201, uploaded.text
    file_id = uploaded.json()["id"]
    listing = await context.client.get(base, headers=headers, params={"parent_id": child_id})
    assert listing.json()[0]["name"] == "Договор.docx"
    download = await context.client.get(base + "/" + file_id + "/download", headers=headers)
    assert download.content == content
    assert download.headers["content-type"] == "application/octet-stream"
    renamed = await context.client.patch(
        base + "/" + file_id,
        headers=headers,
        json={"name": "Новый договор.docx", "parent_id": folder_id},
    )
    assert renamed.status_code == 200
    assert renamed.json()["parent_id"] == folder_id
    deleted = await context.client.delete(base + "/" + folder_id, headers=headers)
    assert deleted.status_code == 204
    assert (await context.client.get(base, headers=headers)).json() == []
    assert (
        await context.client.get(base + "/" + file_id + "/download", headers=headers)
    ).status_code == 404
    assert (
        await context.client.get(base, headers=headers, params={"parent_id": child_id})
    ).status_code == 404


async def test_objects_cannot_share_files_or_folders(context: AppContext):
    headers, first = await setup_object(context)
    _, second = await setup_object(context)
    folder = (
        await context.client.post(first + "/folders", headers=headers, json={"name": "Private"})
    ).json()["id"]
    denied = await context.client.post(
        second + "/folders", headers=headers, json={"name": "Cross object", "parent_id": folder}
    )
    assert denied.status_code == 404
    assert (await context.client.delete(second + "/" + folder, headers=headers)).status_code == 404
    for name in ["..", "a/b", "   "]:
        assert (
            await context.client.post(first + "/folders", headers=headers, json={"name": name})
        ).status_code == 422


@pytest.mark.parametrize("role", list(UserRole))
async def test_object_files_four_role_permissions(context: AppContext, role):
    _, base = await setup_object(context)
    headers = {"Authorization": f"Bearer {(await login(context, role))['access_token']}"}
    listing = await context.client.get(base, headers=headers)
    assert listing.status_code == (200 if role == UserRole.BOSS else 403)
    created = await context.client.post(base + "/folders", headers=headers, json={"name": "Plans"})
    assert created.status_code == (201 if role == UserRole.BOSS else 403)
