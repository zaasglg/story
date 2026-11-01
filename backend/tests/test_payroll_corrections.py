from conftest import AppContext, login
from sqlalchemy import select

from backend.finance.models import LedgerEntry
from backend.identity.models import User, UserRole


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_correction_chain_preserves_original_and_enforces_boss(context: AppContext) -> None:
    admin = auth(await login(context, UserRole.ADMIN))
    boss = auth(await login(context, UserRole.BOSS))
    worker = auth(await login(context, UserRole.WORKER))
    master = auth(await login(context, UserRole.MASTER))
    async with context.database.session_factory() as session:
        worker_id = await session.scalar(select(User.id).where(User.role == UserRole.WORKER))
    created = await context.client.post(
        "/api/v1/admin/financial-requests",
        headers={**admin, "Idempotency-Key": "initial-bonus"},
        json={
            "worker_user_id": str(worker_id),
            "type": "BONUS",
            "amount": "5000.00",
            "period": "2026-10-01",
            "reason": "Bonus",
        },
    )
    assert created.status_code == 201
    approved = await context.client.post(
        f"/api/v1/boss/approvals/{created.json()['id']}/approve", headers=boss
    )
    assert approved.status_code == 200
    ledger = await context.client.get("/api/v1/boss/ledger?period=2026-10-01", headers=boss)
    entry_id = ledger.json()[0]["id"]
    path = f"/api/v1/boss/ledger/{entry_id}/corrections"
    payload = {"expected_amount": "5000.00", "new_amount": "3000.00", "reason": "Corrected bonus"}
    for headers in (admin, master, worker):
        denied = await context.client.post(
            path, headers={**headers, "Idempotency-Key": "correction-1"}, json=payload
        )
        assert denied.status_code == 403
    corrected = await context.client.post(
        path, headers={**boss, "Idempotency-Key": "correction-1"}, json=payload
    )
    assert corrected.status_code == 201, corrected.text
    replay = await context.client.post(
        path, headers={**boss, "Idempotency-Key": "correction-1"}, json=payload
    )
    assert replay.json()["id"] == corrected.json()["id"]
    stale = await context.client.post(
        path, headers={**boss, "Idempotency-Key": "correction-stale"}, json=payload
    )
    assert stale.status_code == 409
    final = await context.client.get("/api/v1/workers/me/finance?period=2026-10-01", headers=worker)
    assert final.json()[0]["bonus"] == "3000.00"
    worker_history = await context.client.get(
        "/api/v1/workers/me/finance/history?period=2026-10-01", headers=worker
    )
    assert worker_history.status_code == 200
    assert worker_history.json()[0]["type"] == "BONUS"
    assert worker_history.json()[0]["amount"] == "3000.00"
    assert worker_history.json()[0]["reason"] == "Bonus"
    assert worker_history.json()[0]["corrected"] is True
    second = await context.client.post(
        path,
        headers={**boss, "Idempotency-Key": "correction-2"},
        json={
            "expected_amount": "3000.00",
            "new_amount": "0.00",
            "reason": "Reversed erroneous bonus",
        },
    )
    assert second.status_code == 201
    final = await context.client.get("/api/v1/workers/me/finance?period=2026-10-01", headers=worker)
    assert final.json()[0]["bonus"] == "0.00"
    history = await context.client.get(path, headers=boss)
    assert [item["revision"] for item in history.json()] == [1, 2]
    async with context.database.session_factory() as session:
        original = await session.scalar(select(LedgerEntry))
    assert original is not None
    assert str(original.amount) == "5000.00"
