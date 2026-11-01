import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from conftest import AppContext, login
from sqlalchemy import select

from backend.communications.events import ChatEvents
from backend.communications.models import ChatThread
from backend.identity.models import UserRole


class SocketProbe:
    def __init__(self, app, thread_id, origin=None):
        self.app = app
        self.path = f"/api/v1/chats/{thread_id}/events"
        self.origin = origin
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()

    async def __aenter__(self):
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "path": self.path,
            "raw_path": self.path.encode(),
            "query_string": b"",
            "root_path": "",
            "http_version": "1.1",
            "headers": [(b"origin", self.origin.encode())] if self.origin else [],
            "client": ("127.0.0.1", 123),
            "server": ("testserver", 80),
            "subprotocols": [],
            "state": {},
        }
        self.task = asyncio.create_task(self.app(scope, self.incoming.get, self.outgoing.put))
        await self.incoming.put({"type": "websocket.connect"})
        self.handshake = await self.receive()
        return self

    async def receive(self):
        return await asyncio.wait_for(self.outgoing.get(), timeout=2)

    async def auth(self, token):
        await self.incoming.put({"type": "websocket.receive", "text": json.dumps({"token": token})})
        return await self.receive()

    async def __aexit__(self, *args):
        await self.incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(self.task, timeout=2)


async def general(context):
    async with context.database.session_factory() as session:
        return await session.scalar(select(ChatThread.id).where(ChatThread.key == "GENERAL"))


@pytest.mark.parametrize("role", [UserRole.MASTER, UserRole.ADMIN, UserRole.BOSS])
async def test_chat_events_authorized_and_only_after_commit(context: AppContext, role):
    tokens = await login(context, role)
    thread = await general(context)
    app = context.client._transport.app
    async with SocketProbe(app, thread) as socket:
        assert socket.handshake["type"] == "websocket.accept"
        assert json.loads((await socket.auth(tokens["access_token"]))["text"]) == {"type": "ready"}
        payload = {"client_message_id": str(uuid4()), "body": "private-chat-body"}
        path = f"/api/v1/chats/{thread}/messages"
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        response = await context.client.post(path, json=payload, headers=headers)
        assert response.status_code == 201
        event = await socket.receive()
        assert json.loads(event["text"]) == {"type": "changed"}
        assert "private-chat-body" not in str(event)
        rows = (await context.client.get(path, headers=headers)).json()
        assert rows[0]["id"] == response.json()["id"]
        assert (await context.client.post(path, json=payload, headers=headers)).status_code == 201
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(socket.outgoing.get(), timeout=0.03)
    assert app.state.chat_events.listeners == {}


async def test_chat_socket_denies_foreign_thread_and_missing_auth(context: AppContext):
    worker = await login(context, UserRole.WORKER)
    boss = await login(context, UserRole.BOSS)
    admin = await login(context, UserRole.ADMIN)
    admin_me = (
        await context.client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {admin['access_token']}"}
        )
    ).json()
    thread = (
        await context.client.post(
            f"/api/v1/chats/direct/{admin_me['id']}",
            headers={"Authorization": f"Bearer {boss['access_token']}"},
        )
    ).json()["id"]
    app = context.client._transport.app
    async with SocketProbe(app, thread) as socket:
        assert (await socket.auth(worker["access_token"]))["code"] == 1008
    async with SocketProbe(app, await general(context)) as socket:
        assert (await socket.auth(None))["code"] == 1008
    async with SocketProbe(app, thread, origin="https://untrusted.example") as socket:
        assert socket.handshake["type"] == "websocket.close"


async def test_logout_revokes_connected_socket_before_next_event(context: AppContext):
    worker = await login(context, UserRole.MASTER)
    thread = await general(context)
    app = context.client._transport.app
    async with SocketProbe(app, thread) as socket:
        await socket.auth(worker["access_token"])
        response = await context.client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {worker['access_token']}"},
            json={"refresh_token": worker["refresh_token"]},
        )
        assert response.status_code == 200
        await app.state.chat_events.publish(thread)
        assert (await socket.receive())["code"] == 1008


async def test_socket_broker_failure_closes_for_fallback(context: AppContext, monkeypatch):
    worker = await login(context, UserRole.MASTER)
    app = context.client._transport.app

    @asynccontextmanager
    async def broken(thread):
        raise ConnectionError("private-redis-password")
        yield

    monkeypatch.setattr(app.state.chat_events, "subscribe", broken)
    async with SocketProbe(app, await general(context)) as socket:
        event = await socket.auth(worker["access_token"])
        assert event["code"] == 1013
        assert "private" not in str(event)


async def test_redis_publish_failure_does_not_fail_committed_message(context: AppContext, caplog):
    worker = await login(context, UserRole.MASTER)
    app = context.client._transport.app
    redis = AsyncMock()
    redis.publish.side_effect = ConnectionError("private-redis-password")
    app.state.chat_events = ChatEvents(redis)
    thread = await general(context)
    headers = {"Authorization": f"Bearer {worker['access_token']}"}
    path = f"/api/v1/chats/{thread}/messages"
    response = await context.client.post(
        path, headers=headers, json={"body": "saved", "client_message_id": str(uuid4())}
    )
    assert response.status_code == 201
    assert (await context.client.get(path, headers=headers)).json()[0]["body"] == "saved"
    assert "chat_realtime_publish_failed" in caplog.text
    assert "private-redis-password" not in caplog.text


async def test_worker_cannot_list_read_send_or_subscribe_general_chat(context: AppContext):
    tokens = await login(context, UserRole.WORKER)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    thread = await general(context)
    chats = await context.client.get("/api/v1/chats", headers=headers)
    assert chats.status_code == 200
    assert all(item["key"] != "GENERAL" for item in chats.json())
    path = f"/api/v1/chats/{thread}/messages"
    assert (await context.client.get(path, headers=headers)).status_code == 404
    response = await context.client.post(
        path, headers=headers, json={"body": "denied", "client_message_id": str(uuid4())}
    )
    assert response.status_code == 404
    async with SocketProbe(context.client._transport.app, thread) as socket:
        assert (await socket.auth(tokens["access_token"]))["code"] == 1008
