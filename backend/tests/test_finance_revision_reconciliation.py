import asyncio
from decimal import Decimal

import pytest
from conftest import login
from sqlalchemy import select
from test_operations_api import auth

from backend.finance.models import Expense
from backend.finance.reconciliation import analyze
from backend.identity.models import UserRole


async def expense(context):
    boss = auth(await login(context, UserRole.BOSS))
    city = (await context.client.get("/api/v1/cities", headers=boss)).json()[0]["id"]
    payload = {
        "city_id": city,
        "expense_date": "2026-10-29",
        "amount": "123.45",
        "currency": "RUB",
        "category": "material",
        "comment": "Synthetic",
    }
    result = await context.client.post("/api/v1/boss/expenses", headers=boss, json=payload)
    assert result.status_code == 201, result.text
    return boss, payload, result.json()


@pytest.mark.parametrize(
    "changed",
    [
        {"amount": "200.00"},
        {"category": "transport"},
        {"expense_date": "2026-10-30"},
        {"comment": "Changed"},
        {"currency": "KZT"},
        {"recipient": "Synthetic changed recipient"},
    ],
)
async def test_stale_expense_rejected_for_significant_field_changes(context, changed):
    boss, payload, created = await expense(context)
    path = "/api/v1/boss/expenses/" + created["id"]
    updated = await context.client.put(path, headers=boss, json={**payload, **changed})
    assert updated.status_code == 200, updated.text
    stale = await context.client.post(
        path + "/approve", headers=boss, json={"expected_revision": created["revision"]}
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "stale_expense"
    current = await context.client.get(path, headers=boss)
    assert current.json()["status"] == "DRAFT"
    assert current.json()["revision"] > created["revision"]
    approved = await context.client.post(
        path + "/approve",
        headers=boss,
        json={
            "expected_revision": current.json()["revision"],
            "reason": "Synthetic approval reason",
        },
    )
    assert approved.status_code == 200, approved.text
    history = (await context.client.get("/api/v1/boss/audit?limit=100", headers=boss)).json()
    row = next(r for r in history if r["action"] == "EXPENSE_APPROVED")
    assert row["actor_role"] == "BOSS" and row["reason"] == "Synthetic approval reason"
    assert any((r.get("old_value") or {}).get("revision") == 0 for r in history)


async def test_duplicate_approvals_are_idempotent_and_missing_revision_is_rejected(context):
    boss, payload, created = await expense(context)
    path = "/api/v1/boss/expenses/" + created["id"] + "/approve"
    assert (await context.client.post(path, headers=boss)).status_code == 422
    if context.database.engine.dialect.name == "postgresql":
        results = await asyncio.gather(
            *(
                context.client.post(
                    path, headers=boss, json={"expected_revision": created["revision"]}
                )
                for _ in range(2)
            )
        )
    else:
        results = [
            await context.client.post(
                path, headers=boss, json={"expected_revision": created["revision"]}
            )
            for _ in range(2)
        ]
    assert [r.status_code for r in results] == [200, 200]
    history = (await context.client.get("/api/v1/boss/audit?limit=100", headers=boss)).json()
    assert sum(r["action"] == "EXPENSE_APPROVED" for r in history) == 1
    current = (
        await context.client.get("/api/v1/boss/expenses/" + created["id"], headers=boss)
    ).json()
    assert current["revision"] == 1


async def test_reconciliation_is_read_only_and_duplicate_findings_are_candidates(context):
    boss, payload, created = await expense(context)
    again = await context.client.post("/api/v1/boss/expenses", headers=boss, json=payload)
    assert again.status_code == 201
    async with context.database.session_factory() as session:
        before = [
            (row.id, row.amount, row.status)
            for row in (await session.scalars(select(Expense))).all()
        ]
    async with context.database.engine.connect() as connection:
        report = await analyze(connection)
    assert report["finding_counts"]["NO_OBJECT"] == 2
    assert report["finding_counts"]["POSSIBLE_DUPLICATE"] == 2
    assert Decimal(report["active_expense_totals"]["RUB"]) == Decimal("246.90")
    assert all(not row["apply_allowed"] for row in report["correction_plan"])
    async with context.database.session_factory() as session:
        after = [
            (row.id, row.amount, row.status)
            for row in (await session.scalars(select(Expense))).all()
        ]
        assert before == after
