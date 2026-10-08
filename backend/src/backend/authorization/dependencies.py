from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit.service import add_audit_event
from backend.authorization.permissions import Permission, has_permission
from backend.core.config import Settings
from backend.core.errors import ApiError
from backend.identity.dependencies import (
    CurrentUser,
    get_app_settings,
    get_current_user,
    get_db_session,
)
from backend.identity.service import client_hash


def require_permission(permission: Permission) -> Callable[..., CurrentUser]:
    async def dependency(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
        request: Request,
        session: Annotated[AsyncSession, Depends(get_db_session)],
        settings: Annotated[Settings, Depends(get_app_settings)],
    ) -> CurrentUser:
        if not has_permission(current_user.role, permission):
            ip_address = request.client.host if request.client else "unknown"
            add_audit_event(
                session,
                action="AUTHORIZATION_DENIED",
                entity_type="permission",
                actor_user_id=current_user.id,
                metadata={"permission": permission.value, "role": current_user.role.value},
                request_id=getattr(request.state, "request_id", None),
                ip_hash=client_hash(ip_address, settings),
                succeeded=False,
            )
            await session.commit()
            raise ApiError(403, "permission_denied", "You do not have permission for this action")
        return current_user

    return dependency
