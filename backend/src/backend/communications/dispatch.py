"""Bounded, at-least-once push processing with per-device retry bookkeeping."""

import asyncio
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.communications.models import Notification, PushDelivery, PushDevice, PushOutbox
from backend.core.crypto import PiiCipher
from backend.core.time import ensure_utc, utc_now
from backend.identity.models import AccountStatus, AuthSession, User


class PushProvider(Protocol):
    async def send(self, *, token: str, notification_id: UUID, idempotency_key: str) -> None:
        """Submit a generic alert/reference, never private message text."""


class InvalidPushToken(Exception):
    """Provider definitively rejected this token."""


class PermanentPushError(Exception):
    """This delivery cannot be retried."""


async def dispatch_one(
    session: AsyncSession,
    provider: PushProvider,
    cipher: PiiCipher,
    *,
    timeout_seconds: float = 10,
    max_attempts: int = 5,
) -> bool:
    """Process one due job; caller supplies its own session and real provider.

    PostgreSQL row locks prevent concurrent processors claiming the same job.
    A crash after provider acceptance but before commit can still duplicate an
    alert: clients/providers must deduplicate the stable notification reference.
    """
    if timeout_seconds <= 0 or max_attempts < 1:
        raise ValueError("Positive timeout and retry limit are required")
    now = utc_now()
    job = await session.scalar(
        select(PushOutbox)
        .where(
            PushOutbox.status == "PENDING",
            PushOutbox.available_at <= now,
        )
        .order_by(PushOutbox.available_at, PushOutbox.notification_id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return False
    if ensure_utc(job.expires_at) <= now:
        job.status = "EXPIRED"
        await session.commit()
        return True
    notification = await session.get(Notification, job.notification_id)
    if notification is None:
        job.status = "FAILED"
        await session.commit()
        return True
    devices = list(
        (
            await session.scalars(
                select(PushDevice)
                .join(User, User.id == PushDevice.user_id)
                .join(AuthSession, AuthSession.id == PushDevice.session_id)
                .where(
                    PushDevice.user_id == notification.recipient_user_id,
                    PushDevice.revoked_at.is_(None),
                    User.status == AccountStatus.ACTIVE,
                    User.archived_at.is_(None),
                    AuthSession.user_id == User.id,
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > now,
                )
                .order_by(PushDevice.id)
            )
        ).all()
    )
    if not devices:
        job.status = "SKIPPED"
        await session.commit()
        return True
    retry = failed = False
    deadline = now + timedelta(seconds=60)
    job.attempts += 1
    for device in devices:
        if ensure_utc(job.expires_at) <= utc_now():
            job.status = "EXPIRED"
            await session.commit()
            return True
        if utc_now() >= deadline:
            retry = True
            break
        # Recheck eligibility before each submission, not only at batch start.
        eligible = await session.scalar(
            select(PushDevice.id)
            .join(User, User.id == PushDevice.user_id)
            .join(AuthSession, AuthSession.id == PushDevice.session_id)
            .where(
                PushDevice.id == device.id,
                PushDevice.token_hash == device.token_hash,
                PushDevice.revoked_at.is_(None),
                User.status == AccountStatus.ACTIVE,
                User.archived_at.is_(None),
                AuthSession.user_id == User.id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > utc_now(),
            )
        )
        if eligible is None:
            continue
        delivery = await session.scalar(
            select(PushDelivery).where(
                PushDelivery.notification_id == job.notification_id,
                PushDelivery.device_id == device.id,
                PushDelivery.token_hash == device.token_hash,
            )
        )
        if delivery is None:
            delivery = PushDelivery(
                notification_id=job.notification_id,
                device_id=device.id,
                token_hash=device.token_hash,
                status="RETRY",
                attempts=0,
            )
            session.add(delivery)
        if delivery.status in {"ACCEPTED", "INVALID"}:
            continue
        if delivery.status == "FAILED":
            failed = True
            continue
        delivery.attempts += 1
        try:
            await asyncio.wait_for(
                provider.send(
                    token=cipher.decrypt(device.token_encrypted),
                    notification_id=job.notification_id,
                    idempotency_key=f"{job.notification_id}:{device.id}:{device.token_hash}",
                ),
                timeout=timeout_seconds,
            )
            delivery.status, delivery.error_code = "ACCEPTED", None
        except InvalidPushToken:
            # A provider may reject an old token after a concurrent token rotation.
            await session.execute(
                update(PushDevice)
                .where(
                    PushDevice.id == device.id,
                    PushDevice.token_hash == device.token_hash,
                )
                .values(revoked_at=utc_now())
                .execution_options(synchronize_session="fetch")
            )
            delivery.status, delivery.error_code = "INVALID", "invalid_token"
        except PermanentPushError:
            delivery.status, delivery.error_code = "FAILED", "provider_rejected"
            failed = True
        except Exception:
            # Do not persist provider exception strings: they may contain tokens.
            delivery.error_code = "temporary_failure"
            if delivery.attempts >= max_attempts:
                delivery.status = "FAILED"
                failed = True
            else:
                delivery.status = "RETRY"
                retry = True
    job.status = "PENDING" if retry else "FAILED" if failed else "COMPLETED"
    if retry:
        job.available_at = utc_now() + timedelta(seconds=min(3600, 30 * 2 ** min(job.attempts, 7)))
    await session.commit()
    return True
