from conftest import AppContext, login
from sqlalchemy import select

from backend.audit.service import add_audit_event
from backend.identity.models import User, UserRole


async def test_recent_actions_explain_actor_subject_and_changes(context: AppContext):
    headers = {"Authorization": f"Bearer {(await login(context, UserRole.ADMIN))['access_token']}"}
    async with context.database.session_factory() as session:
        users = {user.role: user for user in (await session.scalars(select(User))).all()}
        for actor_role, action in [
            (UserRole.ADMIN, "WORKER_UPDATED"),
            (UserRole.ADMIN, "AUTH_TOKEN_REFRESHED"),
            (UserRole.BOSS, "WORKER_BLOCKED"),
        ]:
            add_audit_event(
                session,
                actor_user_id=users[actor_role].id,
                action=action,
                entity_type="worker",
                entity_id=users[UserRole.WORKER].id,
                old_value={"status": "ACTIVE", "password": "secret"},
                new_value={"status": "BLOCKED", "password": "secret"},
            )
        await session.commit()
    result = await context.client.get(
        "/api/v1/admin/audit?limit=5&hide_session_refresh=true", headers=headers
    )
    assert result.status_code == 200
    rows = result.json()
    event = next(row for row in rows if row["action"] == "WORKER_UPDATED")
    assert event["actor_name"] == "Admin User"
    assert event["subject_name"] == "Worker User"
    assert event["changes"] == [{"field": "status", "before": "ACTIVE", "after": "BLOCKED"}]
    assert all(row["action"] not in ("AUTH_TOKEN_REFRESHED", "WORKER_BLOCKED") for row in rows)
    assert "secret" not in result.text
    full = await context.client.get("/api/v1/admin/audit", headers=headers)
    assert any(row["action"] == "AUTH_TOKEN_REFRESHED" for row in full.json())
