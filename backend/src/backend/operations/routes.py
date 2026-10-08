from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.access.models import WorkAuthorization
from backend.access.rating import sync_attendance_rating
from backend.audit.service import add_audit_event
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.errors import ApiError
from backend.core.time import ensure_utc, utc_now
from backend.identity.dependencies import CurrentUser, get_db_session
from backend.identity.models import EmployeeProfile
from backend.operations.models import (
    AttendanceRecord,
    TariffHistory,
    WorkDay,
    WorkSchedule,
)
from backend.operations.schemas import (
    AttendanceCreate,
    AttendanceRead,
    AttendanceUpdate,
    ScheduleCreate,
    ScheduleRead,
    ScheduleUpdate,
    TariffCreate,
    TariffRead,
    WorkDayCreate,
    WorkDayPrivateRead,
    WorkDayTeamRead,
    WorkDayUpdate,
)
from backend.workforce.models import Brigade, City, WorkerAssignment, WorkObject
from backend.workforce.service import require_worker

router = APIRouter(tags=["operations"])
Session = Annotated[AsyncSession, Depends(get_db_session)]


def audit(
    request: Request,
    session: AsyncSession,
    actor: CurrentUser,
    action: str,
    entity_type: str,
    entity_id: UUID,
    fields: set[str] | None = None,
) -> None:
    add_audit_event(
        session,
        actor_user_id=actor.id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        new_value={"fields": sorted(fields)} if fields else None,
        request_id=getattr(request.state, "request_id", None),
    )


async def load_work_day(session: AsyncSession, item_id: UUID) -> WorkDay:
    result = await session.execute(
        select(WorkDay)
        .where(WorkDay.id == item_id, WorkDay.archived_at.is_(None))
        .options(selectinload(WorkDay.brigade), selectinload(WorkDay.work_object))
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise ApiError(404, "work_day_not_found", "Work day was not found")
    return item


async def worker_name(session: AsyncSession, worker_id: UUID) -> str:
    name = await session.scalar(
        select(EmployeeProfile.full_name).where(EmployeeProfile.user_id == worker_id)
    )
    if name is None:
        raise ApiError(404, "worker_not_found", "Worker was not found")
    return name


async def validate_context(
    session: AsyncSession,
    worker_id: UUID,
    brigade_id: UUID,
    object_id: UUID,
    work_date: date,
) -> None:
    await require_worker(session, worker_id)
    city = await session.scalar(select(City).join(Brigade).where(Brigade.id == brigade_id))
    if city is None:
        raise ApiError(422, "assignment_mismatch", "Brigade was not found")
    start = datetime.combine(work_date, time.min, tzinfo=ZoneInfo(city.timezone))
    end = start + timedelta(days=1)
    rows = (
        await session.scalars(
            select(WorkerAssignment).where(
                WorkerAssignment.worker_user_id == worker_id,
                WorkerAssignment.brigade_id == brigade_id,
                WorkerAssignment.object_id == object_id,
                WorkerAssignment.archived_at.is_(None),
            )
        )
    ).all()
    valid = any(
        ensure_utc(row.effective_from) < end
        and (row.effective_to is None or ensure_utc(row.effective_to) > start)
        for row in rows
    )
    if not valid:
        raise ApiError(
            422,
            "assignment_mismatch",
            "Worker must be assigned to this brigade and object on the work date",
        )


async def applicable_tariff(session: AsyncSession, object_id: UUID, work_date) -> TariffHistory:
    tariff = await session.scalar(
        select(TariffHistory)
        .where(
            TariffHistory.object_id == object_id,
            TariffHistory.effective_from <= work_date,
            or_(
                TariffHistory.effective_to.is_(None),
                TariffHistory.effective_to >= work_date,
            ),
            TariffHistory.archived_at.is_(None),
        )
        .order_by(TariffHistory.effective_from.desc())
    )
    if tariff is None:
        raise ApiError(422, "tariff_not_found", "No tariff applies to this object and date")
    return tariff


async def team_projection(session: AsyncSession, item: WorkDay) -> WorkDayTeamRead:
    return WorkDayTeamRead(
        id=item.id,
        worker_user_id=item.worker_user_id,
        worker_name=await worker_name(session, item.worker_user_id),
        object_id=item.object_id,
        object_name=item.work_object.name,
        brigade_id=item.brigade_id,
        brigade_name=item.brigade.name,
        work_date=item.work_date,
        check_in=item.check_in,
        check_out=item.check_out,
        hours=item.hours,
        m3=item.m3,
        status=item.status,
    )


async def private_projection(session: AsyncSession, item: WorkDay) -> WorkDayPrivateRead:
    base = await team_projection(session, item)
    return WorkDayPrivateRead(
        **base.model_dump(),
        rate_per_m3=item.rate_per_m3,
        daily_earnings=item.daily_earnings,
        currency=item.currency,
    )


def work_day_query():
    return (
        select(WorkDay)
        .where(WorkDay.archived_at.is_(None))
        .options(selectinload(WorkDay.brigade), selectinload(WorkDay.work_object))
    )


@router.get("/workers/me/work-days", response_model=list[WorkDayPrivateRead])
async def own_work_days(
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_READ_OWN))],
) -> list[WorkDayPrivateRead]:
    rows = (
        await session.scalars(
            work_day_query()
            .where(WorkDay.worker_user_id == current.id)
            .order_by(WorkDay.work_date.desc())
        )
    ).all()
    return [await private_projection(session, item) for item in rows]


