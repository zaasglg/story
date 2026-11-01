import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.audit.service import add_audit_event
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.config import Settings
from backend.core.crypto import PiiCipher
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.identity.dependencies import CurrentUser, get_app_settings, get_db_session
from backend.identity.models import AccountStatus, EmployeeProfile, User, UserRole
from backend.identity.phone import normalize_phone
from backend.identity.security import hash_password
from backend.workforce.models import (
    Brigade,
    ChangeRequestStatus,
    City,
    EmployeePrivateData,
    ProfileChangeRequest,
    WorkerAssignment,
    WorkObject,
)
from backend.workforce.request_pii import protect_changes, request_read, reveal_changes
from backend.workforce.schemas import (
    AssignmentCreate,
    AssignmentRead,
    BrigadeCreate,
    BrigadeRead,
    BrigadeUpdate,
    ChangeRequestCreate,
    ChangeRequestDecision,
    ChangeRequestRead,
    CityCreate,
    CityRead,
    CityUpdate,
    SalaryAccountWrite,
    WorkerBrigadeRead,
    WorkerCreate,
    WorkerPrivateRead,
    WorkerPublicRead,
    WorkerTeamRead,
    WorkerUpdate,
    WorkObjectCreate,
    WorkObjectRead,
    WorkObjectUpdate,
)
from backend.workforce.service import (
    active_assignment,
    assignment_projection,
    private_projection,
    public_projection,
    require_city,
    require_closed_shift,
    require_context_closed,
    require_worker,
    team_projection,
    validate_worker_city,
)

router = APIRouter(tags=["workforce"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def audit(
    request: Request,
    session: AsyncSession,
    actor: CurrentUser,
    action: str,
    entity: str,
    entity_id: UUID,
    *,
    new_value: dict[str, object] | None = None,
) -> None:
    add_audit_event(
        session,
        actor_user_id=actor.id,
        action=action,
        entity_type=entity,
        entity_id=entity_id,
        new_value=new_value,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/cities", response_model=list[CityRead])
async def list_cities(
    session: Session,
    _: Annotated[CurrentUser, Depends(require_permission(Permission.PROFILE_READ_OWN))],
) -> list[City]:
    return list((await session.scalars(select(City).order_by(City.name))).all())


@router.get("/admin/cities", response_model=list[CityRead])
async def admin_cities(
    session: Session,
    _: Annotated[CurrentUser, Depends(require_permission(Permission.ORGANIZATION_MANAGE))],
) -> list[City]:
    return list((await session.scalars(select(City).order_by(City.name))).all())


async def ensure_city_name(session: AsyncSession, name: str, city_id: UUID | None = None) -> None:
    query = select(City.id).where(func.lower(City.name) == name.lower())
    if city_id is not None:
        query = query.where(City.id != city_id)
    if await session.scalar(query) is not None:
        raise ApiError(409, "city_conflict", "City name already exists")


@router.post("/admin/cities", response_model=CityRead, status_code=201)
async def create_city(
    payload: CityCreate,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.ORGANIZATION_MANAGE))],
) -> City:
    await ensure_city_name(session, payload.name)
    item = City(**payload.model_dump(), created_at=utc_now())
    session.add(item)
    try:
        await session.flush()
        audit(
            request,
            session,
            current,
            "CITY_CREATED",
            "city",
            item.id,
            new_value=payload.model_dump(),
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "city_conflict", "City name already exists") from exc
    return item


@router.patch("/admin/cities/{city_id}", response_model=CityRead)
async def update_city(
    city_id: UUID,
    payload: CityUpdate,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.ORGANIZATION_MANAGE))],
) -> City:
    item = await session.scalar(select(City).where(City.id == city_id).with_for_update())
    if item is None:
        raise ApiError(404, "city_not_found", "City was not found")
    values = payload.model_dump(exclude_unset=True)
    if payload.name is not None:
        await ensure_city_name(session, payload.name, city_id)
    for field, value in values.items():
        setattr(item, field, value)
    try:
        audit(request, session, current, "CITY_UPDATED", "city", city_id, new_value=values)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "city_conflict", "City name already exists") from exc
    return item


