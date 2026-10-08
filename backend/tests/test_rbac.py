from uuid import uuid4

import pytest
from conftest import AppContext
from sqlalchemy import select
from starlette.requests import Request

from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import ROLE_PERMISSIONS, Permission, has_permission
from backend.core.errors import ApiError
from backend.identity.dependencies import CurrentUser
from backend.identity.models import AuditLog, User, UserRole


def test_permission_matrix_is_explicit_for_all_roles() -> None:
    assert set(ROLE_PERMISSIONS) == set(UserRole)
    assert all(ROLE_PERMISSIONS[role] for role in UserRole)


def test_admin_financial_secrets_are_denied() -> None:
    forbidden = {
        Permission.REVENUE_READ,
        Permission.REVENUE_MANAGE,
        Permission.PROFIT_READ,
        Permission.COMPANY_BANK_READ,
        Permission.COMPANY_BANK_MANAGE,
        Permission.FINANCIAL_REQUEST_APPROVE,
    }
    assert forbidden.isdisjoint(ROLE_PERMISSIONS[UserRole.ADMIN])
    assert forbidden.issubset(ROLE_PERMISSIONS[UserRole.BOSS])


def test_master_has_team_read_without_write_or_money() -> None:
    allowed = ROLE_PERMISSIONS[UserRole.MASTER]
    assert Permission.TEAM_READ in allowed
    assert Permission.WORKDAY_READ_TEAM in allowed
    assert Permission.WORKDAY_MANAGE not in allowed
    assert Permission.EXPENSE_READ not in allowed
    assert Permission.WORKER_BANK_READ_ALL not in allowed
    assert Permission.REVENUE_READ not in allowed


def test_worker_cannot_read_private_cross_worker_data() -> None:
    allowed = ROLE_PERMISSIONS[UserRole.WORKER]
    assert Permission.WORKER_PUBLIC_READ in allowed
    assert Permission.WORKER_READ_ALL not in allowed
    assert Permission.WORKDAY_READ_ALL not in allowed
    assert Permission.WORKER_BANK_READ_ALL not in allowed


def test_boss_expense_crud_is_explicit_from_role_matrix() -> None:
    assert has_permission(UserRole.BOSS, Permission.EXPENSE_READ)
    assert has_permission(UserRole.BOSS, Permission.EXPENSE_MANAGE)


def test_roles_do_not_inherit_from_each_other() -> None:
    assert not ROLE_PERMISSIONS[UserRole.ADMIN].issubset(ROLE_PERMISSIONS[UserRole.BOSS])
    assert Permission.WORKER_MANAGE in ROLE_PERMISSIONS[UserRole.ADMIN]
    assert Permission.WORKER_MANAGE not in ROLE_PERMISSIONS[UserRole.BOSS]


async def test_denied_permission_is_audited(context: AppContext) -> None:
    async with context.database.session_factory() as session:
        admin = (
            await session.execute(select(User).where(User.role == UserRole.ADMIN))
        ).scalar_one()
        current = CurrentUser(
            id=admin.id,
            role=admin.role,
            session_id=uuid4(),
            auth_version=admin.auth_version,
        )
        request = Request({"type": "http", "client": ("127.0.0.1", 1234), "headers": []})
        request.state.request_id = "rbac-test"
        dependency = require_permission(Permission.PROFIT_READ)

        with pytest.raises(ApiError) as error:
            await dependency(
                current_user=current,
                request=request,
                session=session,
                settings=context.settings,
            )
        assert error.value.status_code == 403

    async with context.database.session_factory() as session:
        event = (
            await session.execute(select(AuditLog).where(AuditLog.action == "AUTHORIZATION_DENIED"))
        ).scalar_one()
    assert event.actor_user_id == admin.id
    assert event.metadata_json == {"permission": "profit:read", "role": "ADMIN"}
