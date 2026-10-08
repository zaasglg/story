from uuid import UUID

from conftest import AppContext, login
from sqlalchemy import select

from backend.identity.models import AuditLog, User, UserRole
from backend.workforce.models import EmployeePrivateData


def auth(tokens: dict[str, object]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_admin_workflow_and_role_safe_projections(context: AppContext) -> None:
    admin_tokens = await login(context, UserRole.ADMIN)
    master_tokens = await login(context, UserRole.MASTER)
    worker_tokens = await login(context, UserRole.WORKER)
    boss_tokens = await login(context, UserRole.BOSS)

    cities_response = await context.client.get("/api/v1/cities", headers=auth(admin_tokens))
    assert cities_response.status_code == 200
    city_id = cities_response.json()[0]["id"]

    async with context.database.session_factory() as session:
        master_id = await session.scalar(select(User.id).where(User.role == UserRole.MASTER))
    assert master_id is not None

    brigade_response = await context.client.post(
        "/api/v1/admin/brigades",
        headers=auth(admin_tokens),
        json={"name": "Brigade 17", "city_id": city_id, "master_user_id": str(master_id)},
    )
    assert brigade_response.status_code == 201, brigade_response.text
    brigade_id = brigade_response.json()["id"]

    object_response = await context.client.post(
        "/api/v1/admin/objects",
        headers=auth(admin_tokens),
        json={
            "name": "Object Alpha",
            "city_id": city_id,
            "address": "Industrialnaya 17",
            "status": "ACTIVE",
        },
    )
    assert object_response.status_code == 201, object_response.text
    object_id = object_response.json()["id"]

    created = await context.client.post(
        "/api/v1/admin/workers",
        headers=auth(admin_tokens),
        json={
            "phone": "+77075550101",
            "full_name": "Private Worker",
            "employee_id": "EMP-PRIVATE",
            "city_id": city_id,
            "passport_number": "PASS-SECRET-1917",
        },
    )
    assert created.status_code == 201, created.text
    worker_id = created.json()["id"]
    assert created.json()["passport_number"] == "PASS-SECRET-1917"
    async with context.database.session_factory() as session:
        protected = await session.scalar(
            select(EmployeePrivateData).where(EmployeePrivateData.user_id == UUID(worker_id))
        )
        audit_rows = (await session.scalars(select(AuditLog))).all()
    assert protected is not None
    assert protected.passport_encrypted != "PASS-SECRET-1917"
    assert "PASS-SECRET-1917" not in repr(
        [(row.old_value, row.new_value, row.metadata_json) for row in audit_rows]
    )

    public = await context.client.get("/api/v1/workers/public", headers=auth(worker_tokens))
    assert public.status_code == 200
    public_worker = next(item for item in public.json() if item["id"] == worker_id)
    assert set(public_worker) == {
        "id",
        "full_name",
        "role",
        "photo_url",
        "city",
        "brigade_name",
    }
    assert public_worker["role"] == "WORKER"
    assert "PASS-SECRET-1917" not in public.text
    assert "+77075550101" not in public.text
    assert "EMP-PRIVATE" not in public.text

    before_assignment = await context.client.get(
        f"/api/v1/master/team/{worker_id}", headers=auth(master_tokens)
    )
    assert before_assignment.status_code == 404

    assigned = await context.client.post(
        f"/api/v1/admin/workers/{worker_id}/assignment",
        headers=auth(admin_tokens),
        json={"brigade_id": brigade_id, "object_id": object_id},
    )
    assert assigned.status_code == 200, assigned.text

    team_detail = await context.client.get(
        f"/api/v1/master/team/{worker_id}", headers=auth(master_tokens)
    )
    assert team_detail.status_code == 200
    assert team_detail.json()["employee_id"] == "EMP-PRIVATE"
    assert "passport_number" not in team_detail.json()
    assert "phone" not in team_detail.json()
    assert "PASS-SECRET-1917" not in team_detail.text

    boss_create = await context.client.post(
        "/api/v1/admin/workers",
        headers=auth(boss_tokens),
        json={
            "phone": "+77075550102",
            "full_name": "Forbidden Worker",
            "employee_id": "EMP-FORBIDDEN",
        },
    )
    assert boss_create.status_code == 403


async def test_worker_change_request_requires_admin_decision(context: AppContext) -> None:
    worker_tokens = await login(context, UserRole.WORKER)
    admin_tokens = await login(context, UserRole.ADMIN)

    created = await context.client.post(
        "/api/v1/workers/me/change-request",
        headers=auth(worker_tokens),
        json={"full_name": "Updated Worker", "reason": "Legal name corrected"},
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]

    own_before = await context.client.get("/api/v1/workers/me", headers=auth(worker_tokens))
    assert own_before.json()["full_name"] != "Updated Worker"

    decision = await context.client.post(
        f"/api/v1/admin/change-requests/{request_id}/decision",
        headers=auth(admin_tokens),
        json={"approve": True},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "APPROVED"

    own_after = await context.client.get("/api/v1/workers/me", headers=auth(worker_tokens))
    assert own_after.json()["full_name"] == "Updated Worker"
