from conftest import login
from test_equipment_expenses import auth

from backend.identity.models import UserRole


async def test_optional_evidence_upload_request_and_approval_access(context):
    admin = auth(await login(context, UserRole.ADMIN))
    boss = auth(await login(context, UserRole.BOSS))
    worker = auth(await login(context, UserRole.WORKER))
    uploaded = await context.client.post(
        "/api/v1/finance/evidence",
        headers=admin,
        files={"file": ("proof.pdf", b"%PDF-1.4 proof", "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    url = uploaded.json()["url"]
    employees = await context.client.get("/api/v1/admin/workers", headers=admin)
    payload = {
        "worker_user_id": employees.json()[0]["id"],
        "type": "BONUS",
        "amount": "100",
        "period": "2026-11-01",
        "reason": "Good work",
        "evidence_url": url,
    }
    created = await context.client.post(
        "/api/v1/admin/financial-requests",
        headers={**admin, "Idempotency-Key": "evidence-bonus"},
        json=payload,
    )
    assert created.status_code == 201, created.text
    assert created.json()["evidence_url"] == url
    pending = await context.client.get("/api/v1/boss/pending-approvals", headers=boss)
    assert pending.json()[0]["evidence_url"] == url
    for headers in (admin, boss):
        response = await context.client.get("/api/v1" + url, headers=headers)
        assert response.status_code == 200
        assert response.content == b"%PDF-1.4 proof"
    denied = await context.client.get("/api/v1" + url, headers=worker)
    assert denied.status_code == 403
    denied_upload = await context.client.post(
        "/api/v1/finance/evidence", headers=worker, files={"file": ("proof.pdf", b"%PDF-1.4 proof")}
    )
    assert denied_upload.status_code == 403
    missing = await context.client.post(
        "/api/v1/admin/financial-requests",
        headers={**admin, "Idempotency-Key": "missing-evidence"},
        json={
            **payload,
            "evidence_url": "/finance/evidence/00000000-0000-0000-0000-000000000000/file",
        },
    )
    assert missing.status_code == 422
    optional = await context.client.post(
        "/api/v1/admin/financial-requests",
        headers={**admin, "Idempotency-Key": "no-evidence"},
        json={**payload, "evidence_url": None},
    )
    assert optional.status_code == 201
    any_date = await context.client.post(
        "/api/v1/admin/financial-requests",
        headers={**admin, "Idempotency-Key": "any-date"},
        json={**payload, "period": "2026-11-27", "evidence_url": None},
    )
    assert any_date.status_code == 201, any_date.text
    assert any_date.json()["period"] == "2026-11-01"
    replay = await context.client.post(
        "/api/v1/admin/financial-requests",
        headers={**admin, "Idempotency-Key": "any-date"},
        json={**payload, "period": "2026-11-27", "evidence_url": None},
    )
    assert replay.json()["id"] == any_date.json()["id"]
