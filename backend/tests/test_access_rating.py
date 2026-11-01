import base64
import hashlib
from datetime import timedelta
from decimal import Decimal

from conftest import AppContext, login
from sqlalchemy import select

from backend.access.models import PassToken
from backend.core.time import utc_now
from backend.identity.models import User, UserRole


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_pass_authorization_revoke_replay_and_roles(context: AppContext) -> None:
    admin = auth(await login(context, UserRole.ADMIN))
    worker = auth(await login(context, UserRole.WORKER))
    master = auth(await login(context, UserRole.MASTER))
    boss = auth(await login(context, UserRole.BOSS))
    async with context.database.session_factory() as session:
        worker_id = await session.scalar(select(User.id).where(User.role == UserRole.WORKER))
    blocked = await context.client.get("/api/v1/workers/me/pass", headers=worker)
    assert blocked.status_code == 200
    assert blocked.json()["status"] == "BLOCKED"
    assert blocked.json()["permissions"] == []
    today = utc_now().date()
    payload = {
        "worker_user_id": str(worker_id),
        "valid_from": (today - timedelta(days=1)).isoformat(),
        "valid_until": (today + timedelta(days=1)).isoformat(),
        "reference": "Safety induction",
    }
    for headers in (worker, master, boss):
        denied = await context.client.post(
            "/api/v1/admin/work-authorizations", headers=headers, json=payload
        )
        assert denied.status_code == 403
    granted = await context.client.post(
        "/api/v1/admin/work-authorizations", headers=admin, json=payload
    )
    assert granted.status_code == 201
    active = await context.client.get("/api/v1/workers/me/pass", headers=worker)
    assert active.json()["status"] == "ACTIVE"
    assert active.json()["valid_from"] == payload["valid_from"]
    assert active.json()["valid_until"] == payload["valid_until"]
    assert active.json()["phone"] == context.phones[UserRole.WORKER]
    assert Decimal(str(active.json()["rating"])) == Decimal("5.0")
    assert base64.b64decode(active.json()["qr_png_base64"]).startswith(b"\x89PNG\r\n\x1a\n")
    for headers in (worker, master, boss):
        denied_scan = await context.client.post(
            "/api/v1/admin/pass-scans",
            headers=headers,
            json={"qr_payload": active.json()["qr_payload"]},
        )
        assert denied_scan.status_code == 403
    scanned = await context.client.post(
        "/api/v1/admin/pass-scans", headers=admin, json={"qr_payload": active.json()["qr_payload"]}
    )
    assert scanned.status_code == 200
    assert scanned.json()["status"] == "ACTIVE"
    replay = await context.client.post(
        "/api/v1/admin/pass-scans", headers=admin, json={"qr_payload": active.json()["qr_payload"]}
    )
    assert replay.status_code == 422
    issued_before_revoke = await context.client.get("/api/v1/workers/me/pass", headers=worker)
    revoked = await context.client.post(
        f"/api/v1/admin/work-authorizations/{granted.json()['id']}/revoke", headers=admin
    )
    assert revoked.status_code == 200
    blocked_scan = await context.client.post(
        "/api/v1/admin/pass-scans",
        headers=admin,
        json={"qr_payload": issued_before_revoke.json()["qr_payload"]},
    )
    assert blocked_scan.json()["status"] == "BLOCKED"
    assert "phone" not in blocked_scan.json() and "passport_number" not in blocked_scan.json()
    expired_pass = await context.client.get("/api/v1/workers/me/pass", headers=worker)
    async with context.database.session_factory() as session:
        token = await session.scalar(
            select(PassToken).where(
                PassToken.token_hash
                == hashlib.sha256(expired_pass.json()["qr_payload"].encode()).hexdigest()
            )
        )
        assert token is not None
        token.expires_at = utc_now() - timedelta(seconds=1)
        await session.commit()
    expired_scan = await context.client.post(
        "/api/v1/admin/pass-scans",
        headers=admin,
        json={"qr_payload": expired_pass.json()["qr_payload"]},
    )
    assert expired_scan.status_code == 422


async def test_rating_absence_reversal_and_14_day_reward(context: AppContext) -> None:
    admin = auth(await login(context, UserRole.ADMIN))
    worker = auth(await login(context, UserRole.WORKER))
    async with context.database.session_factory() as session:
        worker_id = await session.scalar(select(User.id).where(User.role == UserRole.WORKER))
    today = utc_now().date()
    absent = await context.client.post(
        "/api/v1/admin/attendance",
        headers=admin,
        json={
            "worker_user_id": str(worker_id),
            "work_date": (today - timedelta(days=15)).isoformat(),
            "status": "ABSENT",
        },
    )
    assert absent.status_code == 201
    correction = await context.client.patch(
        f"/api/v1/admin/attendance/{absent.json()['id']}", headers=admin, json={"status": "PRESENT"}
    )
    assert correction.status_code == 200
    rating = await context.client.get("/api/v1/workers/me/rating", headers=worker)
    assert Decimal(str(rating.json()["rating"])) == Decimal("5")
    for offset in range(14, 0, -1):
        attended = await context.client.post(
            "/api/v1/admin/attendance",
            headers=admin,
            json={
                "worker_user_id": str(worker_id),
                "work_date": (today - timedelta(days=offset)).isoformat(),
                "status": "PRESENT",
            },
        )
        assert attended.status_code == 201
    rating = await context.client.get("/api/v1/workers/me/rating", headers=worker)
    assert Decimal(str(rating.json()["rating"])) == Decimal("5")
    assert [event["event_type"] for event in rating.json()["history"]] == [
        "ABSENCE",
        "ATTENDANCE_REVERSED",
        "STREAK_14",
    ]
    violation = await context.client.post(
        "/api/v1/admin/rating-events",
        headers=admin,
        json={
            "worker_user_id": str(worker_id),
            "event_date": today.isoformat(),
            "event_type": "NO_BELT",
            "reason": "Safety violation",
        },
    )
    assert violation.status_code == 201
    rating = await context.client.get("/api/v1/workers/me/rating", headers=worker)
    assert Decimal(str(rating.json()["rating"])) == Decimal("3")
