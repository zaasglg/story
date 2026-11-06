from datetime import date
from decimal import Decimal
from uuid import UUID

from conftest import AppContext, login
from test_operations_api import auth, prepare_assignment

from backend.core.time import utc_now
from backend.identity.models import UserRole
from backend.operations.models import WorkDay, WorkDayStatus


async def test_object_progress_counts_confirmed_days_and_pages(context: AppContext):
    admin = await login(context, UserRole.ADMIN)
    worker, _, brigade, obj = await prepare_assignment(context, admin)
    async with context.database.session_factory() as session:
        from sqlalchemy import select

        from backend.identity.models import User

        actor = await session.scalar(select(User.id).where(User.role == UserRole.ADMIN))
        for day, state in [(1, WorkDayStatus.APPROVED), (2, WorkDayStatus.DRAFT)]:
            session.add(
                WorkDay(
                    worker_user_id=UUID(worker),
                    object_id=UUID(obj),
                    brigade_id=UUID(brigade),
                    work_date=date(2026, 10, day),
                    hours=Decimal("8"),
                    m3=Decimal("2.5"),
                    rate_per_m3=Decimal("100"),
                    daily_earnings=Decimal("250"),
                    currency="RUB",
                    status=state,
                    created_by=actor,
                    updated_by=actor,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            )
        await session.commit()
    path = f"/api/v1/objects/{obj}/work-progress"
    for role in [UserRole.ADMIN, UserRole.BOSS]:
        result = await context.client.get(
            path, headers=auth(await login(context, role)), params={"limit": 1}
        )
        assert result.status_code == 200, result.text
        data = result.json()
        assert data["total"] == 2
        assert Decimal(data["hours"]) == 8
        assert Decimal(data["m3"]) == Decimal("2.5")
        assert len(data["items"]) == 1
        assert data["items"][0]["work_date"] == "2026-10-02"
        assert "daily_earnings" not in data["items"][0]
    next_page = await context.client.get(
        path, headers=auth(admin), params={"limit": 1, "offset": 1}
    )
    assert next_page.json()["items"][0]["work_date"] == "2026-10-01"
    for role in [UserRole.WORKER, UserRole.MASTER]:
        denied = await context.client.get(path, headers=auth(await login(context, role)))
        assert denied.status_code == 403
    boss = auth(await login(context, UserRole.BOSS))
    planned = await context.client.patch(
        f"/api/v1/boss/objects/{obj}",
        headers=boss,
        json={"planned_hours": "10", "planned_volume_m3": "1"},
    )
    assert planned.status_code == 200, planned.text
    progress = (await context.client.get(path, headers=boss)).json()
    assert Decimal(progress["remaining_hours"]) == 2
    assert Decimal(progress["remaining_m3"]) == 0
    assert Decimal(progress["planned_volume_m3"]) == 1
    completed = await context.client.patch(
        f"/api/v1/boss/objects/{obj}", headers=boss, json={"status": "COMPLETED"}
    )
    assert completed.status_code == 200, completed.text
    report = (await context.client.get(path, headers=boss)).json()
    assert report["status"] == "COMPLETED"
    assert report["total"] == 2