@router.get("/workers/me", response_model=WorkerPrivateRead)
async def get_own_profile(
    session: Session,
    settings: SettingsDep,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.PROFILE_READ_OWN))],
) -> WorkerPrivateRead:
    user = await require_worker(session, current.id) if current.role is UserRole.WORKER else None
    if user is None:
        raise ApiError(403, "worker_only", "This endpoint is available to workers only")
    return await private_projection(
        session, user, PiiCipher(settings.pii_encryption_key.get_secret_value())
    )


@router.get("/workers/me/brigade", response_model=list[WorkerBrigadeRead])
async def get_own_brigade(
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.PROFILE_READ_OWN))],
) -> list[WorkerBrigadeRead]:
    if current.role is not UserRole.WORKER:
        raise ApiError(403, "worker_only", "This endpoint is available to workers only")
    assignment = await active_assignment(session, current.id)
    if assignment is None:
        return []
    users = (
        await session.scalars(
            select(User)
            .join(WorkerAssignment, WorkerAssignment.worker_user_id == User.id)
            .where(
                User.role == UserRole.WORKER,
                User.archived_at.is_(None),
                WorkerAssignment.brigade_id == assignment.brigade_id,
                WorkerAssignment.effective_to.is_(None),
                WorkerAssignment.archived_at.is_(None),
            )
            .options(selectinload(User.profile))
            .order_by(User.created_at)
        )
    ).all()
    result = []
    for item in users:
        if item.profile and item.profile.archived_at is None:
            projection = await public_projection(session, item)
            result.append(projection.model_copy(update={"is_self": item.id == current.id}))
    return result


@router.get("/workers/public", response_model=list[WorkerPublicRead])
async def list_public_workers(
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKER_PUBLIC_READ))],
) -> list[WorkerPublicRead]:
    query = select(User).where(User.role == UserRole.WORKER, User.archived_at.is_(None))
    if current.role == UserRole.MASTER:
        query = query.join(EmployeeProfile, EmployeeProfile.user_id == User.id).where(
            EmployeeProfile.master_user_id == current.id
        )
    users = (
        await session.scalars(query.options(selectinload(User.profile)).order_by(User.created_at))
    ).all()
    return [
        await public_projection(session, item)
        for item in users
        if item.profile and item.profile.archived_at is None
    ]


@router.get("/workers/{worker_id}/public", response_model=WorkerPublicRead)
async def get_public_worker(
    worker_id: UUID,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKER_PUBLIC_READ))],
) -> WorkerPublicRead:
    worker = await require_worker(session, worker_id)
    if current.role == UserRole.MASTER and (
        worker.profile is None or worker.profile.master_user_id != current.id
    ):
        raise ApiError(404, "worker_not_found", "Worker was not found")
    return await public_projection(session, worker)


@router.get("/workers/me/change-requests", response_model=list[ChangeRequestRead])
async def own_change_requests(
    session: Session,
    settings: SettingsDep,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.PROFILE_READ_OWN))],
) -> list[ChangeRequestRead]:
    rows = list(
        (
            await session.scalars(
                select(ProfileChangeRequest)
                .where(ProfileChangeRequest.worker_user_id == current.id)
                .order_by(ProfileChangeRequest.created_at.desc())
                .limit(100)
            )
        ).all()
    )

    cipher = PiiCipher(settings.pii_encryption_key.get_secret_value())
    return [request_read(item, cipher) for item in rows]


@router.post("/workers/me/change-request", response_model=ChangeRequestRead, status_code=201)
async def create_change_request(
    payload: ChangeRequestCreate,
    request: Request,
    session: Session,
    settings: SettingsDep,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.PROFILE_READ_OWN))],
) -> ChangeRequestRead:
    if current.role is not UserRole.WORKER:
        raise ApiError(403, "worker_only", "This endpoint is available to workers only")
    changes = payload.model_dump(exclude={"reason"}, exclude_none=True, mode="json")
    cipher = PiiCipher(settings.pii_encryption_key.get_secret_value())
    # Repeated submits after an uncertain network outcome reuse an identical pending request.
    await session.scalar(select(User).where(User.id == current.id).with_for_update())
    pending = (
        await session.scalars(
            select(ProfileChangeRequest).where(
                ProfileChangeRequest.worker_user_id == current.id,
                ProfileChangeRequest.status == ChangeRequestStatus.PENDING,
            )
        )
    ).all()
    for existing in pending:
        if (
            reveal_changes(existing.changes, cipher) == changes
            and existing.reason == payload.reason
        ):
            return request_read(existing, cipher)
    if "city_id" in changes:
        await require_city(session, UUID(str(changes["city_id"])))
    now = utc_now()
    item = ProfileChangeRequest(
        worker_user_id=current.id,
        changes=protect_changes(changes, cipher),
        reason=payload.reason,
        status=ChangeRequestStatus.PENDING,
        created_at=now,
        updated_at=now,
    )
    session.add(item)
    await session.flush()
    audit(
        request,
        session,
        current,
        "PROFILE_CHANGE_REQUESTED",
        "profile_change_request",
        item.id,
        new_value={"fields": sorted(changes)},
    )
    await session.commit()
    await session.refresh(item)
    return request_read(item, cipher)


