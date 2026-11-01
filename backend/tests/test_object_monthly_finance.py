from datetime import date
from decimal import Decimal
from uuid import UUID

from conftest import login
from sqlalchemy import select

from backend.core.time import utc_now
from backend.finance.models import Expense, Revenue
from backend.identity.models import User, UserRole


async def prepare_reports(context):
    headers = {}
    for role in UserRole:
        tokens = await login(context, role)
        headers[role] = {"Authorization": f"Bearer {tokens['access_token']}"}
    cities = await context.client.get("/api/v1/cities", headers=headers[UserRole.ADMIN])
    city = cities.json()[0]["id"]
    other_city = await context.client.post(
        "/api/v1/admin/cities", headers=headers[UserRole.ADMIN], json={"name": "Курск"}
    )
    objects = []
    for number, city_id in ((1, city), (2, city), (3, other_city.json()["id"])):
        response = await context.client.post(
            "/api/v1/admin/objects",
            headers=headers[UserRole.ADMIN],
            json={"name": f"Нысан №{number}", "city_id": city_id, "address": "Строителей, 10"},
        )
        assert response.status_code == 201
        objects.append(response.json())
    async with context.database.session_factory() as session:
        actor = await session.scalar(select(User.id).where(User.role == UserRole.BOSS))
        now = utc_now()

        def add(
            model,
            index,
            amount,
            day,
            currency="RUB",
            status="APPROVED",
            archived=False,
            assigned=True,
            category=None,
        ):
            obj = objects[index]
            fields = {
                "revenue_date" if model is Revenue else "expense_date": date.fromisoformat(day)
            }
            session.add(
                model(
                    object_id=UUID(obj["id"]) if assigned else None,
                    city_id=UUID(obj["city_id"]),
                    amount=Decimal(amount),
                    currency=currency,
                    category=category or ("contract" if model is Revenue else "material"),
                    status=status,
                    created_by=actor,
                    updated_by=actor,
                    created_at=now,
                    updated_at=now,
                    archived_at=now if archived else None,
                    **fields,
                )
            )

        add(Revenue, 0, "200000", "2026-09-01")
        add(Revenue, 0, "150000", "2026-09-30")
        for category, amount in (
            ("salary", "120000"),
            ("material", "40000"),
            ("transport", "25000"),
            ("equipment", "15000"),
            ("accommodation", "10000"),
            ("food", "10000"),
        ):
            add(Expense, 0, amount, "2026-09-15", category=category)
        add(Revenue, 0, "100000", "2026-08-31")
        add(Expense, 0, "120000", "2026-08-01")
        add(Revenue, 0, "700000", "2026-09-05", currency="KZT")
        add(Expense, 0, "300000", "2026-09-06", currency="KZT")
        add(Revenue, 0, "777", "2026-10-01")
        add(Expense, 0, "500", "2026-07-01")
        add(Revenue, 0, "999999", "2026-06-15", status="DRAFT")
        add(Expense, 0, "888888", "2026-05-01", archived=True)
        add(Revenue, 1, "9000000", "2026-09-01")
        add(Revenue, 2, "8000000", "2026-09-01")
        add(Expense, 0, "7000000", "2026-09-01", assigned=False)
        await session.commit()
    return headers, objects, actor


