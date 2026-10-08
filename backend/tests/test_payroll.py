from uuid import UUID

from conftest import AppContext, login
from sqlalchemy import select
from test_operations_api import prepare_assignment

from backend.finance.models import LedgerEntry
from backend.identity.models import User, UserRole


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_approval_idempotency_rejection_and_payroll_scope(context: AppContext) -> None:
    admin = auth(await login(context, UserRole.ADMIN))
    boss = auth(await login(context, UserRole.BOSS))
    worker = auth(await login(context, UserRole.WORKER))
    master = auth(await login(context, UserRole.MASTER))
    async with context.database.session_factory() as session:
        worker_id = await session.scalar(select(User.id).where(User.role == UserRole.WORKER))
    payload = {
        "worker_user_id": str(worker_id),
        "type": "BONUS",
        "amount": "5000.00",
        "period": "2026-10-01",
        "reason": "Good work",
        "currency": "RUB",
    }
    created = await context.client.post(
        "/api/v1/admin/financial-requests",
        headers={**admin, "Idempotency-Key": "bonus-1"},
        json=payload,
    )
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]
    assert created.json()["status"] == "PENDING_BOSS"
    replay = await context.client.post(
        "/api/v1/admin/financial-requests",
        headers={**admin, "Idempotency-Key": "bonus-1"},
        json=payload,
    )
    assert replay.json()["id"] == item_id
    conflict = await context.client.post(
        "/api/v1/admin/financial-requests",
        headers={**admin, "Idempotency-Key": "bonus-1"},
        json={**payload, "amount": "999.00"},
    )
    assert conflict.status_code == 409
    pending_pay = await context.client.get(
        "/api/v1/workers/me/finance?period=2026-10-01", headers=worker
    )
    assert pending_pay.json() == []
    for headers in (admin, worker, master):
        denied = await context.client.post(
            f"/api/v1/boss/approvals/{item_id}/approve", headers=headers
        )
        assert denied.status_code == 403
    for _ in range(2):
        approved = await context.client.post(
            f"/api/v1/boss/approvals/{item_id}/approve", headers=boss
        )
        assert approved.status_code == 200, approved.text
    async with context.database.session_factory() as session:
        entries = (
            await session.scalars(
                select(LedgerEntry).where(LedgerEntry.request_id == UUID(item_id))
            )
        ).all()
    assert len(entries) == 1
    final = await context.client.get("/api/v1/workers/me/finance?period=2026-10-01", headers=worker)
    assert final.json()[0]["bonus"] == "5000.00"
    assert final.json()[0]["remaining"] == "5000.00"
    for headers in (worker, master):
        denied = await context.client.get(
            "/api/v1/admin/payroll?period=2026-10-01", headers=headers
        )
        assert denied.status_code == 403
    rejected_request = await context.client.post(
        "/api/v1/admin/financial-requests",
        headers={**admin, "Idempotency-Key": "bonus-2"},
        json=payload,
    )
    rejected_id = rejected_request.json()["id"]
    rejected = await context.client.post(
        f"/api/v1/boss/approvals/{rejected_id}/reject", headers=boss
    )
    assert rejected.json()["status"] == "REJECTED"
    forbidden_change = await context.client.post(
        f"/api/v1/boss/approvals/{rejected_id}/approve", headers=boss
    )
    assert forbidden_change.status_code == 409
    final = await context.client.get("/api/v1/workers/me/finance?period=2026-10-01", headers=worker)
    assert final.json()[0]["bonus"] == "5000.00"


async def test_salary_cannot_be_arbitrary_or_role_injected(context: AppContext) -> None:
    admin = auth(await login(context, UserRole.ADMIN))
    async with context.database.session_factory() as session:
        worker_id = await session.scalar(select(User.id).where(User.role == UserRole.WORKER))
    payload = {
        "worker_user_id": str(worker_id),
        "type": "SALARY",
        "amount": "10000.00",
        "period": "2026-10-01",
        "reason": "Salary",
    }
    result = await context.client.post(
        "/api/v1/admin/financial-requests",
        headers={**admin, "Idempotency-Key": "salary-test"},
        json=payload,
    )
    assert result.status_code == 422
    injected = await context.client.post(
        "/api/v1/admin/financial-requests",
        headers={**admin, "Idempotency-Key": "salary-test"},
        json={**payload, "status": "APPROVED"},
    )
    assert injected.status_code == 422


async def test_salary_snapshot_stale_request_and_duplicate_month(context: AppContext) -> None:
    admin_tokens = await login(context, UserRole.ADMIN)
    admin = auth(admin_tokens)
    boss = auth(await login(context, UserRole.BOSS))
    worker_id, _, brigade_id, object_id = await prepare_assignment(context, admin_tokens)
    tariff = await context.client.post(
        "/api/v1/admin/tariffs",
        headers=admin,
        json={"object_id": object_id, "rate_per_m3": "100.00", "effective_from": "2026-01-01"},
    )
    assert tariff.status_code == 201
    day = await context.client.post(
        "/api/v1/admin/work-days",
        headers=admin,
        json={
            "worker_user_id": worker_id,
            "object_id": object_id,
            "brigade_id": brigade_id,
            "work_date": "2026-10-01",
            "hours": "8.00",
            "m3": "10.00",
            "status": "APPROVED",
        },
    )
    assert day.status_code == 201
    payload = {
        "worker_user_id": worker_id,
        "type": "SALARY",
        "amount": "1000.00",
        "period": "2026-10-01",
        "reason": "Monthly payroll",
    }
    pending = await context.client.post(
        "/api/v1/admin/financial-requests",
        headers={**admin, "Idempotency-Key": "salary-old"},
        json=payload,
    )
    assert pending.status_code == 201
    changed = await context.client.patch(
        f"/api/v1/admin/work-days/{day.json()['id']}", headers=admin, json={"m3": "12.00"}
    )
    assert changed.status_code == 200
    stale = await context.client.post(
        f"/api/v1/boss/approvals/{pending.json()['id']}/approve", headers=boss
    )
    assert stale.status_code == 409
    cancelled = await context.client.post(
        f"/api/v1/admin/financial-requests/{pending.json()['id']}/cancel", headers=admin
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    payload["amount"] = "1200.00"
    requests = []
    for key in ("salary-new", "salary-duplicate"):
        created = await context.client.post(
            "/api/v1/admin/financial-requests",
            headers={**admin, "Idempotency-Key": key},
            json=payload,
        )
        assert created.status_code == 201
        requests.append(created.json()["id"])
    approved = await context.client.post(
        f"/api/v1/boss/approvals/{requests[0]}/approve", headers=boss
    )
    assert approved.status_code == 200
    duplicate = await context.client.post(
        f"/api/v1/boss/approvals/{requests[1]}/approve", headers=boss
    )
    assert duplicate.status_code == 409
    totals = await context.client.get("/api/v1/admin/payroll?period=2026-10-01", headers=admin)
    assert totals.json()[0]["salary"] == "1200.00"