@router.get("/master/team", response_model=list[WorkerTeamRead])
async def list_master_team(
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.TEAM_READ))],
) -> list[WorkerTeamRead]:
    users = (
        await session.scalars(
            select(User)
            .join(EmployeeProfile, EmployeeProfile.user_id == User.id)
            .where(
                User.role == UserRole.WORKER,
                User.archived_at.is_(None),
                EmployeeProfile.archived_at.is_(None),
                EmployeeProfile.master_user_id == current.id,
            )
            .options(selectinload(User.profile))
            .order_by(EmployeeProfile.full_name)
        )
    ).all()
    return [await team_projection(session, worker) for worker in users]


@router.get("/master/team/{worker_id}", response_model=WorkerTeamRead)
async def get_master_team_worker(
    worker_id: UUID,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.TEAM_READ))],
) -> WorkerTeamRead:
    worker = await require_worker(session, worker_id)
    if worker.profile is None or worker.profile.master_user_id != current.id:
        raise ApiError(404, "worker_not_found", "Worker was not found")
    return await team_projection(session, worker)


@router.get("/master/brigade", response_model=list[BrigadeRead])
async def get_master_brigade(
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.TEAM_READ))],
) -> list[BrigadeRead]:
    rows = (
        await session.scalars(
            select(Brigade)
            .where(Brigade.master_user_id == current.id, Brigade.archived_at.is_(None))
            .options(selectinload(Brigade.city))
        )
    ).all()
    return [
        BrigadeRead(
            id=x.id,
            name=x.name,
            city_id=x.city_id,
            city_name=x.city.name,
            master_user_id=x.master_user_id,
            archived_at=x.archived_at,
        )
        for x in rows
    ]


@router.get("/master/objects", response_model=list[WorkObjectRead])
async def get_master_objects(
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.TEAM_READ))],
) -> list[WorkObjectRead]:
    object_ids = select(WorkerAssignment.object_id).where(
        WorkerAssignment.master_user_id == current.id,
        WorkerAssignment.effective_to.is_(None),
        WorkerAssignment.object_id.is_not(None),
    )
    rows = (
        await session.scalars(
            select(WorkObject)
            .where(WorkObject.id.in_(object_ids), WorkObject.archived_at.is_(None))
            .options(selectinload(WorkObject.city))
        )
    ).all()
    return [object_read(x) for x in rows]


@router.get("/admin/workers", response_model=list[WorkerPrivateRead])
@router.get("/boss/workers", response_model=list[WorkerPrivateRead])
async def list_all_workers(
    session: Session,
    settings: SettingsDep,
    _: Annotated[CurrentUser, Depends(require_permission(Permission.WORKER_READ_ALL))],
) -> list[WorkerPrivateRead]:
    users = (
        await session.scalars(
            select(User)
            .where(User.role == UserRole.WORKER, User.archived_at.is_(None))
            .options(selectinload(User.profile))
            .order_by(User.created_at)
        )
    ).all()
    cipher = PiiCipher(settings.pii_encryption_key.get_secret_value())
    return [
        await private_projection(session, x, cipher)
        for x in users
        if x.profile and x.profile.archived_at is None
    ]


