from conftest import login

from backend.identity.models import UserRole


async def test_master_cannot_read_foreign_rating_or_discover_contact(context):
    admin = await login(context, UserRole.ADMIN)
    master = await login(context, UserRole.MASTER)
    worker = await login(context, UserRole.WORKER)
    ah = {"Authorization": f"Bearer {admin['access_token']}"}
    mh = {"Authorization": f"Bearer {master['access_token']}"}
    other = await context.client.post(
        "/api/v1/admin/workers",
        headers=ah,
        json={
            "phone": "+77075550991",
            "full_name": "Synthetic outsider",
            "employee_id": "D02-OUTSIDE",
        },
    )
    oid = other.json()["id"]
    for path in [f"/workers/{oid}/rating/public", f"/chats/direct/{oid}"]:
        response = await (
            context.client.get("/api/v1" + path, headers=mh)
            if "rating" in path
            else context.client.post("/api/v1" + path, headers=mh)
        )
        assert response.status_code == 404
    contacts = (await context.client.get("/api/v1/chats/contacts", headers=mh)).json()
    assert oid not in {row["id"] for row in contacts}
    assert worker["user"]["id"] in {row["id"] for row in contacts}
    assert {"ADMIN", "BOSS"}.issubset({row["role"] for row in contacts})
    rating = await context.client.get(
        f"/api/v1/workers/{worker['user']['id']}/rating/public", headers=mh
    )
    assert rating.status_code == 200
    # A foreign worker cannot bypass discovery controls by creating the thread first.
    wh = {"Authorization": f"Bearer {worker['access_token']}"}
    thread = await context.client.post(f"/api/v1/chats/direct/{master['user']['id']}", headers=wh)
    from uuid import UUID

    from sqlalchemy import select

    from backend.identity.models import EmployeeProfile

    async with context.database.session_factory() as session:
        profile = await session.scalar(
            select(EmployeeProfile).where(EmployeeProfile.user_id == UUID(worker["user"]["id"]))
        )
        profile.master_user_id = None
        await session.commit()
    assert thread.json()["id"] not in {
        r["id"] for r in (await context.client.get("/api/v1/chats", headers=mh)).json()
    }
    assert (
        await context.client.get(f"/api/v1/chats/{thread.json()['id']}/messages", headers=mh)
    ).status_code == 404
