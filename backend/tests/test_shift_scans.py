from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from conftest import AppContext, login
from sqlalchemy import select
from test_operations_api import auth, prepare_assignment

from backend.access import routes as pass_routes
from backend.access import shifts
from backend.core.time import utc_now
from backend.identity.models import User, UserRole
from backend.operations.models import AttendanceRecord
from backend.workforce.models import WorkerAssignment


async def setup_shift(context):
    admin = await login(context, UserRole.ADMIN)
    worker_id, _, _, _ = await prepare_assignment(context, admin)
    worker = auth(await login(context, UserRole.WORKER))
    master = auth(await login(context, UserRole.MASTER))
    response = await context.client.get("/api/v1/admin/work-authorizations", headers=auth(admin))
    return admin, worker, master, worker_id, response.json()[0]["id"]


async def qr(context, worker):
    response = await context.client.get("/api/v1/workers/me/pass", headers=worker)
    assert response.status_code == 200
    return response.json()["qr_payload"]


async def scan(context, headers, token, action="CHECK_IN"):
    return await context.client.post(
        "/api/v1/master/shift-scans", headers=headers, json={"qr_payload": token, "action": action}
    )


async def test_arrival_departure_duration_replay_and_duplicate(context: AppContext, monkeypatch):
    admin, worker, master, worker_id, _ = await setup_shift(context)
    start = utc_now() + timedelta(seconds=2)
    monkeypatch.setattr(shifts, "utc_now", lambda: start)
    token = await qr(context, worker)
    arrived = await scan(context, master, token)
    assert arrived.status_code == 200, arrived.text
    assert arrived.json()["state"] == "WORKING"
    assert arrived.json()["worked_minutes"] == 0
    assert arrived.json()["recorded_at"] == arrived.json()["check_in"]
    assert arrived.json()["object_name"] == "Operations Object"
    assert arrived.json()["actor_name"] == "Master User"
    assert "phone" not in arrived.json()
    assert (await scan(context, master, token)).status_code == 422
    duplicate = await scan(context, master, await qr(context, worker))
    assert duplicate.status_code == 409
    monkeypatch.setattr(shifts, "utc_now", lambda: start + timedelta(hours=10, minutes=16))
    # Token issue time stays real; issue a token valid at the simulated departure.
    import hashlib

    from backend.access.models import PassToken

    departure_token = await qr(context, worker)
    async with context.database.session_factory() as session:
        row = await session.scalar(
            select(PassToken).where(
                PassToken.token_hash == hashlib.sha256(departure_token.encode()).hexdigest()
            )
        )
        row.expires_at = start + timedelta(hours=11)
        await session.commit()
    departed = await scan(context, master, departure_token, "CHECK_OUT")
    assert departed.status_code == 200, departed.text
    assert departed.json()["state"] == "FINISHED"
    assert Decimal(str(departed.json()["hours"])) == Decimal("10.27")
    assert departed.json()["worked_minutes"] == 616
    assert departed.json()["recorded_at"] == departed.json()["check_out"]
    history = await context.client.get("/api/v1/workers/me/attendance-history", headers=worker)
    assert history.json()["items"][0]["worked_minutes"] == 616
    summary = await context.client.get(
        "/api/v1/admin/attendance-summary",
        headers=auth(admin),
        params={"work_date": departed.json()["work_date"]},
    )
    assert summary.json()["worked_minutes"] == 616
    assert summary.json()["counts"]["FINISHED"] == 1
    async with context.database.session_factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(AttendanceRecord).where(
                        AttendanceRecord.worker_user_id == UUID(worker_id)
                    )
                )
            ).all()
        )
        assert len(rows) == 1
        assert rows[0].check_out is not None


@pytest.mark.parametrize("role", list(UserRole))
async def test_shift_scan_role_matrix(context: AppContext, role):
    _, worker, _, _, _ = await setup_shift(context)
    response = await scan(context, auth(await login(context, role)), await qr(context, worker))
    assert response.status_code == (200 if role in (UserRole.ADMIN, UserRole.MASTER) else 403)