@router.post("/admin/workers", response_model=WorkerPrivateRead, status_code=201)
async def create_worker(
    payload: WorkerCreate,
    request: Request,
    session: Session,
    settings: SettingsDep,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKER_MANAGE))],
) -> WorkerPrivateRead:
    city = await require_city(session, payload.city_id) if payload.city_id else None
    now = utc_now()
    worker = User(
        phone=normalize_phone(payload.phone),
        password_hash=hash_password(payload.password) if payload.password else None,
        role=UserRole.WORKER,
        status=AccountStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    session.add(worker)
    try:
        await session.flush()
        worker.profile = EmployeeProfile(
            user_id=worker.id,
            full_name=payload.full_name,
            position=payload.position,
            employee_id=payload.employee_id,
            city=city.name if city else None,
            city_id=payload.city_id,
            photo_url=payload.photo_url,
            hire_date=payload.hire_date,
            created_at=now,
            updated_at=now,
        )
        if payload.passport_number:
            session.add(
                EmployeePrivateData(
                    user_id=worker.id,
                    passport_encrypted=PiiCipher(
                        settings.pii_encryption_key.get_secret_value()
                    ).encrypt(payload.passport_number),
                    updated_at=now,
                )
            )
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "worker_conflict", "Phone or employee ID already exists") from exc
    audit(
        request,
        session,
        current,
        "WORKER_CREATED",
        "worker",
        worker.id,
        new_value={"employee_id": payload.employee_id},
    )
    await session.commit()
    return await private_projection(
        session,
        await require_worker(session, worker.id),
        PiiCipher(settings.pii_encryption_key.get_secret_value()),
    )


@router.patch("/admin/workers/{worker_id}", response_model=WorkerPrivateRead)
async def update_worker(
    worker_id: UUID,
    payload: WorkerUpdate,
    request: Request,
    session: Session,
    settings: SettingsDep,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKER_MANAGE))],
) -> WorkerPrivateRead:
    await session.scalar(select(User).where(User.id == worker_id).with_for_update())
    worker = await require_worker(session, worker_id)
    profile = worker.profile
    assert profile is not None
    values = payload.model_dump(exclude_unset=True)
    if "city_id" in values:
        await validate_worker_city(session, worker_id, values["city_id"])
        city = await require_city(session, values["city_id"]) if values["city_id"] else None
        profile.city = city.name if city else None
    for field in ("full_name", "position", "employee_id", "city_id", "photo_url", "hire_date"):
        if field in values:
            setattr(profile, field, values[field])
    if "status" in values and values["status"] is not None and values["status"] != worker.status:
        worker.status = values["status"]
        worker.auth_version += 1
    if "passport_number" in values:
        private = await session.scalar(
            select(EmployeePrivateData).where(EmployeePrivateData.user_id == worker.id)
        )
        encrypted = (
            PiiCipher(settings.pii_encryption_key.get_secret_value()).encrypt(
                values["passport_number"]
            )
            if values["passport_number"] is not None
            else None
        )
        if private:
            private.passport_encrypted = encrypted
            private.updated_at = utc_now()
        else:
            session.add(
                EmployeePrivateData(
                    user_id=worker.id, passport_encrypted=encrypted, updated_at=utc_now()
                )
            )
    profile.updated_at = worker.updated_at = utc_now()
    audit(
        request,
        session,
        current,
        "WORKER_UPDATED",
        "worker",
        worker.id,
        new_value={"fields": sorted(values)},
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "worker_conflict", "Employee ID already exists") from exc
    return await private_projection(
        session,
        await require_worker(session, worker.id),
        PiiCipher(settings.pii_encryption_key.get_secret_value()),
    )


