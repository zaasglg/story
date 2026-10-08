from enum import StrEnum

from backend.identity.models import UserRole


class Permission(StrEnum):
    PROFILE_READ_OWN = "profile:read:own"
    WORKER_PUBLIC_READ = "worker:read:public"
    WORKER_READ_ALL = "worker:read:all"
    WORKER_MANAGE = "worker:manage"
    TEAM_READ = "team:read"
    WORKDAY_READ_OWN = "workday:read:own"
    WORKDAY_READ_TEAM = "workday:read:team"
    WORKDAY_READ_ALL = "workday:read:all"
    WORKDAY_MANAGE = "workday:manage"
    ORGANIZATION_READ = "organization:read"
    ORGANIZATION_MANAGE = "organization:manage"
    EQUIPMENT_READ_OWN = "equipment:read:own"
    EQUIPMENT_READ_ALL = "equipment:read:all"
    EQUIPMENT_MANAGE = "equipment:manage"
    EXPENSE_READ = "expense:read"
    EXPENSE_MANAGE = "expense:manage"
    EXPENSE_APPROVE = "expense:approve"
    REVENUE_READ = "revenue:read"
    REVENUE_MANAGE = "revenue:manage"
    PROFIT_READ = "profit:read"
    WORKER_BANK_READ_OWN = "worker_bank:read:own"
    WORKER_BANK_READ_ALL = "worker_bank:read:all"
    COMPANY_BANK_READ = "company_bank:read"
    COMPANY_BANK_MANAGE = "company_bank:manage"
    FINANCIAL_REQUEST_CREATE = "financial_request:create"
    FINANCIAL_REQUEST_APPROVE = "financial_request:approve"
    AUDIT_READ_OWN = "audit:read:own"
    AUDIT_READ_ADMIN = "audit:read:admin"
    AUDIT_READ_ALL = "audit:read:all"
    ANNOUNCEMENT_RECEIVE = "announcement:receive"
    ANNOUNCEMENT_CREATE = "announcement:create"
    CHAT_USE = "chat:use"
    USER_ROLE_MANAGE = "user_role:manage"


ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    UserRole.WORKER: frozenset(
        {
            Permission.PROFILE_READ_OWN,
            Permission.WORKER_PUBLIC_READ,
            Permission.WORKDAY_READ_OWN,
            Permission.EQUIPMENT_READ_OWN,
            Permission.WORKER_BANK_READ_OWN,
            Permission.AUDIT_READ_OWN,
            Permission.ANNOUNCEMENT_RECEIVE,
            Permission.CHAT_USE,
        }
    ),
    UserRole.MASTER: frozenset(
        {
            Permission.PROFILE_READ_OWN,
            Permission.WORKER_PUBLIC_READ,
            Permission.TEAM_READ,
            Permission.WORKDAY_READ_TEAM,
            Permission.ORGANIZATION_READ,
            Permission.ANNOUNCEMENT_RECEIVE,
            Permission.CHAT_USE,
        }
    ),
    UserRole.ADMIN: frozenset(
        {
            Permission.PROFILE_READ_OWN,
            Permission.WORKER_PUBLIC_READ,
            Permission.WORKER_READ_ALL,
            Permission.WORKER_MANAGE,
            Permission.WORKDAY_READ_ALL,
            Permission.WORKDAY_MANAGE,
            Permission.ORGANIZATION_READ,
            Permission.ORGANIZATION_MANAGE,
            Permission.EQUIPMENT_READ_ALL,
            Permission.EQUIPMENT_MANAGE,
            Permission.EXPENSE_READ,
            Permission.EXPENSE_MANAGE,
            Permission.WORKER_BANK_READ_ALL,
            Permission.FINANCIAL_REQUEST_CREATE,
            Permission.AUDIT_READ_ADMIN,
            Permission.ANNOUNCEMENT_RECEIVE,
            Permission.ANNOUNCEMENT_CREATE,
            Permission.CHAT_USE,
        }
    ),
    UserRole.BOSS: frozenset(
        {
            Permission.EXPENSE_APPROVE,
            Permission.PROFILE_READ_OWN,
            Permission.WORKER_PUBLIC_READ,
            Permission.WORKER_READ_ALL,
            Permission.WORKDAY_READ_ALL,
            Permission.ORGANIZATION_READ,
            Permission.EQUIPMENT_READ_ALL,
            Permission.EXPENSE_READ,
            Permission.EXPENSE_MANAGE,
            Permission.REVENUE_READ,
            Permission.REVENUE_MANAGE,
            Permission.PROFIT_READ,
            Permission.WORKER_BANK_READ_ALL,
            Permission.COMPANY_BANK_READ,
            Permission.COMPANY_BANK_MANAGE,
            Permission.FINANCIAL_REQUEST_APPROVE,
            Permission.AUDIT_READ_ALL,
            Permission.ANNOUNCEMENT_RECEIVE,
            Permission.ANNOUNCEMENT_CREATE,
            Permission.CHAT_USE,
            Permission.USER_ROLE_MANAGE,
        }
    ),
}


def has_permission(role: UserRole, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]
