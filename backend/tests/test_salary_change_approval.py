from datetime import date
from decimal import Decimal

from conftest import login
from sqlalchemy import func, select

from backend.communications.models import Notification, PushOutbox
from backend.core.time import utc_now
from backend.finance.models import FinancialRequest, LedgerCorrection, LedgerEntry
from backend.identity.models import User, UserRole


async def setup_salary(context):
    headers = {}
    for role in UserRole:
        tokens = await login(context, role)
        headers[role] = {"Authorization": f"Bearer {tokens['access_token']}"}
    async with context.database.session_factory() as session:
        users = {u.role: u.id for u in (await session.scalars(select(User))).all()}
        initial = FinancialRequest(
            worker_user_id=users[UserRole.WORKER],
            type="SALARY",
            amount=Decimal("120000"),
            currency="RUB",
            period=date(2026, 10, 1),
            reason="Начальная зарплата",
            status="APPROVED",
            idempotency_key="posted-salary",
            created_by=users[UserRole.ADMIN],
            reviewed_by=users[UserRole.BOSS],
            created_at=utc_now(),
            reviewed_at=utc_now(),
        )
        session.add(initial)
        await session.flush()
        entry = LedgerEntry(
            request_id=initial.id,
            worker_user_id=users[UserRole.WORKER],
            type="SALARY",
            amount=Decimal("120000"),
            currency="RUB",
            period=date(2026, 10, 1),
            created_at=utc_now(),
        )
        session.add(entry)
        await session.commit()
        return headers, str(entry.id)


def proposal(amount="150000.00", revision=0, current="120000.00"):
    return {
        "expected_amount": current,
        "new_amount": amount,
        "expected_revision": revision,
        "reason": "Пересмотр оплаты",
    }


async def total(context, worker):
    response = await context.client.get(
        "/api/v1/workers/me/finance?period=2026-10-01", headers=worker
    )
    return response.json()[0]["salary"]


async def test_salary_changes_only_after_boss_approval_and_notifies(context):
    headers, entry = await setup_salary(context)
    path = f"/api/v1/admin/salary-ledger/{entry}/change-requests"
    admin = {**headers[UserRole.ADMIN], "Idempotency-Key": "request-change"}
    created = await context.client.post(path, headers=admin, json=proposal())
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["type"] == "SALARY_CHANGE" and item["status"] == "PENDING_BOSS"
    assert item["expected_amount"] == "120000.00" and item["amount"] == "150000.00"
    assert await total(context, headers[UserRole.WORKER]) == "120000.00"
    again = await context.client.post(path, headers=admin, json=proposal())
    assert again.json()["id"] == item["id"]
    duplicate = await context.client.post(
        path, headers={**admin, "Idempotency-Key": "second-request"}, json=proposal()
    )
    assert duplicate.status_code == 409
    ledger = await context.client.get("/api/v1/admin/salary-ledger", headers=admin)
    assert ledger.json()[0]["status"] == "PENDING_BOSS"
    notices = await context.client.get("/api/v1/me/notifications", headers=headers[UserRole.BOSS])
    assert len(notices.json()) == 1
    assert "150000.00" in notices.json()[0]["body"]
    pending = await context.client.get(
        "/api/v1/boss/pending-approvals", headers=headers[UserRole.BOSS]
    )
    assert pending.json()[0]["id"] == item["id"]
    decision = f"/api/v1/boss/approvals/{item['id']}/approve"
    for role in (UserRole.ADMIN, UserRole.WORKER, UserRole.MASTER):
        assert (await context.client.post(decision, headers=headers[role])).status_code == 403
    approved = await context.client.post(decision, headers=headers[UserRole.BOSS])
    assert approved.status_code == 200, approved.text
    assert await total(context, headers[UserRole.WORKER]) == "150000.00"
    assert (await context.client.post(decision, headers=headers[UserRole.BOSS])).status_code == 200
    async with context.database.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(LedgerEntry)) == 1
        assert await session.scalar(select(func.count()).select_from(LedgerCorrection)) == 1
        assert await session.scalar(select(func.count()).select_from(PushOutbox)) == 2
        assert await session.scalar(select(LedgerEntry.amount)) == Decimal("120000.00")
    history = await context.client.get(
        f"/api/v1/boss/ledger/{entry}/corrections", headers=headers[UserRole.BOSS]
    )
    assert history.json()[0]["new_amount"] == "150000.00"
    admin_notice = await context.client.get(
        "/api/v1/me/notifications", headers=headers[UserRole.ADMIN]
    )
    assert admin_notice.json()[0]["title"] == "Изменение зарплаты одобрено"