async def test_monthly_report_is_object_scoped_and_separates_currencies(context):
    headers, objects, _ = await prepare_reports(context)
    path = f"/api/v1/boss/finance/object/{objects[0]['id']}"
    months = await context.client.get(f"{path}/months", headers=headers[UserRole.BOSS])
    assert months.json() == ["2026-10-01", "2026-09-01", "2026-08-01", "2026-07-01"]
    result = await context.client.get(
        path,
        headers=headers[UserRole.BOSS],
        params={"date_from": "2026-09-01", "date_to": "2026-09-30"},
    )
    assert result.status_code == 200
    assert result.json() == [
        {
            "currency": "KZT",
            "approved_revenue": "700000.00",
            "approved_expenses": "300000.00",
            "expense_breakdown": [{"category": "material", "amount": "300000.00"}],
            "net_profit": "400000.00",
        },
        {
            "currency": "RUB",
            "approved_revenue": "350000.00",
            "approved_expenses": "220000.00",
            "expense_breakdown": [
                {"category": "accommodation", "amount": "10000.00"},
                {"category": "equipment", "amount": "15000.00"},
                {"category": "food", "amount": "10000.00"},
                {"category": "material", "amount": "40000.00"},
                {"category": "salary", "amount": "120000.00"},
                {"category": "transport", "amount": "25000.00"},
            ],
            "net_profit": "130000.00",
        },
    ]
    other = await context.client.get(
        f"/api/v1/boss/finance/object/{objects[1]['id']}",
        headers=headers[UserRole.BOSS],
        params={"date_from": "2026-09-01", "date_to": "2026-09-30"},
    )
    assert other.json()[0]["net_profit"] == "9000000.00"
    empty = await context.client.get(
        path,
        headers=headers[UserRole.BOSS],
        params={"date_from": "2026-05-01", "date_to": "2026-05-31"},
    )
    assert empty.json() == []


async def test_months_include_expenses_only_and_loss_is_not_clamped(context):
    headers, objects, _ = await prepare_reports(context)
    path = f"/api/v1/boss/finance/object/{objects[0]['id']}"
    loss = await context.client.get(
        path,
        headers=headers[UserRole.BOSS],
        params={"date_from": "2026-08-01", "date_to": "2026-08-31"},
    )
    assert loss.json()[0]["net_profit"] == "-20000.00"
    expenses_only = await context.client.get(
        path,
        headers=headers[UserRole.BOSS],
        params={"date_from": "2026-07-01", "date_to": "2026-07-31"},
    )
    assert expenses_only.json()[0]["approved_revenue"] == "0.00"
    assert expenses_only.json()[0]["net_profit"] == "-500.00"


async def test_object_finance_roles_and_unknown_objects(context):
    headers, objects, _ = await prepare_reports(context)
    path = f"/api/v1/boss/finance/object/{objects[0]['id']}"
    for role in UserRole:
        for suffix in ("", "/months"):
            response = await context.client.get(f"{path}{suffix}", headers=headers[role])
            assert response.status_code == (200 if role == UserRole.BOSS else 403)
    assert (await context.client.get(f"{path}/months")).status_code == 401
    for suffix in ("", "/months"):
        missing = await context.client.get(
            f"/api/v1/boss/finance/object/{UUID(int=1)}{suffix}", headers=headers[UserRole.BOSS]
        )
        assert missing.status_code == 404
    invalid = await context.client.get(
        path,
        headers=headers[UserRole.BOSS],
        params={"date_from": "2026-10-01", "date_to": "2026-09-30"},
    )
    assert invalid.status_code == 422


async def test_operation_table_matches_month_totals_and_is_boss_only(context):
    headers, objects, _ = await prepare_reports(context)
    path = f"/api/v1/boss/finance/object/{objects[0]['id']}/operations"
    params = {"date_from": "2026-09-01", "date_to": "2026-09-30"}
    for role in UserRole:
        response = await context.client.get(path, headers=headers[role], params=params)
        assert response.status_code == (200 if role == UserRole.BOSS else 403)
    response = await context.client.get(path, headers=headers[UserRole.BOSS], params=params)
    rows = response.json()
    assert len(rows) == 10
    for currency, income, expense in (("RUB", 350000, 220000), ("KZT", 700000, 300000)):
        for kind, expected in (("income", income), ("expense", expense)):
            assert (
                sum(
                    Decimal(row["amount"])
                    for row in rows
                    if row["currency"] == currency and row["kind"] == kind
                )
                == expected
            )
    assert [row["date"] for row in rows] == sorted([row["date"] for row in rows], reverse=True)
    assert (await context.client.get(path, params=params)).status_code == 401
    empty = await context.client.get(
        path,
        headers=headers[UserRole.BOSS],
        params={"date_from": "2026-05-01", "date_to": "2026-05-31"},
    )
    assert empty.json() == []
    invalid = await context.client.get(
        path,
        headers=headers[UserRole.BOSS],
        params={"date_from": "2026-10-01", "date_to": "2026-09-01"},
    )
    assert invalid.status_code == 422
