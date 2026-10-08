from uuid import UUID

from sqlalchemy import Select

from backend.identity.models import EmployeeProfile


def scope_to_direct_reports(statement: Select, master_user_id: UUID) -> Select:
    """Apply the mandatory MASTER ownership filter to an employee query."""
    return statement.where(
        EmployeeProfile.master_user_id == master_user_id,
        EmployeeProfile.archived_at.is_(None),
    )


def is_own_resource(current_user_id: UUID, resource_user_id: UUID) -> bool:
    return current_user_id == resource_user_id
