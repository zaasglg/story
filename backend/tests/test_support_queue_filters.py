from conftest import login

from backend.identity.models import UserRole


async def test_support_search_status_and_permissions(context):
    headers = {}
    for role in UserRole:
        tokens = await login(context, role)
        headers[role] = {"Authorization": f"Bearer {tokens['access_token']}"}
    ids = []
    for subject, body in (
        ("Камера не работает", "Нужна помощь"),
        ("Вопрос по табелю", "Готово на 100%"),
        ("Другая ошибка", "Проверка"),
    ):
        item = await context.client.post(
            "/api/v1/me/support-tickets",
            headers=headers[UserRole.WORKER],
            json={"subject": subject, "body": body},
        )
        assert item.status_code == 201
        ids.append(item.json()["id"])
    closed = await context.client.post(
        f"/api/v1/admin/support-tickets/{ids[1]}/resolve", headers=headers[UserRole.ADMIN]
    )
    assert closed.status_code == 200
    endpoint = "/api/v1/admin/support-tickets"
    for role in (UserRole.ADMIN, UserRole.BOSS):
        result = await context.client.get(
            endpoint, headers=headers[role], params={"status": "OPEN", "q": "Камера"}
        )
        assert [row["id"] for row in result.json()] == [ids[0]]
        assert result.json()[0]["requester_name"] == "Worker User"
        result = await context.client.get(
            endpoint, headers=headers[role], params={"status": "RESOLVED", "q": "100%"}
        )
        assert [row["id"] for row in result.json()] == [ids[1]]
        result = await context.client.get(endpoint, headers=headers[role], params={"q": "%"})
        assert [row["id"] for row in result.json()] == [ids[1]]
        assert (
            await context.client.get(endpoint, headers=headers[role], params={"status": "INVALID"})
        ).status_code == 422
    for role in (UserRole.MASTER, UserRole.WORKER):
        result = await context.client.get(endpoint, headers=headers[role], params={"q": "Камера"})
        assert result.status_code == 403
