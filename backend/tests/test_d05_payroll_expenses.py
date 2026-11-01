from decimal import Decimal

from conftest import login
from sqlalchemy import select

from backend.finance.models import Expense, LedgerEntry
from backend.identity.models import UserRole


async def test_approved_payroll_costs_corrections_and_payments_are_not_duplicated(context):
    admin = await login(context, UserRole.ADMIN)
    boss = await login(context, UserRole.BOSS)
    worker = await login(context, UserRole.WORKER)
    ah = {"Authorization": f"Bearer {admin['access_token']}"}
    bh = {"Authorization": f"Bearer {boss['access_token']}"}

    async def post(kind, amount):
        response = await context.client.post(
            "/api/v1/admin/financial-requests",
            headers={**ah, "Idempotency-Key": "D05-" + kind},
            json={
                "worker_user_id": worker["user"]["id"],
                "type": kind,
                "amount": amount,
                "currency": "RUB",
                "period": "2026-10-01",
                "reason": "Synthetic cost",
            },
        )
        assert response.status_code == 201, response.text
        path = f"/api/v1/boss/approvals/{response.json()['id']}/approve"
        approved = await context.client.post(path, headers=bh)
        assert approved.status_code == 200, approved.text
        assert (await context.client.post(path, headers=bh)).status_code == 200

    await post("BONUS", "1000")
    await post("PENALTY", "100")
    await post("ADVANCE", "200")
    await post("PAYMENT", "300")
    report = (await context.client.get("/api/v1/boss/profit", headers=bh)).json()
    assert Decimal(report[0]["approved_expenses"]) == 900
    async with context.database.session_factory() as session:
        costs = (await session.scalars(select(Expense))).all()
        assert len(costs) == 2
        bonus = await session.scalar(select(LedgerEntry).where(LedgerEntry.type == "BONUS"))
        eid = str(bonus.id)
        source_id = str(next(c.id for c in costs if c.amount > 0))
    corrected = await context.client.post(
        f"/api/v1/boss/ledger/{eid}/corrections",
        headers={**bh, "Idempotency-Key": "D05-correct"},
        json={"expected_amount": "1000", "new_amount": "1200", "reason": "Synthetic correction"},
    )
    assert corrected.status_code == 201, corrected.text
    assert (
        Decimal(
            (await context.client.get("/api/v1/boss/profit", headers=bh)).json()[0][
                "approved_expenses"
            ]
        )
        == 1100
    )
    assert (
        await context.client.delete(f"/api/v1/boss/expenses/{source_id}", headers=bh)
    ).status_code == 409
    async with context.database.session_factory() as session:
        assert len((await session.scalars(select(Expense))).all()) == 2


