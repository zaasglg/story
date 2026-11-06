from io import BytesIO

from conftest import AppContext, login
from PIL import Image

from backend.identity.models import UserRole


async def test_admin_and_boss_publish_posts_visible_to_every_role(context: AppContext) -> None:
    headers = {
        role: {"Authorization": f"Bearer {(await login(context, role))['access_token']}"}
        for role in UserRole
    }
    source = BytesIO()
    Image.new("RGB", (40, 30), "navy").save(source, format="PNG")
    created = await context.client.post(
        "/api/v1/posts",
        headers=headers[UserRole.ADMIN],
        data={"title": "Новый объект", "body": "Команда завершила первый этап."},
        files={"image": ("news.png", source.getvalue(), "image/png")},
    )
    assert created.status_code == 201, created.text
    assert created.json()["author_role"] == "ADMIN"
    assert created.json()["author_name"] == "Admin User"
    assert created.json()["image_url"] == f"/posts/{created.json()['id']}/image"

    for role in UserRole:
        feed = await context.client.get("/api/v1/posts", headers=headers[role])
        assert feed.status_code == 200
        assert feed.json()[0]["title"] == "Новый объект"
        image = await context.client.get(
            "/api/v1" + feed.json()[0]["image_url"], headers=headers[role]
        )
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/jpeg"

    boss_post = await context.client.post(
        "/api/v1/posts",
        headers=headers[UserRole.BOSS],
        data={"title": "Итоги недели", "body": "Спасибо всей команде."},
    )
    assert boss_post.status_code == 201
    assert boss_post.json()["image_url"] is None


async def test_worker_and_master_cannot_publish_posts(context: AppContext) -> None:
    for role in (UserRole.WORKER, UserRole.MASTER):
        headers = {"Authorization": f"Bearer {(await login(context, role))['access_token']}"}
        response = await context.client.post(
            "/api/v1/posts",
            headers=headers,
            data={"title": "Недоступно", "body": "Этот пост нельзя создать."},
        )
        assert response.status_code == 403
