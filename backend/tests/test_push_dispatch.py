import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from conftest import AppContext, login
from sqlalchemy import select

from backend.communications.dispatch import InvalidPushToken, PermanentPushError, dispatch_one
from backend.communications.models import Notification, PushDelivery, PushDevice, PushOutbox
from backend.communications.push import stage_notifications
from backend.core.crypto import PiiCipher
from backend.core.time import utc_now
from backend.identity.models import AccountStatus, AuthSession, User, UserRole


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.errors = {}

    async def send(self, *, token, notification_id, idempotency_key):
        self.calls.append((token, notification_id, idempotency_key))
        if token in self.errors:
            raise self.errors[token]


async def prepare(context: AppContext, count: int = 1):
    tokens = await login(context, UserRole.WORKER)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    devices = []
    for i in range(count):
        response = await context.client.post(
            "/api/v1/me/push-devices",
            headers=headers,
            json={
                "installation_id": str(uuid4()),
                "platform": "android",
                "token": f"private-token-number-{i}",
            },
        )
        assert response.status_code == 200
        devices.append(response.json()["id"])
    async with context.database.session_factory() as session:
        user_id = await session.scalar(select(User.id).where(User.role == UserRole.WORKER))
        item = Notification(
            recipient_user_id=user_id,
            title="Private title",
            body="Private message",
            created_at=utc_now(),
        )
        await stage_notifications(session, [item])
        await session.commit()
    return item.id, devices


async def test_dispatch_retries_only_failed_device_and_redacts_errors(context: AppContext) -> None:
    job_id, _ = await prepare(context, 2)
    provider = FakeProvider()
    provider.errors["private-token-number-1"] = RuntimeError("sensitive-token-in-error")
    cipher = PiiCipher(context.settings.pii_encryption_key.get_secret_value())
    async with context.database.session_factory() as session:
        assert await dispatch_one(session, provider, cipher)
        job = await session.get(PushOutbox, job_id)
        assert job.status == "PENDING"
        assert len(provider.calls) == 2
        assert not await dispatch_one(session, provider, cipher)
        rows = (await session.scalars(select(PushDelivery))).all()
        assert {r.status for r in rows} == {"ACCEPTED", "RETRY"}
        assert all(r.error_code in {None, "temporary_failure"} for r in rows)
        job.available_at = utc_now() - timedelta(seconds=1)
        await session.commit()
        provider.errors.clear()
        assert await dispatch_one(session, provider, cipher)
        assert (await session.get(PushOutbox, job_id)).status == "COMPLETED"
        assert len(provider.calls) == 3
        assert provider.calls[-1][0] == "private-token-number-1"
        assert provider.calls[-1][2] == next(
            c[2] for c in provider.calls[:2] if c[0] == "private-token-number-1"
        )
        assert not await dispatch_one(session, provider, cipher)


@pytest.mark.parametrize(
    "blocked", ["device", "session", "expired_session", "inactive", "archived", "expired_job"]
)
async def test_dispatch_skips_invalid_recipient_state(context: AppContext, blocked: str) -> None:
    job_id, _ = await prepare(context)
    provider = FakeProvider()
    async with context.database.session_factory() as session:
        device = await session.scalar(select(PushDevice))
        user = await session.get(User, device.user_id)
        auth_session = await session.get(AuthSession, device.session_id)
        job = await session.get(PushOutbox, job_id)
        past = utc_now() - timedelta(seconds=1)
        if blocked == "device":
            device.revoked_at = past
        if blocked == "session":
            auth_session.revoked_at = past
        if blocked == "expired_session":
            auth_session.expires_at = past
        if blocked == "inactive":
            user.status = AccountStatus.BLOCKED
        if blocked == "archived":
            user.archived_at = past
        if blocked == "expired_job":
            job.expires_at = past
        await session.commit()
        assert await dispatch_one(
            session, provider, PiiCipher(context.settings.pii_encryption_key.get_secret_value())
        )
        assert provider.calls == []
        assert job.status == ("EXPIRED" if blocked == "expired_job" else "SKIPPED")


@pytest.mark.parametrize(
    "error,status",
    [(InvalidPushToken(), "INVALID"), (PermanentPushError(), "FAILED"), (RuntimeError(), "FAILED")],
)
async def test_dispatch_terminal_errors(context: AppContext, error: Exception, status: str) -> None:
    job_id, _ = await prepare(context)
    provider = FakeProvider()
    provider.errors["private-token-number-0"] = error
    async with context.database.session_factory() as session:
        await dispatch_one(
            session,
            provider,
            PiiCipher(context.settings.pii_encryption_key.get_secret_value()),
            max_attempts=1,
        )
        delivery = await session.scalar(select(PushDelivery))
        assert delivery.status == status
        job = await session.get(PushOutbox, job_id)
        assert job.status == ("COMPLETED" if status == "INVALID" else "FAILED")
        if status == "INVALID":
            assert (await session.scalar(select(PushDevice))).revoked_at is not None


async def test_dispatch_provider_timeout_is_bounded_and_retryable(context: AppContext) -> None:
    job_id, _ = await prepare(context)
    cancelled = asyncio.Event()

    class SlowProvider:
        async def send(self, *, token, notification_id, idempotency_key):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    async with context.database.session_factory() as session:
        assert await dispatch_one(
            session,
            SlowProvider(),
            PiiCipher(context.settings.pii_encryption_key.get_secret_value()),
            timeout_seconds=0.001,
        )
        assert cancelled.is_set()
        job = await session.get(PushOutbox, job_id)
        assert job.status == "PENDING"
        delivery = await session.scalar(select(PushDelivery))
        assert delivery.status == "RETRY"
        assert delivery.error_code == "temporary_failure"
