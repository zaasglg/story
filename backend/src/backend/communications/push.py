"""Push registration and transactional staging for the dedicated sender."""

from datetime import datetime, timedelta
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit.service import add_audit_event
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.communications.models import Notification, PushDevice, PushOutbox
from backend.core.config import Settings
from backend.core.crypto import PiiCipher
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.identity.dependencies import CurrentUser, get_app_settings, get_db_session

router = APIRouter(tags=["push"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
Owner = Annotated[CurrentUser, Depends(require_permission(Permission.ANNOUNCEMENT_RECEIVE))]
Config = Annotated[Settings, Depends(get_app_settings)]


async def stage_notifications(session: AsyncSession, items: list[Notification]) -> None:
    session.add_all(items)
    await session.flush()
    session.add_all(
        [
            PushOutbox(
                notification_id=item.id,
                status="PENDING",
                attempts=0,
                available_at=item.created_at,
                expires_at=item.created_at + timedelta(days=3),
            )
            for item in items
        ]
    )


class DeviceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    installation_id: UUID
    platform: Literal["android", "ios", "web"]
    token: str = Field(min_length=16, max_length=4096, repr=False)


class DeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    installation_id: UUID
    platform: str
    updated_at: datetime
    revoked_at: datetime | None


@router.post("/me/push-devices", response_model=DeviceRead)
async def register_device(
    payload: DeviceCreate, session: Session, actor: Owner, config: Config
) -> PushDevice:
    fingerprint = sha256(payload.token.encode()).hexdigest()
    item = await session.scalar(
        select(PushDevice)
        .where(
            PushDevice.user_id == actor.id, PushDevice.installation_id == payload.installation_id
        )
        .with_for_update()
    )
    token_owner = await session.scalar(
        select(PushDevice).where(PushDevice.token_hash == fingerprint).with_for_update()
    )
    if token_owner is not None and (item is None or token_owner.id != item.id):
        if token_owner.revoked_at is None:
            raise ApiError(409, "device_token_conflict", "Device token is already registered")
        # Only an explicitly revoked token can move between signed-in accounts.
        # Preserve device/delivery history without retaining a unique active claim.
        token_owner.token_hash = sha256(
            f"revoked:{token_owner.id}:{fingerprint}".encode()
        ).hexdigest()
        await session.flush()
    if item is None:
        item = PushDevice(user_id=actor.id, installation_id=payload.installation_id)
        session.add(item)
    item.session_id = actor.session_id
    item.platform = payload.platform
    item.token_hash = fingerprint
    item.token_encrypted = PiiCipher(config.pii_encryption_key.get_secret_value()).encrypt(
        payload.token
    )
    item.updated_at, item.revoked_at = utc_now(), None
    try:
        await session.flush()
        add_audit_event(
            session,
            actor_user_id=actor.id,
            action="PUSH_DEVICE_REGISTERED",
            entity_type="push_device",
            entity_id=item.id,
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "device_registration_conflict", "Retry device registration") from exc
    return item


@router.get("/me/push-devices", response_model=list[DeviceRead])
async def own_devices(session: Session, actor: Owner) -> list[PushDevice]:
    return list(
        (
            await session.scalars(
                select(PushDevice)
                .where(PushDevice.user_id == actor.id, PushDevice.revoked_at.is_(None))
                .order_by(PushDevice.updated_at.desc())
                .limit(100)
            )
        ).all()
    )


@router.delete("/me/push-devices/{device_id}", status_code=204)
async def revoke_device(device_id: UUID, session: Session, actor: Owner) -> None:
    item = await session.scalar(
        select(PushDevice)
        .where(PushDevice.id == device_id, PushDevice.user_id == actor.id)
        .with_for_update()
    )
    if item is None:
        raise ApiError(404, "device_not_found", "Device was not found")
    if item.revoked_at is None:
        item.revoked_at = utc_now()
        add_audit_event(
            session,
            actor_user_id=actor.id,
            action="PUSH_DEVICE_REVOKED",
            entity_type="push_device",
            entity_id=item.id,
        )
        await session.commit()
