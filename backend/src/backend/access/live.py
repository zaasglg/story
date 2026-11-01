"""Durable, minimal QR event feed. Authentication is rechecked on every heartbeat."""

import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import func, select

from backend.access.models import ShiftScanEvent
from backend.access.routes import Session
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission, has_permission
from backend.core.errors import ApiError
from backend.identity.dependencies import CurrentUser, get_current_user

router = APIRouter(tags=["attendance events"])
Reader = Annotated[CurrentUser, Depends(require_permission(Permission.WORKDAY_READ_ALL))]


@router.get("/admin/attendance-events")
async def feed(
    session: Session,
    actor: Reader,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
):
    rows = list(
        (
            await session.scalars(
                select(ShiftScanEvent)
                .where(ShiftScanEvent.event_sequence > after)
                .order_by(ShiftScanEvent.event_sequence)
                .limit(limit)
            )
        ).all()
    )
    return {
        "events": [
            {
                "id": row.event_sequence,
                "type": "attendance.changed",
                "attendance_id": row.attendance_id,
                "object_id": row.object_id,
                "city_id": row.city_id,
                "work_date": row.work_date,
            }
            for row in rows
        ],
        "cursor": rows[-1].event_sequence if rows else after,
        "has_more": len(rows) == limit,
    }


@router.websocket("/admin/attendance-events/live")
async def live(socket: WebSocket):
    origin = socket.headers.get("origin")
    if origin and origin not in socket.app.state.settings.cors_origins:
        await socket.close(code=1008)
        return
    await socket.accept()
    try:
        frame = await asyncio.wait_for(socket.receive_text(), 5)
        if len(frame) > 8192:
            raise ValueError("oversized_auth")
        token = json.loads(frame)["token"]
        last = None
        while True:
            async with socket.app.state.database.session_factory() as session:
                actor = await get_current_user(
                    HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
                    session,
                    socket.app.state.settings,
                )
                if not has_permission(actor.role, Permission.WORKDAY_READ_ALL):
                    raise ApiError(403, "permission_denied", "Permission denied")
                latest = await session.scalar(select(func.max(ShiftScanEvent.event_sequence))) or 0
            await socket.send_json(
                {"type": "ready" if last is None else "changed" if last != latest else "heartbeat"}
            )
            last = latest
            try:
                incoming = await asyncio.wait_for(socket.receive(), timeout=1)
                if incoming["type"] == "websocket.disconnect":
                    return
                await socket.close(code=1008)
                return
            except TimeoutError:
                pass
    except (ApiError, ValueError, KeyError, TypeError, TimeoutError):
        await socket.close(code=1008)
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await socket.close(code=1013)
        except RuntimeError:
            pass