async def test_rejection_cancellation_and_zero_salary(context):
    headers, entry = await setup_salary(context)
    path = f"/api/v1/admin/salary-ledger/{entry}/change-requests"
    for key, decision in (("reject", "reject"), ("cancel", "cancel"), ("zero", "approve")):
        created = await context.client.post(
            path, headers={**headers[UserRole.ADMIN], "Idempotency-Key": key}, json=proposal("0.00")
        )
        assert created.status_code == 201, created.text
        item_id = created.json()["id"]
        if decision == "cancel":
            result = await context.client.post(
                f"/api/v1/admin/financial-requests/{item_id}/cancel",
                headers=headers[UserRole.ADMIN],
            )
        else:
            result = await context.client.post(
                f"/api/v1/boss/approvals/{item_id}/{decision}", headers=headers[UserRole.BOSS]
            )
        assert result.status_code == 200, result.text
        assert await total(context, headers[UserRole.WORKER]) == (
            "0.00" if key == "zero" else "120000.00"
        )
        if key != "zero":
            denied = await context.client.post(
                f"/api/v1/boss/approvals/{item_id}/approve", headers=headers[UserRole.BOSS]
            )
            assert denied.status_code == 409


async def test_stale_request_rejected_even_if_amount_returns_to_original(context):
    headers, entry = await setup_salary(context)
    path = f"/api/v1/admin/salary-ledger/{entry}/change-requests"
    created = await context.client.post(
        path, headers={**headers[UserRole.ADMIN], "Idempotency-Key": "stale"}, json=proposal()
    )
    assert created.status_code == 201
    for key, old, new in (
        ("first", "120000.00", "130000.00"),
        ("second", "130000.00", "120000.00"),
    ):
        corrected = await context.client.post(
            f"/api/v1/boss/ledger/{entry}/corrections",
            headers={**headers[UserRole.BOSS], "Idempotency-Key": key},
            json={"expected_amount": old, "new_amount": new, "reason": "Решение руководителя"},
        )
        assert corrected.status_code == 201, corrected.text
    decision = await context.client.post(
        f"/api/v1/boss/approvals/{created.json()['id']}/approve", headers=headers[UserRole.BOSS]
    )
    assert decision.status_code == 409
    assert await total(context, headers[UserRole.WORKER]) == "120000.00"
    pending = await context.client.get(
        "/api/v1/boss/pending-approvals", headers=headers[UserRole.BOSS]
    )
    assert pending.json()[0]["status"] == "PENDING_BOSS"
    async with context.database.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Notification)) == 1


async def test_salary_request_role_and_payload_guards(context):
    headers, entry = await setup_salary(context)
    path = f"/api/v1/admin/salary-ledger/{entry}/change-requests"
    for role in (UserRole.BOSS, UserRole.MASTER, UserRole.WORKER):
        assert (
            await context.client.get("/api/v1/admin/salary-ledger", headers=headers[role])
        ).status_code == 403
        response = await context.client.post(
            path, headers={**headers[role], "Idempotency-Key": "forbidden"}, json=proposal()
        )
        assert response.status_code == 403
    admin = {**headers[UserRole.ADMIN], "Idempotency-Key": "validations"}
    for data in (proposal("-1"), {**proposal(), "approved": True}, {**proposal(), "reason": "  "}):
        assert (await context.client.post(path, headers=admin, json=data)).status_code == 422
    assert (
        await context.client.post(path, headers=admin, json=proposal("120000.00"))
    ).status_code == 422
    assert (
        await context.client.post(path, headers=admin, json=proposal(current="110000.00"))
    ).status_code == 409
    assert (
        await context.client.post(path, headers=admin, json=proposal(revision=1))
    ).status_code == 409
    assert await total(context, headers[UserRole.WORKER]) == "120000.00"
