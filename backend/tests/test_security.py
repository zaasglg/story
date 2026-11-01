import pytest
from conftest import AppContext, login

from backend.identity.models import UserRole


@pytest.mark.parametrize("role", list(UserRole))
async def test_password_change_preserves_current_and_revokes_other_sessions(
    context: AppContext, role
):
    first = await login(context, role)
    second = await login(context, role)
    headers = {"Authorization": f"Bearer {first['access_token']}"}
    wrong = await context.client.post(
        "/api/v1/me/password",
        headers=headers,
        json={
            "current_password": "wrong",
            "new_password": "New-password-456",
        },
    )
    assert wrong.status_code == 400
    assert "wrong" not in wrong.text
    response = await context.client.post(
        "/api/v1/me/password",
        headers=headers,
        json={
            "current_password": "Test-password-123",
            "new_password": "New-password-456",
        },
    )
    assert response.status_code == 200
    assert (await context.client.get("/api/v1/me", headers=headers)).status_code == 200
    other = {"Authorization": f"Bearer {second['access_token']}"}
    assert (await context.client.get("/api/v1/me", headers=other)).status_code == 401
    assert (
        await context.client.post(
            "/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}
        )
    ).status_code == 401
    for password, status in [("Test-password-123", 401), ("New-password-456", 200)]:
        attempt = await context.client.post(
            "/api/v1/auth/login",
            json={
                "phone": context.phones[role],
                "password": password,
            },
        )
        assert attempt.status_code == status


async def test_bulk_revoke_does_not_touch_other_accounts(context: AppContext):
    worker = await login(context, UserRole.WORKER)
    other_worker = await login(context, UserRole.WORKER)
    boss = await login(context, UserRole.BOSS)
    headers = {"Authorization": f"Bearer {worker['access_token']}"}
    assert (
        await context.client.delete("/api/v1/me/other-sessions", headers=headers)
    ).status_code == 200
    rows = (await context.client.get("/api/v1/me/sessions", headers=headers)).json()
    assert len(rows) == 1 and rows[0]["current"]
    for tokens, status in [(worker, 200), (other_worker, 401), (boss, 200)]:
        assert (
            await context.client.get(
                "/api/v1/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
            )
        ).status_code == status


async def test_password_policy_and_rate_limit(context: AppContext):
    tokens = await login(context, UserRole.WORKER)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    path = "/api/v1/me/password"
    assert (
        await context.client.post(
            path, json={"current_password": "x", "new_password": "short"}, headers=headers
        )
    ).status_code == 422
    assert (
        await context.client.post(
            path,
            json={
                "current_password": "Test-password-123",
                "new_password": "Test-password-123",
                "role": "BOSS",
            },
            headers=headers,
        )
    ).status_code == 422
    for _ in range(5):
        assert (
            await context.client.post(
                path,
                json={"current_password": "bad", "new_password": "New-password-456"},
                headers=headers,
            )
        ).status_code == 400
    assert (
        await context.client.post(
            path,
            json={"current_password": "Test-password-123", "new_password": "New-password-456"},
            headers=headers,
        )
    ).status_code == 429


async def test_profile_history_is_scoped_and_decision_note_visible(context: AppContext):
    worker = await login(context, UserRole.WORKER)
    admin = await login(context, UserRole.ADMIN)
    headers = {"Authorization": f"Bearer {worker['access_token']}"}
    admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
    path = "/api/v1/workers/me/change-request"
    assert (
        await context.client.post(path, headers=headers, json={"full_name": "New", "role": "BOSS"})
    ).status_code == 422
    response = await context.client.post(
        path, headers=headers, json={"full_name": "New Name", "reason": "Correction"}
    )
    assert response.status_code == 201
    own = (await context.client.get("/api/v1/workers/me/change-requests", headers=headers)).json()
    assert own[0]["id"] == response.json()["id"]
    assert (
        await context.client.get("/api/v1/workers/me/change-requests", headers=admin_headers)
    ).json() == []
    result = await context.client.post(
        f"/api/v1/admin/change-requests/{own[0]['id']}/decision",
        headers=admin_headers,
        json={"approve": False, "review_note": "Please verify spelling"},
    )
    assert result.status_code == 200
    own = (await context.client.get("/api/v1/workers/me/change-requests", headers=headers)).json()
    assert own[0]["status"] == "REJECTED"
    assert own[0]["review_note"] == "Please verify spelling"