async def test_salary_allocations_rounding_object_reports_and_legacy_are_preserved(context):
    from datetime import date
    from uuid import UUID

    from test_operations_api import auth, prepare_assignment

    from backend.core.time import utc_now
    from backend.operations.models import WorkDay, WorkDayStatus
    from backend.workforce.models import WorkObject

    admin, boss = await login(context, UserRole.ADMIN), await login(context, UserRole.BOSS)
    wid, _, bid, oid = await prepare_assignment(context, admin)
    async with context.database.session_factory() as session:
        obj = await session.get(WorkObject, UUID(oid))
        second = WorkObject(
            name="Synthetic second",
            city_id=obj.city_id,
            address="Synthetic",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(second)
        await session.flush()
        other = second.id
        for index, (object_id, amount) in enumerate(
            [(obj.id, Decimal(200)), (other, Decimal(100))], start=1
        ):
            session.add(
                WorkDay(
                    worker_user_id=UUID(wid),
                    object_id=object_id,
                    brigade_id=UUID(bid),
                    work_date=date(2026, 10, index),
                    hours=8,
                    m3=1,
                    rate_per_m3=amount,
                    daily_earnings=amount,
                    currency="RUB",
                    status=WorkDayStatus.APPROVED,
                    created_by=UUID(admin["user"]["id"]),
                    updated_by=UUID(admin["user"]["id"]),
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            )
        await session.commit()
    created = await context.client.post(
        "/api/v1/admin/financial-requests",
        headers={**auth(admin), "Idempotency-Key": "D05-salary"},
        json={
            "worker_user_id": wid,
            "type": "SALARY",
            "amount": "300",
            "currency": "RUB",
            "period": "2026-10-01",
            "reason": "Synthetic salary",
        },
    )
    assert created.status_code == 201
    approved = await context.client.post(
        f"/api/v1/boss/approvals/{created.json()['id']}/approve", headers=auth(boss)
    )
    assert approved.status_code == 200, approved.text
    async with context.database.session_factory() as session:
        entry = await session.scalar(select(LedgerEntry).where(LedgerEntry.type == "SALARY"))
        eid = str(entry.id)
    response = await context.client.post(
        f"/api/v1/boss/ledger/{eid}/corrections",
        headers={**auth(boss), "Idempotency-Key": "D05-round"},
        json={"expected_amount": "300", "new_amount": "301", "reason": "Synthetic adjustment"},
    )
    assert response.status_code == 201
    amounts = []
    for object_id in [oid, str(other)]:
        report = (
            await context.client.get(f"/api/v1/boss/finance/object/{object_id}", headers=auth(boss))
        ).json()
        amounts.append(Decimal(report[0]["approved_expenses"]))
    assert amounts == [Decimal("200.67"), Decimal("100.33")]
    assert sum(amounts) == 301
    # Old ledger entries must not silently generate expenses on migration/correction.
    # A separate period avoids the salary uniqueness constraint on the same worker.
    async with context.database.session_factory() as session:
        entry = await session.get(LedgerEntry, UUID(eid))
        entry.cost_integrated = False
        await session.commit()
    response = await context.client.post(
        f"/api/v1/boss/ledger/{eid}/corrections",
        headers={**auth(boss), "Idempotency-Key": "D05-legacy"},
        json={
            "expected_amount": "301",
            "new_amount": "302",
            "reason": "Synthetic legacy correction",
        },
    )
    assert response.status_code == 201
    assert (
        Decimal(
            (await context.client.get("/api/v1/boss/profit", headers=auth(boss))).json()[0][
                "approved_expenses"
            ]
        )
        == 301
    )


async def test_explicit_legacy_link_does_not_duplicate_or_rewrite_manual_costs(context):
    from datetime import date
    from uuid import UUID

    from test_salary_change_approval import setup_salary

    from backend.core.time import utc_now
    from backend.identity.models import EmployeeProfile

    headers, eid = await setup_salary(context)
    bh = headers[UserRole.BOSS]
    async with context.database.session_factory() as session:
        entry = await session.get(LedgerEntry, UUID(eid))
        profile = await session.scalar(
            select(EmployeeProfile).where(EmployeeProfile.user_id == entry.worker_user_id)
        )
        cost = Expense(
            city_id=profile.city_id,
            expense_date=date(2026, 10, 1),
            amount=entry.amount,
            currency="RUB",
            category="salary",
            status="APPROVED",
            comment="Synthetic legacy cost",
            created_by=entry.worker_user_id,
            updated_by=entry.worker_user_id,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(cost)
        await session.commit()
        cid = str(cost.id)
    before = (await context.client.get("/api/v1/boss/profit", headers=bh)).json()
    path = f"/api/v1/boss/ledger/{eid}/expense-links"
    payload = {"expense_ids": [cid], "expected_amount": "120000", "expected_revision": 0}
    assert (
        await context.client.post(path, headers=headers[UserRole.ADMIN], json=payload)
    ).status_code == 403
    assert (
        await context.client.post(path, headers=bh, json={**payload, "expected_amount": "1"})
    ).status_code == 409
    for _ in range(2):
        response = await context.client.post(path, headers=bh, json=payload)
        assert response.status_code == 200, response.text
    assert (await context.client.get("/api/v1/boss/profit", headers=bh)).json() == before
    async with context.database.session_factory() as session:
        rows = (await session.scalars(select(Expense))).all()
        assert len(rows) == 1 and rows[0].amount == Decimal("120000")
        assert rows[0].comment == "Synthetic legacy cost"
