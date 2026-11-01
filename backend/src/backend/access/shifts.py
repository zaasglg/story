"""QR attendance with automatic direction, duplicate protection and scan history."""

import hashlib
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from backend.access.models import PassToken, ShiftScanEvent
from backend.access.rating import sync_attendance_rating
from backend.access.routes import Session, pass_details
from backend.audit.service import add_audit_event
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.errors import ApiError
from backend.core.time import ensure_utc, utc_now
from backend.identity.dependencies import CurrentUser
from backend.identity.models import EmployeeProfile, User, UserRole
from backend.operations.models import AttendanceRecord, AttendanceStatus
from backend.workforce.models import City, WorkerAssignment
from backend.workforce.service import active_assignment

router = APIRouter(tags=["attendance"])


class ShiftScan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qr_payload: str = Field(min_length=20, max_length=128)
    action: Literal["AUTO", "CHECK_IN", "CHECK_OUT"] = "AUTO"


@router.post("/master/shift-scans")
@router.post("/admin/shift-scans")
async def scan_shift(
    payload: ShiftScan,
    session: Session,
    actor: Annotated[CurrentUser, Depends(require_permission(Permission.SHIFT_SCAN))],
) -> dict:
    # Serialize event allocation through commit: a reconnect cursor cannot skip
    # an earlier transaction that commits after a later sequence.
    if session.bind.dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(1917, 35)"))
    token_hash = hashlib.sha256(payload.qr_payload.encode()).hexdigest()
    token = await session.scalar(select(PassToken).where(PassToken.token_hash == token_hash))
    if token is None:
        raise ApiError(422, "invalid_pass_token", "Pass token is invalid")
    # Serialize all QR actions for one worker, including different fresh tokens.
    await session.scalar(select(User).where(User.id == token.worker_user_id).with_for_update())
    now = utc_now()
    open_rows = list(
        (
            await session.scalars(
                select(AttendanceRecord)
                .where(
                    AttendanceRecord.worker_user_id == token.worker_user_id,
                    AttendanceRecord.archived_at.is_(None),
                    AttendanceRecord.check_in.is_not(None),
                    AttendanceRecord.check_out.is_(None),
                )
                .order_by(AttendanceRecord.work_date.desc())
                .with_for_update()
            )
        ).all()
    )
    if len(open_rows) > 1:
        raise ApiError(409, "shift_conflict", "Administrator must correct multiple open shifts")
    action = payload.action
    if action == "AUTO":
        action = "CHECK_OUT" if open_rows else "CHECK_IN"
    details = await pass_details(session, token.worker_user_id)
    if action == "CHECK_IN":
        assignment = await active_assignment(session, token.worker_user_id)
        if assignment is not None:
            await session.refresh(assignment.brigade, with_for_update=True)
            if assignment.work_object is not None:
                await session.refresh(assignment.work_object, with_for_update=True)
        if (
            assignment is None
            or ensure_utc(assignment.effective_from) > now
            or assignment.brigade.archived_at is not None
        ):
            raise ApiError(409, "shift_assignment_required", "An active assignment is required")
        if assignment.work_object is None or assignment.work_object.archived_at is not None:
            raise ApiError(409, "shift_assignment_required", "An active object is required")
        if actor.role == UserRole.MASTER and (
            assignment.master_user_id != actor.id or assignment.brigade.master_user_id != actor.id
        ):
            raise ApiError(403, "shift_team_denied", "Worker is outside your brigade")
        if details["status"] != "ACTIVE":
            raise ApiError(403, "shift_access_blocked", "Work authorization is required")
        city = await session.get(City, assignment.work_object.city_id)
        context = dict(
            object_id=assignment.object_id,
            brigade_id=assignment.brigade_id,
            master_user_id=assignment.master_user_id,
            city_id=assignment.work_object.city_id,
            brigade_name=assignment.brigade.name,
            object_name=assignment.work_object.name,
            timezone=city.timezone if city else "Europe/Moscow",
        )
    else:
        if not open_rows:
            raise ApiError(409, "shift_not_started", "No open shift exists")
        start_event = await session.scalar(
            select(ShiftScanEvent).where(
                ShiftScanEvent.attendance_id == open_rows[0].id, ShiftScanEvent.action == "CHECK_IN"
            )
        )
        if start_event is None:
            raise ApiError(
                409,
                "shift_context_required",
                "Administrator must reconcile the legacy open shift context",
            )
        master_id = start_event.master_user_id
        if master_id is None:
            # Historical context only, never the worker's current assignment.
            master_id = await session.scalar(
                select(WorkerAssignment.master_user_id)
                .where(
                    WorkerAssignment.worker_user_id == token.worker_user_id,
                    WorkerAssignment.brigade_id == start_event.brigade_id,
                    WorkerAssignment.object_id == start_event.object_id,
                    WorkerAssignment.effective_from <= start_event.recorded_at,
                    (WorkerAssignment.effective_to.is_(None))
                    | (WorkerAssignment.effective_to > start_event.recorded_at),
                )
                .order_by(WorkerAssignment.effective_from.desc())
                .limit(1)
            )
        if actor.role == UserRole.MASTER and master_id != actor.id:
            raise ApiError(
                403, "shift_team_denied", "Only the original shift master can close this shift"
            )
        context = {
            key: getattr(start_event, key)
            for key in (
                "object_id",
                "brigade_id",
                "city_id",
                "brigade_name",
                "object_name",
                "timezone",
            )
        }
        context["master_user_id"] = master_id
    zone = ZoneInfo(context["timezone"])
    today = now.astimezone(zone).date()
    consumed = await session.execute(
        update(PassToken)
        .where(
            PassToken.id == token.id,
            PassToken.consumed_at.is_(None),
            PassToken.expires_at > now,
        )
        .values(consumed_at=now)
        .returning(PassToken.id)
        .execution_options(synchronize_session=False)
    )
    if consumed.scalar_one_or_none() is None:
        raise ApiError(422, "invalid_pass_token", "Pass token is expired or already used")
    if action == "CHECK_IN":
        if open_rows:
            raise ApiError(409, "shift_already_started", "Shift has already started")
        item = await session.scalar(
            select(AttendanceRecord)
            .where(
                AttendanceRecord.worker_user_id == token.worker_user_id,
                AttendanceRecord.work_date == today,
            )
            .with_for_update()
        )
        if item is not None:
            raise ApiError(409, "shift_conflict", "Attendance already exists for this date")
        item = AttendanceRecord(
            worker_user_id=token.worker_user_id,
            work_date=today,
            check_in=now,
            status=AttendanceStatus.PRESENT,
            created_by=actor.id,
            updated_by=actor.id,
            created_at=now,
            updated_at=now,
        )
        session.add(item)
    else:
        if not open_rows:
            raise ApiError(409, "shift_not_started", "No open shift exists")
        item = open_rows[0]
        seconds = (now - ensure_utc(item.check_in)).total_seconds()
        if seconds < 120:
            raise ApiError(
                409,
                "shift_scan_too_soon",
                "Wait two minutes after arrival before scanning departure",
            )
        if seconds > 24 * 3600:
            raise ApiError(
                409, "shift_conflict", "An old open shift requires administrator correction"
            )
        item.check_out = now
        item.updated_by, item.updated_at = actor.id, now
    try:
        await session.flush()
        actor_profile = await session.scalar(
            select(EmployeeProfile).where(EmployeeProfile.user_id == actor.id)
        )
        session.add(
            ShiftScanEvent(
                attendance_id=item.id,
                worker_user_id=token.worker_user_id,
                actor_user_id=actor.id,
                **(
                    {
                        "event_sequence": (
                            await session.scalar(
                                select(func.coalesce(func.max(ShiftScanEvent.event_sequence), 0))
                            )
                        )
                        + 1
                    }
                    if session.bind.dialect.name == "sqlite"
                    else {}
                ),
                master_user_id=context["master_user_id"],
                brigade_id=context["brigade_id"],
                object_id=context["object_id"],
                city_id=context["city_id"],
                worker_name=details["full_name"],
                actor_name=actor_profile.full_name if actor_profile else str(actor.id),
                brigade_name=context["brigade_name"],
                object_name=context["object_name"],
                timezone=zone.key,
                action=action,
                work_date=item.work_date,
                recorded_at=now,
            )
        )
        await sync_attendance_rating(session, token.worker_user_id, actor.id)
        add_audit_event(
            session,
            actor_user_id=actor.id,
            action=action,
            entity_type="attendance",
            entity_id=item.id,
            new_value={"source": "QR", "work_date": str(item.work_date)},
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "shift_conflict", "Attendance changed; refresh and retry") from exc
    hours = Decimal(str((now - ensure_utc(item.check_in)).total_seconds())) / Decimal(3600)
    return {
        **details,
        "action": action,
        "state": "WORKING" if item.check_out is None else "FINISHED",
        "work_date": item.work_date,
        "check_in": ensure_utc(item.check_in).astimezone(zone),
        "check_out": ensure_utc(item.check_out).astimezone(zone) if item.check_out else None,
        "timezone": zone.key,
        "hours": hours.quantize(Decimal("0.01")),
        "worked_minutes": max(0, int((now - ensure_utc(item.check_in)).total_seconds() // 60)),
        "recorded_at": now.astimezone(zone),
        "object_id": context["object_id"],
        "object_name": context["object_name"],
        "brigade_name": context["brigade_name"],
        "actor_name": actor_profile.full_name if actor_profile else str(actor.id),
    }
