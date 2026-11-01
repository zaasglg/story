import asyncio

import pytest
from conftest import AppContext, login

from backend.finance import routes
from backend.identity.models import UserRole


async def test_concurrent_approval_cannot_preserve_approval_of_changed_amount(
    context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    boss_tokens = await login(context, UserRole.BOSS)
    boss = {"Authorization": f"Bearer {boss_tokens['access_token']}"}
    city = (await context.client.get("/api/v1/cities", headers=boss)).json()[0]["id"]
    payload = {
        "city_id": city,
        "expense_date": "2026-10-29",
        "amount": "100.00",
        "currency": "RUB",
        "category": "material",
    }
    created = await context.client.post("/api/v1/admin/expenses", headers=boss, json=payload)
    assert created.status_code == 201
    item_id = created.json()["id"]
    approval_task = None
    original = routes.validate_expense
    loaded, resume = asyncio.Event(), asyncio.Event()

    async def delayed_validation(session, data):
        loaded.set()
        await resume.wait()
        await original(session, data)

    monkeypatch.setattr(routes, "validate_expense", delayed_validation)
    editing = asyncio.create_task(
        context.client.put(
            f"/api/v1/admin/expenses/{item_id}",
            headers=boss,
            json={**payload, "amount": "999.00"},
        )
    )
    try:
        await asyncio.wait_for(loaded.wait(), 5)
        # SQLite ignores FOR UPDATE, deliberately exercising the unconditional
        # DRAFT write as well as the PostgreSQL row-lock protection.
        approval_task = None
        if context.database.engine.dialect.name == "postgresql":
            # PostgreSQL holds the row lock while validation is paused. Waiting
            # for approval before releasing the edit would deadlock the test.
            approval_task = asyncio.create_task(
                context.client.post(
                    f"/api/v1/boss/expenses/{item_id}/approve",
                    headers=boss,
                    json={"expected_revision": created.json()["revision"]},
                )
            )
            await asyncio.sleep(0.05)
            assert not approval_task.done()
        else:
            approved = await context.client.post(
                f"/api/v1/boss/expenses/{item_id}/approve",
                headers=boss,
                json={"expected_revision": created.json()["revision"]},
            )
            assert approved.status_code == 200
        resume.set()
        changed = await asyncio.wait_for(editing, 5)
        assert changed.status_code == 200
        if approval_task is not None:
            approved = await asyncio.wait_for(approval_task, 5)
            assert approved.status_code == 409
        stored = await context.client.get(f"/api/v1/admin/expenses/{item_id}", headers=boss)
        assert stored.json()["amount"] == "999.00"
        assert changed.json()["status"] == "DRAFT"
        # PostgreSQL rejects the stale reviewed revision after the edit. SQLite
        # permits approval first; the subsequent edit clears its status.
        assert stored.json()["status"] == "DRAFT"
    finally:
        resume.set()
        if not editing.done():
            editing.cancel()
        await asyncio.gather(editing, return_exceptions=True)
        if approval_task is not None:
            if not approval_task.done():
                approval_task.cancel()
            await asyncio.gather(approval_task, return_exceptions=True)