@router.get("/master/team/{worker_id}/work-days", response_model=list[WorkDayTeamRead])
async def master_worker_days(
    worker_id: UUID,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_READ_TEAM))],
) -> list[WorkDayTeamRead]:
    worker = await require_worker(session, worker_id)
    if worker.profile is None or worker.profile.master_user_id != current.id:
        raise ApiError(404, "worker_not_found", "Worker was not found")
    rows = (
        await session.scalars(
            work_day_query()
            .where(WorkDay.worker_user_id == worker_id)
            .order_by(WorkDay.work_date.desc())
        )
    ).all()
    return [await team_projection(session, item) for item in rows]


@router.get("/admin/work-days", response_model=list[WorkDayPrivateRead])
@router.get("/boss/work-days", response_model=list[WorkDayPrivateRead])
async def all_work_days(
    session: Session,
    _: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_READ_ALL))],
) -> list[WorkDayPrivateRead]:
    rows = (await session.scalars(work_day_query().order_by(WorkDay.work_date.desc()))).all()
    return [await private_projection(session, item) for item in rows]


@router.post("/admin/work-days", response_model=WorkDayPrivateRead, status_code=201)
async def create_work_day(
    payload: WorkDayCreate,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_MANAGE))],
) -> WorkDayPrivateRead:
    await validate_context(
        session, payload.worker_user_id, payload.brigade_id, payload.object_id, payload.work_date
    )
    tariff = await applicable_tariff(session, payload.object_id, payload.work_date)
    authorization = await session.scalar(
        select(WorkAuthorization.id)
        .where(
            WorkAuthorization.worker_user_id == payload.worker_user_id,
            WorkAuthorization.valid_from <= payload.work_date,
            WorkAuthorization.valid_until >= payload.work_date,
            WorkAuthorization.revoked_at.is_(None),
        )
        .limit(1)
    )
    if authorization is None:
        raise ApiError(
            422,
            "work_authorization_required",
            "A valid work authorization is required for this work date",
        )
    now = utc_now()
    earnings = (payload.m3 * tariff.rate_per_m3).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    item = WorkDay(
        **payload.model_dump(),
        rate_per_m3=tariff.rate_per_m3,
        daily_earnings=earnings,
        currency=tariff.currency,
        created_by=current.id,
        updated_by=current.id,
        created_at=now,
        updated_at=now,
    )
    session.add(item)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "work_day_conflict", "Work day already exists") from exc
    audit(request, session, current, "WORK_DAY_CREATED", "work_day", item.id)
    await session.commit()
    return await private_projection(session, await load_work_day(session, item.id))


