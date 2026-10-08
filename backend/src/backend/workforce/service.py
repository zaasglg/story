from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.crypto import PiiCipher
from backend.core.errors import ApiError
from backend.identity.models import EmployeeProfile, User, UserRole
from backend.workforce.models import (
    City,
    EmployeePrivateData,
    WorkerAssignment,
)
from backend.workforce.schemas import (
    AssignmentRead,
    WorkerPrivateRead,
    WorkerPublicRead,
    WorkerTeamRead,
)


async def require_city(session: AsyncSession, city_id: UUID) -> City:
    city = await session.get(City, city_id)
    if city is None:
        raise ApiError(404, "city_not_found", "City was not found")
    return city


async def require_worker(session: AsyncSession, worker_id: UUID) -> User:
    result = await session.execute(
        select(User)
        .where(User.id == worker_id, User.role == UserRole.WORKER, User.archived_at.is_(None))
        .options(selectinload(User.profile))
    )
    worker = result.scalar_one_or_none()
    if worker is None or worker.profile is None or worker.profile.archived_at is not None:
        raise ApiError(404, "worker_not_found", "Worker was not found")
    return worker


async def active_assignment(session: AsyncSession, worker_id: UUID) -> WorkerAssignment | None:
    result = await session.execute(
        select(WorkerAssignment)
        .where(
            WorkerAssignment.worker_user_id == worker_id,
            WorkerAssignment.effective_to.is_(None),
            WorkerAssignment.archived_at.is_(None),
        )
        .options(
            selectinload(WorkerAssignment.brigade),
            selectinload(WorkerAssignment.work_object),
        )
    )
    return result.scalar_one_or_none()


async def city_name(session: AsyncSession, profile: EmployeeProfile) -> str | None:
    if profile.city_id is not None:
        city = await session.get(City, profile.city_id)
        return city.name if city else None
    return profile.city


async def public_projection(session: AsyncSession, worker: User) -> WorkerPublicRead:
    profile = worker.profile
    assert profile is not None
    assignment = await active_assignment(session, worker.id)
    return WorkerPublicRead(
        id=worker.id,
        full_name=profile.full_name,
        role=worker.role,
        photo_url=profile.photo_url,
        city=await city_name(session, profile),
        brigade_name=assignment.brigade.name if assignment else None,
    )


def assignment_projection(assignment: WorkerAssignment | None) -> AssignmentRead | None:
    if assignment is None:
        return None
    return AssignmentRead(
        brigade_id=assignment.brigade_id,
        brigade_name=assignment.brigade.name,
        object_id=assignment.object_id,
        object_name=assignment.work_object.name if assignment.work_object else None,
        master_user_id=assignment.master_user_id,
        effective_from=assignment.effective_from,
    )


async def team_projection(session: AsyncSession, worker: User) -> WorkerTeamRead:
    profile = worker.profile
    assert profile is not None
    assignment = await active_assignment(session, worker.id)
    return WorkerTeamRead(
        **(await public_projection(session, worker)).model_dump(),
        employee_id=profile.employee_id,
        assignment=assignment_projection(assignment),
    )


async def private_projection(
    session: AsyncSession, worker: User, cipher: PiiCipher
) -> WorkerPrivateRead:
    profile = worker.profile
    assert profile is not None
    private = await session.scalar(
        select(EmployeePrivateData).where(EmployeePrivateData.user_id == worker.id)
    )
    passport = (
        cipher.decrypt(private.passport_encrypted)
        if private is not None and private.passport_encrypted
        else None
    )
    return WorkerPrivateRead(
        **(await team_projection(session, worker)).model_dump(),
        phone=worker.phone,
        status=worker.status,
        hire_date=profile.hire_date,
        passport_number=passport,
    )
