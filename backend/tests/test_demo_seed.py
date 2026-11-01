from datetime import date

from conftest import AppContext, login
from sqlalchemy import func, select

from backend.demo_seed import populate_demo
from backend.identity.models import Base, UserRole
from backend.management.catalog import RESOURCES


async def test_demo_seed_is_linked_and_repeatable(context: AppContext) -> None:
    from sqlalchemy import update

    from backend.identity.models import User

    async with context.database.session_factory() as session:
        for index, role in enumerate(
            [UserRole.BOSS, UserRole.ADMIN, UserRole.MASTER, UserRole.WORKER], start=1
        ):
            await session.execute(
                update(User)
                .where(User.phone == context.phones[role])
                .values(phone=f"+7700000000{index}")
            )
            context.phones[role] = f"+7700000000{index}"
        await session.commit()
        await populate_demo(session, date(2026, 10, 11))
        before = {
            name: await session.scalar(select(func.count()).select_from(table))
            for name, table in Base.metadata.tables.items()
            if name != "audit_logs"
        }
        await populate_demo(session, date(2026, 10, 11))
        after = {
            name: await session.scalar(select(func.count()).select_from(table))
            for name, table in Base.metadata.tables.items()
            if name != "audit_logs"
        }
        assert before == after
        assert after["worker_assignments"] == 9
        assert after["work_schedules"] == 135
        assert after["work_days"] == 62
        assert after["attendance_records"] == 63
        assert after["ledger_entries"] == 18
        assert after["equipment_issues"] == 4
        assert after["notifications"] == 27

    for role, paths in {
        UserRole.ADMIN: ["/admin/workers", "/admin/financial-requests"],
        UserRole.BOSS: ["/boss/expenses", "/boss/finance", "/boss/pending-approvals"],
        UserRole.MASTER: ["/master/team", "/master/schedules"],
        UserRole.WORKER: [
            "/workers/me/work-days",
            "/workers/me/equipment",
            "/workers/me/finance?period=2026-10-01",
            "/me/notifications",
        ],
    }.items():
        tokens = await login(context, role)
        for path in paths:
            response = await context.client.get(
                "/api/v1" + path,
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            assert response.status_code == 200, response.text
            assert response.json()

    admin_tokens = await login(context, UserRole.ADMIN)
    admin_headers = {"Authorization": f"Bearer {admin_tokens['access_token']}"}
    for name in RESOURCES:
        path = f"/api/v1/admin/catalog/{name}"
        response = await context.client.get(path, headers=admin_headers, params={"limit": 2})
        if name == "expenses":
            assert response.status_code == 403
            continue
        assert response.status_code == 200, (name, response.text)
        page = response.json()
        assert len(page["items"]) <= 2
        for item in page["items"]:
            detail = await context.client.get(f"{path}/{item['id']}", headers=admin_headers)
            assert detail.status_code == 200, (name, detail.text)
            assert detail.json()["item"] == item
        for sort in page["sorts"]:
            sorted_page = await context.client.get(
                path, headers=admin_headers, params={"sort": sort, "q": "a", "limit": 1}
            )
            assert sorted_page.status_code == 200, (name, sorted_page.text)
