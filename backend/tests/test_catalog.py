from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from conftest import AppContext, login
from sqlalchemy import select

from backend.authorization.permissions import has_permission
from backend.core.time import utc_now
from backend.identity.models import AccountStatus, EmployeeProfile, User, UserRole
from backend.management.catalog import RESOURCES
from backend.workforce.models import City


async def headers(context, role=UserRole.ADMIN):
    tokens = await login(context, role)
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.mark.parametrize("role", list(UserRole))
async def test_catalog_matches_existing_permission_matrix(context: AppContext, role):
    auth = await headers(context, role)
    for name, descriptor in RESOURCES.items():
        response = await context.client.get(f"/api/v1/admin/catalog/{name}", headers=auth)
        assert response.status_code == (
            200 if has_permission(role, descriptor.permission) else 403
        ), (name, response.text)
        if response.status_code == 200:
            assert response.json()["limit"] == 25
            assert isinstance(response.json()["items"], list)


async def test_catalog_global_search_pagination_and_safe_projection(context: AppContext):
    auth = await headers(context)
    async with context.database.session_factory() as session:
        city = await session.scalar(select(City))
        now = utc_now()
        for i in range(31):
            user = User(
                id=uuid4(),
                phone=f"+7700999{i:04}",
                role=UserRole.WORKER,
                status=AccountStatus.ACTIVE,
                created_at=now - timedelta(days=1),
                updated_at=now,
            )
            session.add(user)
            await session.flush()
            session.add(
                EmployeeProfile(
                    user_id=user.id,
                    full_name=f"Search Employee {i:02}",
                    employee_id=f"SEARCH-{i:02}",
                    city_id=city.id,
                    created_at=now,
                    updated_at=now,
                )
            )
        await session.commit()
    path = "/api/v1/admin/catalog/workers"
    params = {"q": "Search Employee", "sort": "name", "direction": "asc", "limit": 25}
    first = (await context.client.get(path, headers=auth, params=params)).json()
    second = (await context.client.get(path, headers=auth, params={**params, "offset": 25})).json()
    assert first["total"] == second["total"] == 31
    assert len(first["items"]) == 25 and len(second["items"]) == 6
    assert not {row["id"] for row in first["items"]} & {row["id"] for row in second["items"]}
    assert second["items"][-1]["full_name"] == "Search Employee 30"
    hit = (await context.client.get(path, headers=auth, params={"q": "SEARCH-30"})).json()
    assert hit["total"] == 1
    row = hit["items"][0]
    assert "password_hash" not in row and "auth_version" not in row
    detail = await context.client.get(f"{path}/{row['id']}", headers=auth)
    assert detail.json()["item"] == row
    assert detail.json()["labels"][str(city.id)] == city.name
    literal = await context.client.get(path, headers=auth, params={"q": "%"})
    assert literal.json()["total"] == 0


async def test_catalog_filters_archives_dates_and_invalid_parameters(context: AppContext):
    auth = await headers(context)
    path = "/api/v1/admin/catalog/workers"
    worker = (await context.client.get(path, headers=auth)).json()["items"][0]
    filtered = await context.client.get(path, headers=auth, params={"status": "BLOCKED"})
    assert filtered.json()["total"] == 0
    filtered = await context.client.get(path, headers=auth, params={"city_id": str(uuid4())})
    assert filtered.json()["total"] == 0
    for query in (
        {"limit": 101},
        {"offset": -1},
        {"sort": "password_hash"},
        {"status": "unknown"},
        {"date_from": "2026-10-30", "date_to": "2026-10-01"},
    ):
        assert (await context.client.get(path, headers=auth, params=query)).status_code == 422
    assert (
        await context.client.get("/api/v1/admin/catalog/users", headers=auth)
    ).status_code == 404
    async with context.database.session_factory() as session:
        user = await session.get(User, UUID(worker["id"]))
        user.archived_at = utc_now()
        await session.commit()
    assert (await context.client.get(f"{path}/{worker['id']}", headers=auth)).status_code == 404
