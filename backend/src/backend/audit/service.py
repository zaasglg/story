from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.time import utc_now
from backend.identity.models import AuditLog


def add_audit_event(
    session: AsyncSession,
    *,
    action: str,
    entity_type: str,
    actor_user_id: UUID | None = None,
    entity_id: UUID | None = None,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    request_id: str | None = None,
    ip_hash: str | None = None,
    succeeded: bool = True,
) -> None:
    session.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_value=old_value,
            new_value=new_value,
            metadata_json=metadata,
            created_at=utc_now(),
            request_id=request_id,
            ip_hash=ip_hash,
            succeeded=succeeded,
        )
    )