async def test_scope_blocked_and_missing_start(context: AppContext):
    admin, worker, master, worker_id, authorization_id = await setup_shift(context)
    assert (await scan(context, master, await qr(context, worker), "CHECK_OUT")).status_code == 409
    async with context.database.session_factory() as session:
        assignment = await session.scalar(
            select(WorkerAssignment).where(WorkerAssignment.worker_user_id == UUID(worker_id))
        )
        original = assignment.master_user_id
        assignment.master_user_id = await session.scalar(
            select(User.id).where(User.role == UserRole.ADMIN)
        )
        await session.commit()
    token = await qr(context, worker)
    denied = await scan(context, master, token)
    assert denied.status_code == 403
    async with context.database.session_factory() as session:
        assignment = await session.scalar(
            select(WorkerAssignment).where(WorkerAssignment.worker_user_id == UUID(worker_id))
        )
        assignment.master_user_id = original
        await session.commit()
    revoked = await context.client.post(
        f"/api/v1/admin/work-authorizations/{authorization_id}/revoke", headers=auth(admin)
    )
    assert revoked.status_code == 200, revoked.text
    blocked = await scan(context, master, token)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "shift_access_blocked"
    async with context.database.session_factory() as session:
        assert await session.scalar(select(AttendanceRecord.id)) is None