@router.patch("/admin/work-days/{item_id}", response_model=WorkDayPrivateRead)
async def update_work_day(
    item_id: UUID,
    payload: WorkDayUpdate,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_MANAGE))],
) -> WorkDayPrivateRead:
    item = await load_work_day(session, item_id)
    values = payload.model_dump(exclude_unset=True)
    check_in = values.get("check_in", item.check_in)
    check_out = values.get("check_out", item.check_out)
    if check_in and check_out and check_out <= check_in:
        raise ApiError(422, "invalid_work_time", "Check-out must be after check-in")
    for field, value in values.items():
        setattr(item, field, value)
    item.daily_earnings = (item.m3 * item.rate_per_m3).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    item.updated_by = current.id
    item.updated_at = utc_now()
    audit(
        request,
        session,
        current,
        "WORK_DAY_UPDATED",
        "work_day",
        item.id,
        set(values),
    )
    await session.commit()
    return await private_projection(session, await load_work_day(session, item.id))


@router.delete("/admin/work-days/{item_id}", status_code=204)
async def archive_work_day(
    item_id: UUID,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_MANAGE))],
) -> Response:
    item = await load_work_day(session, item_id)
    item.archived_at = item.updated_at = utc_now()
    item.updated_by = current.id
    audit(request, session, current, "WORK_DAY_ARCHIVED", "work_day", item.id)
    await session.commit()
    return Response(status_code=204)


@router.get("/admin/tariffs", response_model=list[TariffRead])
async def list_tariffs(
    session: Session,
    _: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_MANAGE))],
) -> list[TariffRead]:
    rows = (
        await session.scalars(
            select(TariffHistory)
            .where(TariffHistory.archived_at.is_(None))
            .options(selectinload(TariffHistory.work_object))
            .order_by(TariffHistory.object_id, TariffHistory.effective_from.desc())
        )
    ).all()
    return [
        TariffRead(
            id=x.id,
            object_id=x.object_id,
            object_name=x.work_object.name,
            rate_per_m3=x.rate_per_m3,
            currency=x.currency,
            effective_from=x.effective_from,
            effective_to=x.effective_to,
        )
        for x in rows
    ]


@router.post("/admin/tariffs", response_model=TariffRead, status_code=201)
async def create_tariff(
    payload: TariffCreate,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_MANAGE))],
) -> TariffRead:
    obj = await session.get(WorkObject, payload.object_id)
    if obj is None or obj.archived_at is not None:
        raise ApiError(404, "object_not_found", "Object was not found")
    previous = await session.scalar(
        select(TariffHistory).where(
            TariffHistory.object_id == payload.object_id,
            TariffHistory.effective_to.is_(None),
            TariffHistory.archived_at.is_(None),
        )
    )
    if previous and payload.effective_from <= previous.effective_from:
        raise ApiError(409, "tariff_date_conflict", "New tariff must start after current tariff")
    if previous:
        previous.effective_to = payload.effective_from - timedelta(days=1)
    now = utc_now()
    item = TariffHistory(**payload.model_dump(), created_by=current.id, created_at=now)
    session.add(item)
    await session.flush()
    audit(request, session, current, "TARIFF_CREATED", "tariff", item.id)
    await session.commit()
    await session.refresh(item)
    return TariffRead(
        **payload.model_dump(),
        id=item.id,
        object_name=obj.name,
        effective_to=item.effective_to,
    )


