import pytest
from sqlalchemy import func, select
from test_salary_change_approval import setup_salary

from backend.finance.models import LedgerEntry
from backend.identity.models import UserRole


@pytest.mark.parametrize(
    "kind,amount,field,monthly,remaining",
    [
        ("BONUS", "10000.00", "bonus", "130000.00", "130000.00"),
        ("PENALTY", "5000.00", "penalty", "115000.00", "115000.00"),
        ("ADVANCE", "30000.00", "advance", "120000.00", "90000.00"),
    ],
)
async def test_adjustment_enters_all_totals_only_after_boss_approval(
    context, kind, amount, field, monthly, remaining
):
    headers, _ = await setup_salary(context)
    ledger = await context.client.get(
        "/api/v1/admin/salary-ledger", headers=headers[UserRole.ADMIN]
    )
    worker_id = ledger.json()[0]["worker_user_id"]
    payload = {
        "worker_user_id": worker_id,
        "type": kind,
        "amount": amount,
        "currency": "RUB",
        "period": "2026-10-01",
        "reason": "Основание операции",
    }
    admin = {**headers[UserRole.ADMIN], "Idempotency-Key": "adjustment"}
    for role in (UserRole.BOSS, UserRole.MASTER, UserRole.WORKER):
        denied = await context.client.post(
            "/api/v1/admin/financial-requests",
            headers={**headers[role], "Idempotency-Key": "forbidden"},
            json=payload,
        )
        assert denied.status_code == 403
    created = await context.client.post(
        "/api/v1/admin/financial-requests", headers=admin, json=payload
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["status"] == "PENDING_BOSS"
    replay = await context.client.post(
        "/api/v1/admin/financial-requests", headers=admin, json=payload
    )
    assert replay.json()["id"] == item["id"]
    paths = (
        (UserRole.WORKER, "/workers/me/finance"),
        (UserRole.ADMIN, "/admin/payroll"),
        (UserRole.BOSS, "/boss/payroll"),
    )
    for role, path in paths:
        result = await context.client.get(f"/api/v1{path}?period=2026-10-01", headers=headers[role])
        assert result.json()[0][field] == "0.00"
        assert result.json()[0]["monthly_pay"] == "120000.00"
        assert result.json()[0]["remaining"] == "120000.00"
    history = await context.client.get(
        "/api/v1/workers/me/finance/history?period=2026-10-01", headers=headers[UserRole.WORKER]
    )
    assert [row["type"] for row in history.json()] == ["SALARY"]
    notices = await context.client.get("/api/v1/me/notifications", headers=headers[UserRole.BOSS])
    assert len(notices.json()) == 1
    assert amount in notices.json()[0]["body"]
    decision = f"/api/v1/boss/approvals/{item['id']}/approve"
    for role in (UserRole.ADMIN, UserRole.MASTER, UserRole.WORKER):
        assert (await context.client.post(decision, headers=headers[role])).status_code == 403
    for _ in range(2):
        approved = await context.client.post(decision, headers=headers[UserRole.BOSS])
        assert approved.status_code == 200, approved.text
    for role, path in paths:
        result = await context.client.get(f"/api/v1{path}?period=2026-10-01", headers=headers[role])
        assert result.json()[0][field] == amount
        assert result.json()[0]["monthly_pay"] == monthly
        assert result.json()[0]["remaining"] == remaining
    history = await context.client.get(
        "/api/v1/workers/me/finance/history?period=2026-10-01", headers=headers[UserRole.WORKER]
    )
    assert sum(row["type"] == kind for row in history.json()) == 1
    async with context.database.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(LedgerEntry)) == 2
    notice = await context.client.get("/api/v1/me/notifications", headers=headers[UserRole.ADMIN])
    assert notice.json()[0]["title"] == "Финансовая заявка одобрена"


@pytest.mark.parametrize("kind", ["BONUS", "PENALTY", "ADVANCE"])
@pytest.mark.parametrize("decision", ["reject", "cancel"])
async def test_rejected_and_cancelled_adjustments_never_enter_payroll(context, kind, decision):
    headers, _ = await setup_salary(context)
    ledger = await context.client.get(
        "/api/v1/admin/salary-ledger", headers=headers[UserRole.ADMIN]
    )
    payload = {
        "worker_user_id": ledger.json()[0]["worker_user_id"],
        "type": kind,
        "amount": "1000.00",
        "period": "2026-10-01",
        "reason": "Основание",
    }
    created = await context.client.post(
        "/api/v1/admin/financial-requests",
        json=payload,
        headers={**headers[UserRole.ADMIN], "Idempotency-Key": "reject-test"},
    )
    assert created.status_code == 201
    item_id = created.json()["id"]
    path = (
        f"/api/v1/boss/approvals/{item_id}/reject"
        if decision == "reject"
        else f"/api/v1/admin/financial-requests/{item_id}/cancel"
    )
    result = await context.client.post(
        path, headers=headers[UserRole.BOSS if decision == "reject" else UserRole.ADMIN]
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == ("REJECTED" if decision == "reject" else "CANCELLED")
    assert (
        await context.client.post(
            f"/api/v1/boss/approvals/{item_id}/approve", headers=headers[UserRole.BOSS]
        )
    ).status_code == 409
    total = await context.client.get(
        "/api/v1/workers/me/finance?period=2026-10-01", headers=headers[UserRole.WORKER]
    )
    assert total.json()[0]["monthly_pay"] == "120000.00"
    assert total.json()[0]["remaining"] == "120000.00"
    async with context.database.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(LedgerEntry)) == 1


@pytest.mark.parametrize("kind", ["BONUS", "PENALTY", "ADVANCE"])
async def test_adjustment_cannot_inject_approval_or_negative_amount(context, kind):
    headers, _ = await setup_salary(context)
    ledger = await context.client.get(
        "/api/v1/admin/salary-ledger", headers=headers[UserRole.ADMIN]
    )
    payload = {
        "worker_user_id": ledger.json()[0]["worker_user_id"],
        "type": kind,
        "amount": "1000.00",
        "period": "2026-10-01",
        "reason": "Основание",
    }
    for changes in (
        {"amount": "-5000"},
        {"amount": "0"},
        {"status": "APPROVED"},
        {"reviewed_by": ledger.json()[0]["worker_user_id"]},
        {"reason": "   "},
    ):
        result = await context.client.post(
            "/api/v1/admin/financial-requests",
            headers={**headers[UserRole.ADMIN], "Idempotency-Key": "invalid"},
            json={**payload, **changes},
        )
        assert result.status_code == 422, result.text