@router.delete("/admin/workers/{worker_id}", status_code=204)
async def archive_worker(
    worker_id: UUID,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKER_MANAGE))],
) -> Response:
    await require_closed_shift(session, worker_id)
    worker = await require_worker(session, worker_id)
    now = utc_now()
    worker.status = AccountStatus.ARCHIVED
    worker.archived_at = now
    worker.auth_version += 1
    assert worker.profile is not None
    worker.profile.archived_at = now
    assignment = await active_assignment(session, worker.id)
    if assignment:
        assignment.effective_to = now
    audit(request, session, current, "WORKER_ARCHIVED", "worker", worker.id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/admin/workers/{worker_id}/assignment", response_model=AssignmentRead)
async def assign_worker(
    worker_id: UUID,
    payload: AssignmentCreate,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKER_MANAGE))],
) -> AssignmentRead:
    await session.scalar(select(User).where(User.id == worker_id).with_for_update())
    await require_closed_shift(session, worker_id)
    worker = await require_worker(session, worker_id)
    brigade = await session.get(Brigade, payload.brigade_id, with_for_update=True)
    if brigade is None or brigade.archived_at is not None or brigade.master_user_id is None:
        raise ApiError(422, "invalid_brigade", "An active brigade with a master is required")
    if payload.object_id:
        obj = await session.get(WorkObject, payload.object_id, with_for_update=True)
        if obj is None or obj.archived_at is not None:
            raise ApiError(422, "invalid_object", "An active object is required")
    if payload.object_id and obj.city_id != brigade.city_id:
        raise ApiError(
            422, "assignment_city_mismatch", "Object and brigade must belong to the same city"
        )
    now = utc_now()
    previous = await active_assignment(session, worker.id)
    if previous:
        previous.effective_to = now
    assignment = WorkerAssignment(
        worker_user_id=worker.id,
        brigade_id=brigade.id,
        object_id=payload.object_id,
        master_user_id=brigade.master_user_id,
        effective_from=now,
        assigned_by=current.id,
        created_at=now,
    )
    session.add(assignment)
    assert worker.profile is not None
    worker.profile.master_user_id = brigade.master_user_id
    worker.profile.city_id = brigade.city_id
    worker.profile.city = (await require_city(session, brigade.city_id)).name
    audit(
        request,
        session,
        current,
        "WORKER_ASSIGNED",
        "worker_assignment",
        assignment.id,
        new_value={
            "worker_id": str(worker.id),
            "brigade_id": str(brigade.id),
            "object_id": str(payload.object_id) if payload.object_id else None,
        },
    )
    await session.commit()
    loaded = await active_assignment(session, worker.id)
    assert loaded is not None
    projected = assignment_projection(loaded)
    assert projected is not None
    return projected


@router.delete("/admin/workers/{worker_id}/assignment", status_code=204)
async def end_assignment(
    worker_id: UUID,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKER_MANAGE))],
) -> Response:
    await require_closed_shift(session, worker_id)
    worker = await require_worker(session, worker_id)
    previous = await active_assignment(session, worker.id)
    if previous is not None:
        previous.effective_to = utc_now()
        assert worker.profile is not None
        worker.profile.master_user_id = None
        audit(request, session, current, "WORKER_UNASSIGNED", "worker_assignment", previous.id)
        await session.commit()
    return Response(status_code=204)


@router.get("/admin/masters", response_model=list[WorkerPublicRead])
async def list_masters(
    session: Session,
    _: Annotated[CurrentUser, Depends(require_permission(Permission.WORKER_MANAGE))],
) -> list[WorkerPublicRead]:
    users = (
        await session.scalars(
            select(User)
            .where(
                User.role == UserRole.MASTER,
                User.status == AccountStatus.ACTIVE,
                User.archived_at.is_(None),
            )
            .options(selectinload(User.profile))
        )
    ).all()
    return [await public_projection(session, user) for user in users if user.profile]


def brigade_read(item: Brigade) -> BrigadeRead:
    return BrigadeRead(
        id=item.id,
        name=item.name,
        city_id=item.city_id,
        city_name=item.city.name,
        master_user_id=item.master_user_id,
        archived_at=item.archived_at,
    )


def object_read(item: WorkObject) -> WorkObjectRead:
    return WorkObjectRead(
        id=item.id,
        name=item.name,
        city_id=item.city_id,
        city_name=item.city.name,
        address=item.address,
        latitude=item.latitude,
        longitude=item.longitude,
        status=item.status,
        archived_at=item.archived_at,
    )


@router.get("/admin/brigades", response_model=list[BrigadeRead])
@router.get("/boss/brigades", response_model=list[BrigadeRead])
async def list_brigades(
    session: Session,
    _: Annotated[CurrentUser, Depends(require_permission(Permission.ORGANIZATION_READ))],
) -> list[BrigadeRead]:
    rows = (
        await session.scalars(
            select(Brigade)
            .where(Brigade.archived_at.is_(None))
            .options(selectinload(Brigade.city))
            .order_by(Brigade.name)
        )
    ).all()
    return [brigade_read(x) for x in rows]