async def schedule_projection(session: AsyncSession, item: WorkSchedule) -> ScheduleRead:
    return ScheduleRead(
        id=item.id,
        worker_user_id=item.worker_user_id,
        worker_name=await worker_name(session, item.worker_user_id),
        brigade_id=item.brigade_id,
        brigade_name=item.brigade.name,
        object_id=item.object_id,
        object_name=item.work_object.name,
        work_date=item.work_date,
        shift_start=item.shift_start,
        shift_end=item.shift_end,
    )


def schedule_query():
    return (
        select(WorkSchedule)
        .where(WorkSchedule.archived_at.is_(None))
        .options(selectinload(WorkSchedule.brigade), selectinload(WorkSchedule.work_object))
    )


@router.get("/workers/me/schedules", response_model=list[ScheduleRead])
async def own_schedules(
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_READ_OWN))],
) -> list[ScheduleRead]:
    rows = (
        await session.scalars(schedule_query().where(WorkSchedule.worker_user_id == current.id))
    ).all()
    return [await schedule_projection(session, x) for x in rows]


@router.get("/master/schedules", response_model=list[ScheduleRead])
async def master_schedules(
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_READ_TEAM))],
) -> list[ScheduleRead]:
    worker_ids = select(EmployeeProfile.user_id).where(
        EmployeeProfile.master_user_id == current.id,
        EmployeeProfile.archived_at.is_(None),
    )
    rows = (
        await session.scalars(schedule_query().where(WorkSchedule.worker_user_id.in_(worker_ids)))
    ).all()
    return [await schedule_projection(session, x) for x in rows]


@router.get("/admin/schedules", response_model=list[ScheduleRead])
@router.get("/boss/schedules", response_model=list[ScheduleRead])
async def all_schedules(
    session: Session,
    _: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_READ_ALL))],
) -> list[ScheduleRead]:
    rows = (await session.scalars(schedule_query())).all()
    return [await schedule_projection(session, x) for x in rows]


@router.post("/admin/schedules", response_model=ScheduleRead, status_code=201)
async def create_schedule(
    payload: ScheduleCreate,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_MANAGE))],
) -> ScheduleRead:
    await validate_context(
        session, payload.worker_user_id, payload.brigade_id, payload.object_id, payload.work_date
    )
    now = utc_now()
    item = WorkSchedule(
        **payload.model_dump(),
        created_by=current.id,
        updated_by=current.id,
        created_at=now,
        updated_at=now,
    )
    session.add(item)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "schedule_conflict", "Worker already has a shift on this date") from exc
    audit(request, session, current, "SCHEDULE_CREATED", "work_schedule", item.id)
    await session.commit()
    loaded = (
        await session.execute(schedule_query().where(WorkSchedule.id == item.id))
    ).scalar_one()
    return await schedule_projection(session, loaded)


@router.patch("/admin/schedules/{item_id}", response_model=ScheduleRead)
async def update_schedule(
    item_id: UUID,
    payload: ScheduleUpdate,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_MANAGE))],
) -> ScheduleRead:
    item = await session.scalar(schedule_query().where(WorkSchedule.id == item_id))
    if item is None:
        raise ApiError(404, "schedule_not_found", "Schedule was not found")
    values = payload.model_dump(exclude_unset=True)
    brigade_id = values.get("brigade_id", item.brigade_id)
    object_id = values.get("object_id", item.object_id)
    await validate_context(
        session, item.worker_user_id, brigade_id, object_id, values.get("work_date", item.work_date)
    )
    start = values.get("shift_start", item.shift_start)
    end = values.get("shift_end", item.shift_end)
    if start == end:
        raise ApiError(422, "invalid_shift", "Shift start and end must differ")
    for field, value in values.items():
        setattr(item, field, value)
    item.updated_by, item.updated_at = current.id, utc_now()
    audit(request, session, current, "SCHEDULE_UPDATED", "work_schedule", item.id, set(values))
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "schedule_conflict", "Worker already has a shift on this date") from exc
    loaded = (
        await session.execute(schedule_query().where(WorkSchedule.id == item.id))
    ).scalar_one()
    return await schedule_projection(session, loaded)


