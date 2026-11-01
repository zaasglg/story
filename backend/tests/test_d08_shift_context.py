from datetime import timedelta
from uuid import UUID

from conftest import login
from sqlalchemy import select
from test_operations_api import auth, prepare_assignment

from backend.core.time import utc_now
from backend.identity.models import UserRole
from backend.workforce.models import WorkerAssignment


async def test_open_shift_blocks_transfer_and_checkout_uses_original_snapshot(context, monkeypatch):
    admin, master, worker = [
        await login(context, role) for role in (UserRole.ADMIN, UserRole.MASTER, UserRole.WORKER)
    ]
    wid, _, bid, oid = await prepare_assignment(context, admin)
    now = utc_now()
    monkeypatch.setattr("backend.access.shifts.utc_now", lambda: now)
    monkeypatch.setattr("backend.access.routes.utc_now", lambda: now)
    qr = (await context.client.get("/api/v1/workers/me/pass", headers=auth(worker))).json()[
        "qr_payload"
    ]
    assert (
        await context.client.post(
            "/api/v1/master/shift-scans", headers=auth(master), json={"qr_payload": qr}
        )
    ).status_code == 200
    old = (await context.client.get("/api/v1/admin/objects", headers=auth(admin))).json()[0]
    obj = (
        await context.client.post(
            "/api/v1/admin/objects",
            headers=auth(admin),
            json={"name": "Synthetic target", "city_id": old["city_id"], "address": "Synthetic"},
        )
    ).json()
    path = f"/api/v1/admin/workers/{wid}/assignment"
    transfer = await context.client.post(
        path, headers=auth(admin), json={"brigade_id": bid, "object_id": obj["id"]}
    )
    assert transfer.status_code == 409
    assert (await context.client.delete(path, headers=auth(admin))).status_code == 409
    # Simulate a pre-upgrade inconsistent assignment; checkout must still use entry snapshot.
    async with context.database.session_factory() as session:
        row = await session.scalar(
            select(WorkerAssignment).where(
                WorkerAssignment.worker_user_id == UUID(wid),
                WorkerAssignment.effective_to.is_(None),
            )
        )
        row.object_id = UUID(obj["id"])
        await session.commit()
    future = now + timedelta(minutes=3)
    monkeypatch.setattr("backend.access.shifts.utc_now", lambda: future)
    monkeypatch.setattr("backend.access.routes.utc_now", lambda: future)
    qr = (await context.client.get("/api/v1/workers/me/pass", headers=auth(worker))).json()[
        "qr_payload"
    ]
    result = await context.client.post(
        "/api/v1/master/shift-scans", headers=auth(master), json={"qr_payload": qr}
    )
    assert result.status_code == 200, result.text
    assert result.json()["action"] == "CHECK_OUT"
    assert result.json()["object_id"] == oid
    assert result.json()["worked_minutes"] == 3
    assert (
        await context.client.post(
            path, headers=auth(admin), json={"brigade_id": bid, "object_id": obj["id"]}
        )
    ).status_code == 200


async def test_postgres_scan_and_transfer_share_a_serialized_context(context):
    import asyncio

    import pytest

    if context.database.engine.dialect.name != "postgresql":
        pytest.skip("Real row locks require PostgreSQL")
    admin, master, worker = [
        await login(context, role) for role in (UserRole.ADMIN, UserRole.MASTER, UserRole.WORKER)
    ]
    wid, _, bid, oid = await prepare_assignment(context, admin)
    old = (await context.client.get("/api/v1/admin/objects", headers=auth(admin))).json()[0]
    obj = (
        await context.client.post(
            "/api/v1/admin/objects",
            headers=auth(admin),
            json={"name": "Concurrent target", "city_id": old["city_id"], "address": "Synthetic"},
        )
    ).json()
    qr = (await context.client.get("/api/v1/workers/me/pass", headers=auth(worker))).json()[
        "qr_payload"
    ]
    scanned, transferred = await asyncio.wait_for(
        asyncio.gather(
            context.client.post(
                "/api/v1/master/shift-scans", headers=auth(master), json={"qr_payload": qr}
            ),
            context.client.post(
                f"/api/v1/admin/workers/{wid}/assignment",
                headers=auth(admin),
                json={"brigade_id": bid, "object_id": obj["id"]},
            ),
        ),
        timeout=10,
    )
    assert scanned.status_code == 200, scanned.text
    assert transferred.status_code in (200, 409), transferred.text
    expected = obj["id"] if transferred.status_code == 200 else oid
    assert scanned.json()["object_id"] == expected
    workers = (await context.client.get("/api/v1/admin/workers", headers=auth(admin))).json()
    assert next(w for w in workers if w["id"] == wid)["assignment"]["object_id"] == expected