@router.post("/admin/brigades", response_model=BrigadeRead, status_code=201)
async def create_brigade(
    payload: BrigadeCreate,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.ORGANIZATION_MANAGE))],
) -> BrigadeRead:
    await require_city(session, payload.city_id)
    if payload.master_user_id:
        master = await session.get(User, payload.master_user_id)
        if (
            master is None
            or master.role is not UserRole.MASTER
            or master.status is not AccountStatus.ACTIVE
        ):
            raise ApiError(422, "invalid_master", "An active master is required")
    now = utc_now()
    item = Brigade(**payload.model_dump(), created_at=now, updated_at=now)
    session.add(item)
    await session.flush()
    audit(request, session, current, "BRIGADE_CREATED", "brigade", item.id)
    await session.commit()
    return brigade_read(
        (
            await session.execute(
                select(Brigade).where(Brigade.id == item.id).options(selectinload(Brigade.city))
            )
        ).scalar_one()
    )


@router.delete("/admin/brigades/{brigade_id}", status_code=204)
async def archive_brigade(
    brigade_id: UUID,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.ORGANIZATION_MANAGE))],
) -> Response:
    item = await session.get(Brigade, brigade_id, with_for_update=True)
    if item is None or item.archived_at is not None:
        raise ApiError(404, "brigade_not_found", "Brigade was not found")
    await require_context_closed(session, "brigade_id", item.id)
    item.archived_at = item.updated_at = utc_now()
    audit(request, session, current, "BRIGADE_ARCHIVED", "brigade", item.id)
    await session.commit()
    return Response(status_code=204)


@router.patch("/admin/brigades/{brigade_id}", response_model=BrigadeRead)
async def update_brigade(
    brigade_id: UUID,
    payload: BrigadeUpdate,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.ORGANIZATION_MANAGE))],
) -> BrigadeRead:
    item = await session.get(Brigade, brigade_id, with_for_update=True)
    if item is None or item.archived_at is not None:
        raise ApiError(404, "brigade_not_found", "Brigade was not found")
    if "master_user_id" in payload.model_fields_set or "city_id" in payload.model_fields_set:
        await require_context_closed(session, "brigade_id", item.id)
    values = payload.model_dump(exclude_unset=True)
    if values.get("city_id") is not None:
        await require_city(session, values["city_id"])
        if values["city_id"] != item.city_id:
            assigned = await session.scalar(
                select(WorkerAssignment.id)
                .where(
                    WorkerAssignment.brigade_id == item.id,
                    WorkerAssignment.effective_to.is_(None),
                    WorkerAssignment.archived_at.is_(None),
                )
                .limit(1)
            )
            if assigned is not None:
                raise ApiError(
                    422,
                    "assignment_city_mismatch",
                    "End active assignments before changing the brigade city",
                )
        conflict = await session.scalar(
            select(WorkerAssignment.id)
            .join(WorkObject, WorkObject.id == WorkerAssignment.object_id)
            .where(
                WorkerAssignment.brigade_id == item.id,
                WorkerAssignment.effective_to.is_(None),
                WorkerAssignment.archived_at.is_(None),
                WorkObject.city_id != values["city_id"],
            )
            .limit(1)
        )
        if conflict is not None:
            raise ApiError(
                422, "assignment_city_mismatch", "City conflicts with an active assignment"
            )
    if values.get("master_user_id") is not None:
        master = await session.get(User, values["master_user_id"])
        if (
            master is None
            or master.role is not UserRole.MASTER
            or master.status is not AccountStatus.ACTIVE
        ):
            raise ApiError(422, "invalid_master", "An active master is required")
    for field, value in values.items():
        setattr(item, field, value)
    item.updated_at = utc_now()
    audit(
        request,
        session,
        current,
        "BRIGADE_UPDATED",
        "brigade",
        item.id,
        new_value={"fields": sorted(values)},
    )
    await session.commit()
    loaded = (
        await session.execute(
            select(Brigade).where(Brigade.id == item.id).options(selectinload(Brigade.city))
        )
    ).scalar_one()
    return brigade_read(loaded)


@router.get("/admin/objects", response_model=list[WorkObjectRead])
@router.get("/boss/objects", response_model=list[WorkObjectRead])
async def list_objects(
    session: Session,
    _: Annotated[CurrentUser, Depends(require_permission(Permission.ORGANIZATION_READ))],
) -> list[WorkObjectRead]:
    rows = (
        await session.scalars(
            select(WorkObject)
            .where(WorkObject.archived_at.is_(None))
            .options(selectinload(WorkObject.city))
            .order_by(WorkObject.name)
        )
    ).all()
    return [object_read(x) for x in rows]


