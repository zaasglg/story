from datetime import timedelta

from conftest import login
from sqlalchemy import select
from test_operations_api import auth
from test_shift_scans import qr, scan, setup_shift

from backend.access import shifts
from backend.access.models import PassToken
from backend.identity.models import UserRole


async def test_durable_qr_feed_replays_missed_events_without_private_payload(context, monkeypatch):
    admin, worker, master, wid, _ = await setup_shift(context)
    headers = auth(admin)
    token = await qr(context, worker)
    arrived = await scan(context, master, token, "AUTO")
    assert arrived.status_code == 200, arrived.text
    path = "/api/v1/admin/attendance-events"
    first = await context.client.get(path, headers=headers, params={"after": 0})
    assert first.status_code == 200, first.text
    assert len(first.json()["events"]) == 1
    cursor = first.json()["cursor"]
    # The client disconnects: another committed scan must be available through replay.
    departure = await qr(context, worker)
    now = shifts.utc_now() + timedelta(hours=1)
    import hashlib

    async with context.database.session_factory() as session:
        item = await session.scalar(
            select(PassToken).where(
                PassToken.token_hash == hashlib.sha256(departure.encode()).hexdigest()
            )
        )
        item.expires_at = now + timedelta(minutes=5)
        await session.commit()
    monkeypatch.setattr(shifts, "utc_now", lambda: now)
    assert (await scan(context, master, departure, "AUTO")).status_code == 200
    replay = await context.client.get(path, headers=headers, params={"after": cursor})
    assert len(replay.json()["events"]) == 1
    assert replay.json()["cursor"] > cursor
    assert (
        await context.client.get(path, headers=headers, params={"after": replay.json()["cursor"]})
    ).json()["events"] == []
    assert set(replay.json()["events"][0]) == {
        "id",
        "type",
        "attendance_id",
        "city_id",
        "object_id",
        "work_date",
    }
    for role in (UserRole.MASTER, UserRole.WORKER):
        assert (
            await context.client.get(path, headers=auth(await login(context, role)))
        ).status_code == 403


async def test_admin_socket_hint_and_revocation(context):
    import json

    from test_chat_realtime import SocketProbe

    admin = await login(context, UserRole.ADMIN)
    app = context.client._transport.app
    socket = SocketProbe(app, "unused")
    socket.path = "/api/v1/admin/attendance-events/live"
    async with socket:
        assert socket.handshake["type"] == "websocket.accept"
        ready = await socket.auth(admin["access_token"])
        assert json.loads(ready["text"]) == {"type": "ready"}
        # A revoked session must stop receiving the global event feed.
        await context.client.post(
            "/api/v1/auth/logout",
            headers=auth(admin),
            json={"refresh_token": admin["refresh_token"]},
        )
        assert (await socket.receive())["code"] == 1008
    worker = await login(context, UserRole.WORKER)
    socket = SocketProbe(app, "unused")
    socket.path = "/api/v1/admin/attendance-events/live"
    async with socket:
        assert (await socket.auth(worker["access_token"]))["code"] == 1008
