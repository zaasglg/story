from conftest import AppContext, login
from sqlalchemy import func, select

from backend.communications.models import SupportTicket
from backend.identity.models import AuditLog, User, UserRole


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_private_chat_scope_and_message_idempotency(context: AppContext) -> None:
    worker = auth(await login(context, UserRole.WORKER))
    master = auth(await login(context, UserRole.MASTER))
    admin = auth(await login(context, UserRole.ADMIN))
    async with context.database.session_factory() as session:
        master_id = await session.scalar(select(User.id).where(User.role == UserRole.MASTER))
    created = await context.client.post(f"/api/v1/chats/direct/{master_id}", headers=worker)
    assert created.status_code == 200
    thread_id = created.json()["id"]
    assigned_chat = await context.client.post("/api/v1/chats/manager", headers=worker)
    assert assigned_chat.status_code == 200
    assert assigned_chat.json()["id"] == thread_id
    no_manager = await context.client.post("/api/v1/chats/manager", headers=master)
    assert no_manager.status_code == 404
    path = f"/api/v1/chats/{thread_id}/messages"
    payload = {"client_message_id": "message-one", "body": "Private worker message"}
    sent = await context.client.post(path, headers=worker, json=payload)
    assert sent.status_code == 201
    replay = await context.client.post(path, headers=worker, json=payload)
    assert replay.json()["id"] == sent.json()["id"]
    conflict = await context.client.post(
        path, headers=worker, json={**payload, "body": "Different"}
    )
    assert conflict.status_code == 409
    own = await context.client.get(path, headers=master)
    assert own.json()[0]["body"] == payload["body"]
    for method in ("get", "post"):
        denied = await getattr(context.client, method)(
            path, headers=admin, **({"json": payload} if method == "post" else {})
        )
        assert denied.status_code == 404
    inbox = await context.client.get("/api/v1/me/notifications", headers=master)
    assert len(inbox.json()) == 1
    notification_id = inbox.json()[0]["id"]
    denied_read = await context.client.post(
        f"/api/v1/me/notifications/{notification_id}/read", headers=worker
    )
    assert denied_read.status_code == 404
    read = await context.client.post(
        f"/api/v1/me/notifications/{notification_id}/read", headers=master
    )
    assert read.json()["read_at"] is not None
    admin_inbox = await context.client.get("/api/v1/me/notifications", headers=admin)
    assert admin_inbox.json() == []


async def test_announcements_and_support_role_scope(context: AppContext) -> None:
    admin = auth(await login(context, UserRole.ADMIN))
    worker = auth(await login(context, UserRole.WORKER))
    master = auth(await login(context, UserRole.MASTER))
    payload = {"title": "Safety briefing", "body": "Meet at 08:00"}
    for headers in (worker, master):
        denied = await context.client.post("/api/v1/announcements", headers=headers, json=payload)
        assert denied.status_code == 403
    created = await context.client.post("/api/v1/announcements", headers=admin, json=payload)
    assert created.status_code == 201
    inbox = await context.client.get("/api/v1/me/notifications", headers=worker)
    assert inbox.json()[0]["title"] == "Safety briefing"
    ticket = await context.client.post(
        "/api/v1/me/support-tickets",
        headers=worker,
        json={"subject": "Equipment issue", "body": "Need help"},
    )
    assert ticket.status_code == 201
    other_tickets = await context.client.get("/api/v1/me/support-tickets", headers=master)
    assert other_tickets.json() == []
    denied = await context.client.post(
        f"/api/v1/admin/support-tickets/{ticket.json()['id']}/resolve", headers=worker
    )
    assert denied.status_code == 403
    resolved = await context.client.post(
        f"/api/v1/admin/support-tickets/{ticket.json()['id']}/resolve", headers=admin
    )
    assert resolved.json()["status"] == "RESOLVED"
    chats = await context.client.get("/api/v1/chats", headers=worker)
    assert any(item["key"] == "GENERAL" for item in chats.json())


async def test_support_retry_validation_and_queue_scope(context: AppContext) -> None:
    tokens = {role: auth(await login(context, role)) for role in UserRole}
    payload = {
        "subject": "  Missing equipment  ",
        "body": "  Please help  ",
        "client_request_id": "support-retry-1",
    }
    path = "/api/v1/me/support-tickets"
    first = await context.client.post(path, headers=tokens[UserRole.WORKER], json=payload)
    replay = await context.client.post(path, headers=tokens[UserRole.WORKER], json=payload)
    assert first.status_code == replay.status_code == 201
    assert first.json()["id"] == replay.json()["id"]
    assert first.json()["subject"] == "Missing equipment"
    conflict = await context.client.post(
        path, headers=tokens[UserRole.WORKER], json={**payload, "body": "Different request"}
    )
    assert conflict.status_code == 409
    other = await context.client.post(path, headers=tokens[UserRole.MASTER], json=payload)
    assert other.status_code == 201
    assert other.json()["id"] != first.json()["id"]
    for role in (UserRole.WORKER, UserRole.MASTER):
        denied = await context.client.get("/api/v1/admin/support-tickets", headers=tokens[role])
        assert denied.status_code == 403
        denied = await context.client.post(
            f"/api/v1/admin/support-tickets/{first.json()['id']}/resolve", headers=tokens[role]
        )
        assert denied.status_code == 403
        own = await context.client.get(path, headers=tokens[role])
        assert len(own.json()) == 1
    for role in (UserRole.ADMIN, UserRole.BOSS):
        queue = await context.client.get(
            "/api/v1/admin/support-tickets?limit=1", headers=tokens[role]
        )
        assert queue.status_code == 200
        assert len(queue.json()) == 1
    bad = await context.client.post(
        path, headers=tokens[UserRole.WORKER], json={**payload, "subject": "   "}
    )
    assert bad.status_code == 422
    injected = await context.client.post(
        path, headers=tokens[UserRole.WORKER], json={**payload, "status": "RESOLVED"}
    )
    assert injected.status_code == 422
    for _ in range(2):
        resolved = await context.client.post(
            f"/api/v1/admin/support-tickets/{first.json()['id']}/resolve",
            headers=tokens[UserRole.BOSS],
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "RESOLVED"
    async with context.database.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(SupportTicket)) == 2
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "SUPPORT_TICKET_CREATED")
            )
            == 2
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "SUPPORT_TICKET_RESOLVED")
            )
            == 1
        )