@router.post("/admin/objects", response_model=WorkObjectRead, status_code=201)
async def create_object(
    payload: WorkObjectCreate,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.ORGANIZATION_MANAGE))],
) -> WorkObjectRead:
    await require_city(session, payload.city_id)
    now = utc_now()
    item = WorkObject(**payload.model_dump(), created_at=now, updated_at=now)
    session.add(item)
    await session.flush()
    audit(request, session, current, "OBJECT_CREATED", "work_object", item.id)
    await session.commit()
    return object_read(
        (
            await session.execute(
                select(WorkObject)
                .where(WorkObject.id == item.id)
                .options(selectinload(WorkObject.city))
            )
        ).scalar_one()
    )


@router.delete("/admin/objects/{object_id}", status_code=204)
async def archive_object(
    object_id: UUID,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.ORGANIZATION_MANAGE))],
) -> Response:
    item = await session.get(WorkObject, object_id, with_for_update=True)
    if item is None or item.archived_at is not None:
        raise ApiError(404, "object_not_found", "Object was not found")
    await require_context_closed(session, "object_id", item.id)
    item.archived_at = item.updated_at = utc_now()
    audit(request, session, current, "OBJECT_ARCHIVED", "work_object", item.id)
    await session.commit()
    return Response(status_code=204)


@router.patch("/admin/objects/{object_id}", response_model=WorkObjectRead)
async def update_object(
    object_id: UUID,
    payload: WorkObjectUpdate,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.ORGANIZATION_MANAGE))],
) -> WorkObjectRead:
    item = await session.get(WorkObject, object_id, with_for_update=True)
    if item is None or item.archived_at is not None:
        raise ApiError(404, "object_not_found", "Object was not found")
    values = payload.model_dump(exclude_unset=True)
    if "address" in values and values["address"] != item.address and "latitude" not in values:
        values.update(latitude=None, longitude=None)
    if values.get("city_id") is not None:
        await require_city(session, values["city_id"])
        conflict = await session.scalar(
            select(WorkerAssignment.id)
            .join(Brigade, Brigade.id == WorkerAssignment.brigade_id)
            .where(
                WorkerAssignment.object_id == item.id,
                WorkerAssignment.effective_to.is_(None),
                WorkerAssignment.archived_at.is_(None),
                Brigade.city_id != values["city_id"],
            )
            .limit(1)
        )
        if conflict is not None:
            raise ApiError(
                422, "assignment_city_mismatch", "City conflicts with an active assignment"
            )
    for field, value in values.items():
        setattr(item, field, value)
    item.updated_at = utc_now()
    audit(
        request,
        session,
        current,
        "OBJECT_UPDATED",
        "work_object",
        item.id,
        new_value={"fields": sorted(values)},
    )
    await session.commit()
    loaded = (
        await session.execute(
            select(WorkObject)
            .where(WorkObject.id == item.id)
            .options(selectinload(WorkObject.city))
        )
    ).scalar_one()
    return object_read(loaded)


@router.get("/admin/change-requests", response_model=list[ChangeRequestRead])
async def list_change_requests(
    session: Session,
    settings: SettingsDep,
    _: Annotated[CurrentUser, Depends(require_permission(Permission.WORKER_MANAGE))],
) -> list[ChangeRequestRead]:
    rows = list(
        (
            await session.scalars(
                select(ProfileChangeRequest).order_by(ProfileChangeRequest.created_at.desc())
            )
        ).all()
    )

    cipher = PiiCipher(settings.pii_encryption_key.get_secret_value())
    return [request_read(item, cipher) for item in rows]


