import pytest
from conftest import login
from sqlalchemy import select
from test_equipment_expenses import auth

from backend.identity.models import User, UserRole


async def test_note_crud_search_pin_and_revision(context):
    boss = auth(await login(context, UserRole.BOSS))
    first = await context.client.post(
        "/api/v1/boss/notes", headers=boss, json={"title": "Meeting", "body": "Discuss budget"}
    )
    assert first.status_code == 201, first.text
    row = first.json()
    second = await context.client.post(
        "/api/v1/boss/notes", headers=boss, json={"title": "Pinned", "pinned": True}
    )
    listing = await context.client.get("/api/v1/boss/notes", headers=boss)
    assert listing.json()[0]["id"] == second.json()["id"]
    search = await context.client.get("/api/v1/boss/notes?q=budget", headers=boss)
    assert [note["id"] for note in search.json()] == [row["id"]]
    url = f"/api/v1/boss/notes/{row['id']}"
    update = await context.client.put(
        url, headers=boss, json={"title": "Updated", "body": "Text", "revision": 0}
    )
    assert update.status_code == 200
    assert update.json()["revision"] == 1
    stale = await context.client.put(url, headers=boss, json={"title": "Stale", "revision": 0})
    assert stale.status_code == 409
    # Ownership is checked in both listing and mutations, independently of role access.
    from backend.notes import Note

    async with context.database.session_factory() as session:
        other = await session.scalar(select(User.id).where(User.role == UserRole.ADMIN))
        note = await session.get(Note, __import__("uuid").UUID(row["id"]))
        note.owner_id = other
        await session.commit()
    assert (await context.client.delete(url, headers=boss)).status_code == 404
    assert (await context.client.get("/api/v1/boss/notes?q=Updated", headers=boss)).json() == []


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.MASTER, UserRole.WORKER])
async def test_notes_are_boss_only(context, role):
    headers = auth(await login(context, role))
    assert (await context.client.get("/api/v1/boss/notes", headers=headers)).status_code == 403
    assert (
        await context.client.post(
            "/api/v1/boss/notes", headers=headers, json={"title": "Forbidden"}
        )
    ).status_code == 403
