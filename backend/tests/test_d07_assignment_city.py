from conftest import login
from test_operations_api import auth, prepare_assignment

from backend.identity.models import UserRole


async def test_mismatched_assignment_and_city_edits_are_rejected_atomically(context):
    admin = await login(context, UserRole.ADMIN)
    wid, _, bid, oid = await prepare_assignment(context, admin)
    headers = auth(admin)
    city = (
        await context.client.post(
            "/api/v1/admin/cities",
            headers=headers,
            json={"name": "Synthetic other city", "timezone": "Asia/Almaty"},
        )
    ).json()
    obj = (
        await context.client.post(
            "/api/v1/admin/objects",
            headers={
                "Authorization": f"Bearer {(await login(context, UserRole.BOSS))['access_token']}"
            },
            json={"name": "Synthetic other object", "city_id": city["id"], "address": "Synthetic"},
        )
    ).json()
    result = await context.client.post(
        f"/api/v1/admin/workers/{wid}/assignment",
        headers=headers,
        json={"brigade_id": bid, "object_id": obj["id"]},
    )
    assert result.status_code == 422
    for path in [f"/admin/brigades/{bid}", f"/admin/objects/{oid}", f"/admin/workers/{wid}"]:
        result = await context.client.patch(
            "/api/v1" + path,
            headers=auth(await login(context, UserRole.BOSS)) if "/objects/" in path else headers,
            json={"city_id": city["id"]},
        )
        assert result.status_code == 422, result.text
    workers = (await context.client.get("/api/v1/admin/workers", headers=headers)).json()
    assert next(w for w in workers if w["id"] == wid)["assignment"]["object_id"] == oid
    assert (
        await context.client.post(
            f"/api/v1/admin/workers/{wid}/assignment",
            headers=headers,
            json={"brigade_id": bid, "object_id": oid},
        )
    ).status_code == 200


async def test_postgres_profile_city_edit_cannot_race_with_assignment(context, monkeypatch):
    import asyncio

    import pytest

    from backend.workforce import routes

    if context.database.engine.dialect.name != "postgresql":
        pytest.skip("Real row locks require PostgreSQL")
    admin = await login(context, UserRole.ADMIN)
    wid, _, bid, oid = await prepare_assignment(context, admin)
    headers = auth(admin)
    assert (
        await context.client.delete(f"/api/v1/admin/workers/{wid}/assignment", headers=headers)
    ).status_code == 204
    city = (
        await context.client.post(
            "/api/v1/admin/cities",
            headers=headers,
            json={"name": "Synthetic race city", "timezone": "UTC"},
        )
    ).json()
    validated, resume = asyncio.Event(), asyncio.Event()
    original = routes.validate_worker_city

    async def paused(session, worker_id, city_id):
        await original(session, worker_id, city_id)
        validated.set()
        await resume.wait()

    monkeypatch.setattr(routes, "validate_worker_city", paused)
    editing = asyncio.create_task(
        context.client.patch(
            f"/api/v1/admin/workers/{wid}", headers=headers, json={"city_id": city["id"]}
        )
    )
    assigning = None
    try:
        await asyncio.wait_for(validated.wait(), 5)
        assigning = asyncio.create_task(
            context.client.post(
                f"/api/v1/admin/workers/{wid}/assignment",
                headers=headers,
                json={"brigade_id": bid, "object_id": oid},
            )
        )
        await asyncio.sleep(0.05)
        assert not assigning.done()
        resume.set()
        assert (await asyncio.wait_for(editing, 5)).status_code == 200
        assert (await asyncio.wait_for(assigning, 5)).status_code == 200
        worker = next(
            w
            for w in (await context.client.get("/api/v1/admin/workers", headers=headers)).json()
            if w["id"] == wid
        )
        brigade = next(
            b
            for b in (await context.client.get("/api/v1/admin/brigades", headers=headers)).json()
            if b["id"] == bid
        )
        assert worker["city_id"] == brigade["city_id"]
    finally:
        resume.set()
        for task in (editing, assigning):
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def test_unchanged_object_city_does_not_block_address_edit_with_legacy_assignment(context):
    from uuid import UUID

    from backend.workforce.models import Brigade

    admin = await login(context, UserRole.ADMIN)
    _, _, brigade_id, object_id = await prepare_assignment(context, admin)
    boss = auth(await login(context, UserRole.BOSS))
    original = next(
        row
        for row in (await context.client.get("/api/v1/boss/objects", headers=boss)).json()
        if row["id"] == object_id
    )
    other = (
        await context.client.post(
            "/api/v1/admin/cities",
            headers=auth(admin),
            json={"name": "Legacy other city", "timezone": "Asia/Almaty"},
        )
    ).json()
    async with context.database.session_factory() as session:
        brigade = await session.get(Brigade, UUID(brigade_id))
        brigade.city_id = UUID(other["id"])
        await session.commit()
    updated = await context.client.patch(
        f"/api/v1/boss/objects/{object_id}",
        headers=boss,
        json={
            "city_id": original["city_id"],
            "address": "Updated exact address",
            "latitude": 51.67,
            "longitude": 39.18,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["city_id"] == original["city_id"]
    assert updated.json()["address"] == "Updated exact address"
    assert updated.json()["latitude"] == 51.67