@router.post("/admin/change-requests/{request_id}/decision", response_model=ChangeRequestRead)
async def decide_change_request(
    request_id: UUID,
    payload: ChangeRequestDecision,
    request: Request,
    session: Session,
    settings: SettingsDep,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKER_MANAGE))],
) -> ChangeRequestRead:
    item = await session.get(ProfileChangeRequest, request_id)
    if item is None:
        raise ApiError(404, "change_request_not_found", "Change request was not found")
    if item.status is not ChangeRequestStatus.PENDING:
        raise ApiError(409, "change_request_decided", "Change request was already decided")
    now = utc_now()
    if payload.approve:
        await session.scalar(select(User).where(User.id == item.worker_user_id).with_for_update())
        worker = await require_worker(session, item.worker_user_id)
        profile = worker.profile
        assert profile is not None
        changes = reveal_changes(
            item.changes, PiiCipher(settings.pii_encryption_key.get_secret_value())
        )
        if "city_id" in changes:
            city_id = UUID(str(changes.pop("city_id")))
            await validate_worker_city(session, worker.id, city_id)
            city = await require_city(session, city_id)
            profile.city_id, profile.city = city_id, city.name
        if "passport_number" in changes:
            passport = str(changes.pop("passport_number"))
            private = await session.scalar(
                select(EmployeePrivateData).where(EmployeePrivateData.user_id == worker.id)
            )
            encrypted = PiiCipher(settings.pii_encryption_key.get_secret_value()).encrypt(passport)
            if private:
                private.passport_encrypted, private.updated_at = encrypted, now
            else:
                session.add(
                    EmployeePrivateData(
                        user_id=worker.id, passport_encrypted=encrypted, updated_at=now
                    )
                )
        for field in ("full_name", "photo_url"):
            if field in changes:
                setattr(profile, field, changes[field])
        profile.updated_at = now
        item.status = ChangeRequestStatus.APPROVED
    else:
        item.status = ChangeRequestStatus.REJECTED
    item.reviewed_by, item.reviewed_at, item.updated_at = current.id, now, now
    item.review_note = payload.review_note
    audit(
        request,
        session,
        current,
        "PROFILE_CHANGE_DECIDED",
        "profile_change_request",
        item.id,
        new_value={"status": item.status.value},
    )
    await session.commit()
    await session.refresh(item)
    return request_read(item, PiiCipher(settings.pii_encryption_key.get_secret_value()))


@router.get("/admin/workers/{worker_id}/salary-account", response_model=SalaryAccountWrite | None)
async def read_salary_account(
    worker_id: UUID,
    session: Session,
    settings: SettingsDep,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKER_BANK_READ_ALL))],
    request: Request,
) -> SalaryAccountWrite | None:
    await require_worker(session, worker_id)
    private = await session.scalar(
        select(EmployeePrivateData).where(EmployeePrivateData.user_id == worker_id)
    )
    result = None
    if private and private.salary_account_encrypted:
        result = SalaryAccountWrite.model_validate_json(
            PiiCipher(settings.pii_encryption_key.get_secret_value()).decrypt(
                private.salary_account_encrypted
            )
        )
    audit(request, session, current, "SALARY_ACCOUNT_READ", "worker", worker_id)
    await session.commit()
    return result


@router.get("/workers/me/salary-account", response_model=SalaryAccountWrite | None)
async def read_own_salary_account(
    session: Session,
    settings: SettingsDep,
    request: Request,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKER_BANK_READ_OWN))],
) -> SalaryAccountWrite | None:
    return await read_salary_account(current.id, session, settings, current, request)


@router.put("/admin/workers/{worker_id}/salary-account", response_model=SalaryAccountWrite)
async def write_salary_account(
    worker_id: UUID,
    payload: SalaryAccountWrite,
    session: Session,
    settings: SettingsDep,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.COMPANY_BANK_MANAGE))],
    request: Request,
) -> SalaryAccountWrite:
    await require_worker(session, worker_id)
    private = await session.scalar(
        select(EmployeePrivateData).where(EmployeePrivateData.user_id == worker_id)
    )
    if private is None:
        private = EmployeePrivateData(user_id=worker_id, updated_at=utc_now())
        session.add(private)
    private.salary_account_encrypted = PiiCipher(
        settings.pii_encryption_key.get_secret_value()
    ).encrypt(json.dumps(payload.model_dump(), ensure_ascii=False))
    private.updated_at = utc_now()
    audit(request, session, current, "SALARY_ACCOUNT_UPDATED", "worker", worker_id)
    await session.commit()
    return payload


@router.delete("/admin/workers/{worker_id}/salary-account", status_code=204)
async def delete_salary_account(
    worker_id: UUID,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.COMPANY_BANK_MANAGE))],
    request: Request,
) -> None:
    await require_worker(session, worker_id)
    private = await session.scalar(
        select(EmployeePrivateData).where(EmployeePrivateData.user_id == worker_id)
    )
    if private:
        private.salary_account_encrypted = None
        private.updated_at = utc_now()
    audit(request, session, current, "SALARY_ACCOUNT_DELETED", "worker", worker_id)
    await session.commit()