@router.delete("/admin/schedules/{item_id}", status_code=204)
async def archive_schedule(
    item_id: UUID,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_MANAGE))],
) -> Response:
    item = await session.scalar(
        select(WorkSchedule).where(WorkSchedule.id == item_id, WorkSchedule.archived_at.is_(None))
    )
    if item is None:
        raise ApiError(404, "schedule_not_found", "Schedule was not found")
    item.archived_at = item.updated_at = utc_now()
    item.updated_by = current.id
    audit(request, session, current, "SCHEDULE_ARCHIVED", "work_schedule", item.id)
    await session.commit()
    return Response(status_code=204)


async def attendance_projection(session: AsyncSession, item: AttendanceRecord) -> AttendanceRead:
    return AttendanceRead(
        id=item.id,
        worker_user_id=item.worker_user_id,
        worker_name=await worker_name(session, item.worker_user_id),
        work_date=item.work_date,
        check_in=item.check_in,
        check_out=item.check_out,
        status=item.status,
        comment=item.comment,
    )


@router.get("/admin/attendance", response_model=list[AttendanceRead])
async def list_attendance(
    session: Session,
    _: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_MANAGE))],
) -> list[AttendanceRead]:
    rows = (
        await session.scalars(
            select(AttendanceRecord)
            .where(AttendanceRecord.archived_at.is_(None))
            .order_by(AttendanceRecord.work_date.desc())
        )
    ).all()
    return [await attendance_projection(session, x) for x in rows]


@router.post("/admin/attendance", response_model=AttendanceRead, status_code=201)
async def create_attendance(
    payload: AttendanceCreate,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_MANAGE))],
) -> AttendanceRead:
    await require_worker(session, payload.worker_user_id)
    now = utc_now()
    item = AttendanceRecord(
        **payload.model_dump(),
        created_by=current.id,
        updated_by=current.id,
        created_at=now,
        updated_at=now,
    )
    session.add(item)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "attendance_conflict", "Attendance already exists") from exc
    audit(request, session, current, "ATTENDANCE_CREATED", "attendance", item.id)
    await sync_attendance_rating(session, item.worker_user_id, current.id)
    await session.commit()
    return await attendance_projection(session, item)


@router.patch("/admin/attendance/{item_id}", response_model=AttendanceRead)
async def update_attendance(
    item_id: UUID,
    payload: AttendanceUpdate,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_MANAGE))],
) -> AttendanceRead:
    item = await session.get(AttendanceRecord, item_id)
    if item is None or item.archived_at is not None:
        raise ApiError(404, "attendance_not_found", "Attendance was not found")
    values = payload.model_dump(exclude_unset=True)
    check_in = values.get("check_in", item.check_in)
    check_out = values.get("check_out", item.check_out)
    if check_in and check_out and check_out <= check_in:
        raise ApiError(422, "invalid_attendance_time", "Check-out must be after check-in")
    for field, value in values.items():
        setattr(item, field, value)
    item.updated_by, item.updated_at = current.id, utc_now()
    audit(request, session, current, "ATTENDANCE_UPDATED", "attendance", item.id, set(values))
    await sync_attendance_rating(session, item.worker_user_id, current.id)
    await session.commit()
    return await attendance_projection(session, item)


@router.delete("/admin/attendance/{item_id}", status_code=204)
async def archive_attendance(
    item_id: UUID,
    request: Request,
    session: Session,
    current: Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_MANAGE))],
) -> Response:
    item = await session.get(AttendanceRecord, item_id)
    if item is None or item.archived_at is not None:
        raise ApiError(404, "attendance_not_found", "Attendance was not found")
    item.archived_at = item.updated_at = utc_now()
    item.updated_by = current.id
    audit(request, session, current, "ATTENDANCE_ARCHIVED", "attendance", item.id)
    await session.commit()
    return Response(status_code=204)
