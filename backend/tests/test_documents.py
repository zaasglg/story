from conftest import AppContext, login
from test_operations_api import auth

from backend.identity.models import UserRole


async def test_admin_uploads_and_worker_reads_own_document(context: AppContext) -> None:
    admin = auth(await login(context, UserRole.ADMIN))
    worker = auth(await login(context, UserRole.WORKER))
    profile = (await context.client.get("/api/v1/workers/me", headers=worker)).json()
    created = await context.client.post(
        "/api/v1/admin/documents",
        headers=admin,
        data={
            "worker_user_id": profile["id"],
            "document_type": "MEDICAL",
            "title": "Medical book",
            "issue_date": "2026-01-01",
            "expires_at": "2027-01-01",
        },
        files={"file": ("medical.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
    )
    assert created.status_code == 201, created.text
    document = created.json()
    assert document["status"] == "ACTIVE"

    own = await context.client.get("/api/v1/workers/me/documents", headers=worker)
    assert own.status_code == 200
    assert own.json()[0]["title"] == "Medical book"
    downloaded = await context.client.get(
        f"/api/v1/workers/me/documents/{document['id']}/file", headers=worker
    )
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"%PDF-")
    assert downloaded.headers["cache-control"] == "private, no-store"


async def test_documents_are_private_and_validate_files(context: AppContext) -> None:
    admin = auth(await login(context, UserRole.ADMIN))
    master = auth(await login(context, UserRole.MASTER))
    worker = auth(await login(context, UserRole.WORKER))
    profile = (await context.client.get("/api/v1/workers/me", headers=worker)).json()
    denied = await context.client.get("/api/v1/admin/documents", headers=master)
    assert denied.status_code == 403
    invalid = await context.client.post(
        "/api/v1/admin/documents",
        headers=admin,
        data={
            "worker_user_id": profile["id"],
            "document_type": "PASSPORT",
            "title": "Passport",
        },
        files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
    )
    assert invalid.status_code == 422
