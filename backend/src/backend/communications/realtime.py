"""Authenticated, read-only chat invalidations over WebSocket."""

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPAuthorizationCredentials

from backend.authorization.permissions import Permission, has_permission
from backend.communications.routes import require_thread
from backend.core.errors import ApiError
from backend.identity.dependencies import get_current_user

router = APIRouter()


async def authorize(socket: WebSocket, token: str, thread_id: UUID) -> None:
    async with socket.app.state.database.session_factory() as session:
        actor = await get_current_user(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
            session,
            socket.app.state.settings,
        )
        if not has_permission(actor.role, Permission.CHAT_USE):
            raise ApiError(403, "permission_denied", "Chat is unavailable")
        await require_thread(session, thread_id, actor)


@router.websocket("/chats/{thread_id}/events")
async def chat_events(socket: WebSocket, thread_id: UUID) -> None:
    origin = socket.headers.get("origin")
    if origin and origin not in socket.app.state.settings.cors_origins:
        await socket.close(code=1008)
        return
    await socket.accept()
    pending: list[asyncio.Task] = []
    try:
        # Never put a bearer token into URLs, query strings, or access logs.
        frame = await asyncio.wait_for(socket.receive_text(), timeout=5)
        if len(frame) > 8192:
            raise ValueError("oversized_auth")
        data = json.loads(frame)
        token = data.get("token") if isinstance(data, dict) else None
        if not isinstance(token, str):
            raise ValueError("missing_auth")
        await authorize(socket, token, thread_id)
        async with socket.app.state.chat_events.subscribe(thread_id) as receive:
            await socket.send_json({"type": "ready"})
            disconnect = asyncio.create_task(socket.receive())
            pending.append(disconnect)
            while True:
                event = asyncio.create_task(receive())
                pending.append(event)
                done, _ = await asyncio.wait(
                    [disconnect, event], return_when=asyncio.FIRST_COMPLETED
                )
                if disconnect in done:
                    if disconnect.result()["type"] != "websocket.disconnect":
                        await socket.close(code=1008)
                    return
                changed = event.result()
                pending.remove(event)
                # Fresh transaction on each event/heartbeat: revoked sessions,
                # changed roles and expired access tokens cannot keep listening.
                await authorize(socket, token, thread_id)
                await socket.send_json({"type": "changed" if changed else "heartbeat"})
    except (ApiError, ValueError, TimeoutError):
        await socket.close(code=1008)
    except WebSocketDisconnect:
        pass
    except Exception:
        # Redis/network errors: clients reconnect and retain HTTP fallback.
        try:
            await socket.close(code=1013)
        except RuntimeError:
            pass
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
