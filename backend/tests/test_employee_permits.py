import pytest
from conftest import AppContext, login
from sqlalchemy import select

from backend.identity.models import User, UserRole


async def employee_id(context, role):
    async with context.database.session_factory() as session:
        return str(await session.scalar(select(User.id).where(User.role == role)))


@pytest.mark.parametrize("target_role", [UserRole.WORKER, UserRole.MASTER])
async def test_grant_revoke_and_own_read(context: AppContext, target_role):
    worker_id = await employee_id(context, target_role)
    admin = {"Authorization": f"Bearer {(await login(context, UserRole.ADMIN))['access_token']}"}
    own = {"Authorization": f"Bearer {(await login(context, target_role))['access_token']}"}
    url = f"/api/v1/admin/workers/{worker_id}/permits"
    listing = await context.client.get(url, headers=admin)
    assert listing.status_code == 200
    assert len(listing.json()) == 5
    assert all(not item["granted"] for item in listing.json())
    for code in ["GROUND", "HEIGHT", "ENCLOSED", "MACHINERY", "TOOLS"]:
        result = await context.client.put(f"{url}/{code}", headers=admin, json={"granted": True})
        assert result.status_code == 200, result.text
    own_list = await context.client.get("/api/v1/workers/me/permits", headers=own)
    assert own_list.status_code == 200
    assert all(item["granted"] for item in own_list.json())
    revoked = await context.client.put(f"{url}/HEIGHT", headers=admin, json={"granted": False})
    assert revoked.status_code == 200
    assert not next(item for item in revoked.json() if item["code"] == "HEIGHT")["granted"]
    invalid = await context.client.put(f"{url}/UNKNOWN", headers=admin, json={"granted": True})
    assert invalid.status_code == 422


@pytest.mark.parametrize("role", list(UserRole))
async def test_permit_four_role_permissions(context: AppContext, role):
    worker_id = await employee_id(context, UserRole.WORKER)
    headers = {"Authorization": f"Bearer {(await login(context, role))['access_token']}"}
    url = f"/api/v1/admin/workers/{worker_id}/permits"
    read = await context.client.get(url, headers=headers)
    assert read.status_code == (200 if role in (UserRole.ADMIN, UserRole.BOSS) else 403)
    write = await context.client.put(f"{url}/GROUND", headers=headers, json={"granted": True})
    assert write.status_code == (200 if role == UserRole.ADMIN else 403)
