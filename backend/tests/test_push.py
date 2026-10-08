from uuid import uuid4

from conftest import AppContext, login
from sqlalchemy import func, select

from backend.communications.models import Notification, PushDevice, PushOutbox
from backend.communications.push import stage_notifications
from backend.core.crypto import PiiCipher
from backend.core.time import utc_now
from backend.identity.models import User, UserRole


async def test_push_registration_own_scope_and_secrets(context: AppContext) -> None:
    headers = {
        role: {"Authorization": f"Bearer {(await login(context, role))['access_token']}"}
        for role in UserRole
    }
    path = "/api/v1/me/push-devices"
    payload = {
        "installation_id": str(uuid4()),
        "platform": "android",
        "token": "private-device-token-12345",
    }
    created = await context.client.post(path, headers=headers[UserRole.WORKER], json=payload)
    assert created.status_code == 200
    device_id = created.json()["id"]
    assert "token" not in created.json()
    assert payload["token"] not in created.text
    repeated = await context.client.post(path, headers=headers[UserRole.WORKER], json=payload)
    assert repeated.json()["id"] == device_id
    rotated = await context.client.post(
        path,
        headers=headers[UserRole.WORKER],
        json={**payload, "token": "rotated-private-token-12345"},
    )
    assert rotated.json()["id"] == device_id
    for role in (UserRole.MASTER, UserRole.ADMIN, UserRole.BOSS):
        assert (await context.client.get(path, headers=headers[role])).json() == []
        denied = await context.client.delete(f"{path}/{device_id}", headers=headers[role])
        assert denied.status_code == 404
    collision = await context.client.post(
        path,
        headers=headers[UserRole.ADMIN],
        json={**payload, "token": "rotated-private-token-12345"},
    )
    assert collision.status_code == 409
    async with context.database.session_factory() as session:
        device = await session.scalar(select(PushDevice))
        assert device.token_encrypted != "rotated-private-token-12345"
        assert (
            PiiCipher(context.settings.pii_encryption_key.get_secret_value()).decrypt(
                device.token_encrypted
            )
            == "rotated-private-token-12345"
        )
        assert await session.scalar(select(func.count()).select_from(PushDevice)) == 1
    invalid = await context.client.post(
        path,
        headers=headers[UserRole.WORKER],
        json={**payload, "platform": payload["token"], "user_id": str(uuid4())},
    )
    assert invalid.status_code == 422
    assert payload["token"] not in invalid.text
    for _ in range(2):
        revoked = await context.client.delete(
            f"{path}/{device_id}", headers=headers[UserRole.WORKER]
        )
        assert revoked.status_code == 204
        assert revoked.content == b""
    assert (await context.client.get(path, headers=headers[UserRole.WORKER])).json() == []


async def test_notifications_stage_outbox_atomically_and_chat_replay(context: AppContext) -> None:
    worker = {"Authorization": f"Bearer {(await login(context, UserRole.WORKER))['access_token']}"}
    admin = {"Authorization": f"Bearer {(await login(context, UserRole.ADMIN))['access_token']}"}
    announced = await context.client.post(
        "/api/v1/announcements", headers=admin, json={"title": "Briefing", "body": "Tomorrow"}
    )
    assert announced.status_code == 201
    async with context.database.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(PushOutbox)) == 4
    # A direct recipient is resolved from an authenticated user, not a supplied notification owner.
    me = (await context.client.get("/api/v1/me", headers=admin)).json()
    direct = await context.client.post(f"/api/v1/chats/direct/{me['id']}", headers=worker)
    path = f"/api/v1/chats/{direct.json()['id']}/messages"
    for _ in range(2):
        sent = await context.client.post(
            path, headers=worker, json={"client_message_id": "push-retry", "body": "Hello"}
        )
        assert sent.status_code == 201
    async with context.database.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(PushOutbox)) == 5
        assert await session.scalar(select(func.count()).select_from(Notification)) == 5
        assert set((await session.scalars(select(PushOutbox.status))).all()) == {"PENDING"}


async def test_outbox_rolls_back_with_notification(context: AppContext) -> None:
    async with context.database.session_factory() as session:
        user_id = await session.scalar(select(User.id).where(User.role == UserRole.WORKER))
        await stage_notifications(
            session,
            [
                Notification(
                    recipient_user_id=user_id,
                    title="Not committed",
                    body="Private",
                    created_at=utc_now(),
                )
            ],
        )
        await session.flush()
        assert await session.scalar(select(func.count()).select_from(PushOutbox)) == 1
        await session.rollback()
        assert await session.scalar(select(func.count()).select_from(PushOutbox)) == 0
        assert await session.scalar(select(func.count()).select_from(Notification)) == 0