async def test_overnight_checkout_after_revocation_and_dashboard(context: AppContext, monkeypatch):
    from backend.operations import dashboard

    admin, worker, master, _, authorization_id = await setup_shift(context)
    start = utc_now().replace(hour=20, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(shifts, "utc_now", lambda: start)
    monkeypatch.setattr(pass_routes, "utc_now", lambda: start)
    monkeypatch.setattr(dashboard, "utc_now", lambda: start)
    arrived = await scan(context, master, await qr(context, worker))
    assert arrived.status_code == 200, arrived.text
    end = start + timedelta(hours=8)
    monkeypatch.setattr(shifts, "utc_now", lambda: end)
    monkeypatch.setattr(pass_routes, "utc_now", lambda: end)
    monkeypatch.setattr(dashboard, "utc_now", lambda: end)
    during = (await context.client.get("/api/v1/workers/me/dashboard", headers=worker)).json()
    assert during["state"] == "WORKING"
    assert Decimal(str(during["attendance_hours"])) == 8
    revoked = await context.client.post(
        f"/api/v1/admin/work-authorizations/{authorization_id}/revoke", headers=auth(admin)
    )
    assert revoked.status_code == 200
    departed = await scan(context, master, await qr(context, worker), "CHECK_OUT")
    assert departed.status_code == 200, departed.text
    assert departed.json()["work_date"] == arrived.json()["work_date"]
    assert Decimal(str(departed.json()["hours"])) == 8
    result = (await context.client.get("/api/v1/workers/me/dashboard", headers=worker)).json()
    assert result["state"] == "FINISHED"
    assert Decimal(str(result["attendance_hours"])) == 8


async def test_expired_qr_and_payload_injection(context: AppContext, monkeypatch):
    _, worker, master, _, _ = await setup_shift(context)
    token = await qr(context, worker)
    monkeypatch.setattr(shifts, "utc_now", lambda: utc_now() + timedelta(minutes=3))
    response = await scan(context, master, token)
    assert response.status_code == 422
    injection = await context.client.post(
        "/api/v1/master/shift-scans",
        headers=master,
        json={"qr_payload": token, "action": "CHECK_IN", "role": "ADMIN"},
    )
    assert injection.status_code == 422


async def test_auto_scan_cooldown_history_snapshots_and_summary(context: AppContext, monkeypatch):
    from backend.access import attendance_views
    from backend.access.models import ShiftScanEvent
    from backend.workforce.models import Brigade, WorkObject

    admin, worker, master, worker_id, _ = await setup_shift(context)
    clock = utc_now() + timedelta(seconds=2)
    monkeypatch.setattr(shifts, "utc_now", lambda: clock)
    monkeypatch.setattr(pass_routes, "utc_now", lambda: clock)
    monkeypatch.setattr(attendance_views, "utc_now", lambda: clock)

    async def auto():
        return await context.client.post(
            "/api/v1/master/shift-scans",
            headers=master,
            json={"qr_payload": await qr(context, worker)},
        )

    arrived = await auto()
    assert arrived.status_code == 200, arrived.text
    assert arrived.json()["action"] == "CHECK_IN"
    async with context.database.session_factory() as session:
        assignment = await session.scalar(
            select(WorkerAssignment).where(WorkerAssignment.worker_user_id == UUID(worker_id))
        )
        object_id = str(assignment.object_id)
        obj = await session.get(WorkObject, assignment.object_id)
        original_name = obj.name
        obj.name = "Renamed object"
        brigade = await session.get(Brigade, assignment.brigade_id)
        brigade.name = "Renamed brigade"
        await session.commit()
    clock += timedelta(seconds=119)
    duplicate = await auto()
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "shift_scan_too_soon"
    summary = await context.client.get(
        "/api/v1/admin/attendance-summary",
        headers=auth(admin),
        params={"work_date": arrived.json()["work_date"], "object_id": object_id},
    )
    assert summary.status_code == 200, summary.text
    assert summary.json()["counts"]["WORKING"] == 1
    assert summary.json()["items"][0]["object_name"] == original_name
    clock += timedelta(hours=8)
    departed = await auto()
    assert departed.status_code == 200, departed.text
    assert departed.json()["action"] == "CHECK_OUT"
    after = await auto()
    assert after.status_code == 409
    history = await context.client.get("/api/v1/workers/me/attendance-history", headers=worker)
    assert history.status_code == 200
    assert history.json()["total"] == 1
    item = history.json()["items"][0]
    assert item["state"] == "FINISHED"
    assert item["object_name"] == original_name
    assert [event["action"] for event in item["events"]] == ["CHECK_IN", "CHECK_OUT"]
    assert item["events"][1]["object_name"] == original_name
    assert all(event["actor_name"] == "Master User" for event in item["events"])
    assert "phone" not in history.text and "password" not in history.text
    async with context.database.session_factory() as session:
        events = list((await session.scalars(select(ShiftScanEvent))).all())
        assert len(events) == 2
    empty = await context.client.get(
        "/api/v1/workers/me/attendance-history?offset=1", headers=worker
    )
    assert empty.json()["items"] == [] and empty.json()["total"] == 1
    final = await context.client.get(
        "/api/v1/admin/attendance-summary",
        headers=auth(admin),
        params={"work_date": arrived.json()["work_date"], "object_id": object_id, "limit": 1},
    )
    assert final.json()["counts"]["FINISHED"] == 1


@pytest.mark.parametrize("role", list(UserRole))
async def test_attendance_read_role_matrix(context: AppContext, role):
    headers = auth(await login(context, role))
    history = await context.client.get("/api/v1/workers/me/attendance-history", headers=headers)
    summary = await context.client.get("/api/v1/admin/attendance-summary", headers=headers)
    assert history.status_code == (200 if role == UserRole.WORKER else 403)
    assert summary.status_code == (200 if role == UserRole.ADMIN else 403)
    if role == UserRole.WORKER:
        assert (
            await context.client.get(
                "/api/v1/workers/me/attendance-history?limit=101", headers=headers
            )
        ).status_code == 422


async def test_summary_absence_only_for_scheduled_workers_after_shift_end(
    context: AppContext, monkeypatch
):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from backend.access import attendance_views

    admin, _, _, worker_id, _ = await setup_shift(context)
    async with context.database.session_factory() as session:
        assignment = await session.scalar(
            select(WorkerAssignment).where(WorkerAssignment.worker_user_id == UUID(worker_id))
        )
        brigade_id, object_id = str(assignment.brigade_id), str(assignment.object_id)
    today = utc_now().date()
    plan = await context.client.post(
        "/api/v1/admin/schedules",
        headers=auth(admin),
        json={
            "worker_user_id": worker_id,
            "brigade_id": brigade_id,
            "object_id": object_id,
            "work_date": today.isoformat(),
            "shift_start": "08:00",
            "shift_end": "20:00",
        },
    )
    assert plan.status_code == 201, plan.text
    before_end = datetime.combine(today, datetime.min.time(), ZoneInfo("Europe/Moscow")).replace(
        hour=10
    )
    monkeypatch.setattr(attendance_views, "utc_now", lambda: before_end)
    params = {"work_date": today.isoformat(), "object_id": object_id}
    during = await context.client.get(
        "/api/v1/admin/attendance-summary", headers=auth(admin), params=params
    )
    assert during.status_code == 200, during.text
    assert during.json()["counts"]["NOT_ARRIVED"] == 1
    assert during.json()["counts"]["ABSENT"] == 0
    assert during.json()["scheduled"] == 1
    monkeypatch.setattr(attendance_views, "utc_now", lambda: before_end + timedelta(hours=11))
    after = await context.client.get(
        "/api/v1/admin/attendance-summary", headers=auth(admin), params=params
    )
    assert after.json()["counts"]["ABSENT"] == 1
    empty = await context.client.get(
        "/api/v1/admin/attendance-summary",
        headers=auth(admin),
        params={**params, "object_id": str(UUID(int=0))},
    )
    assert empty.json()["total"] == 0
