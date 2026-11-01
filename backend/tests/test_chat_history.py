from datetime import UTC, datetime
from uuid import uuid4

from conftest import AppContext, login
from sqlalchemy import select

from backend.communications.models import ChatMessage, ChatThread
from backend.identity.models import User, UserRole


async def test_chat_cursor_with_tied_dates_and_foreign_anchor(context: AppContext) -> None:
    tokens = await login(context, UserRole.MASTER)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    async with context.database.session_factory() as session:
        sender_id = await session.scalar(select(User.id).where(User.role == UserRole.MASTER))
        general = await session.scalar(select(ChatThread).where(ChatThread.key == "GENERAL"))
        private = ChatThread(key=str(uuid4()), created_at=datetime.now(UTC))
        session.add(private)
        await session.flush()
        stamp = datetime.now(UTC)
        rows = [
            ChatMessage(
                thread_id=general.id,
                sender_user_id=sender_id,
                client_message_id=str(i),
                body=str(i),
                created_at=stamp,
            )
            for i in range(3)
        ]
        foreign = ChatMessage(
            thread_id=private.id,
            sender_user_id=sender_id,
            client_message_id="foreign",
            body="private",
            created_at=stamp,
        )
        session.add_all([*rows, foreign])
        await session.commit()
        thread_id, foreign_id = general.id, foreign.id
    path = f"/api/v1/chats/{thread_id}/messages"
    first = await context.client.get(path, headers=headers, params={"limit": 1})
    assert first.status_code == 200
    second = await context.client.get(
        path, headers=headers, params={"limit": 1, "before_id": first.json()[0]["id"]}
    )
    third = await context.client.get(
        path, headers=headers, params={"limit": 1, "before_id": second.json()[0]["id"]}
    )
    ids = [page.json()[0]["id"] for page in (first, second, third)]
    assert len(set(ids)) == 3
    empty = await context.client.get(path, headers=headers, params={"before_id": ids[-1]})
    assert empty.json() == []
    denied = await context.client.get(path, headers=headers, params={"before_id": str(foreign_id)})
    assert denied.status_code == 404
