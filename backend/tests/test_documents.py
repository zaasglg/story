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


async def test_self_upload_and_privacy_for_every_role(context: AppContext) -> None:
    admin = auth(await login(context, UserRole.ADMIN))
    owners = (await context.client.get("/api/v1/admin/document-owners", headers=admin)).json()
    assert {row["role"] for row in owners} == {role.value for role in UserRole}
    tokens = {role: auth(await login(context, role)) for role in UserRole}
    documents = {}
    for role, headers in tokens.items():
        created = await context.client.post(
            "/api/v1/me/documents",
            headers=headers,
            data={
                "document_type": "PASSPORT",
                "title": "Паспорт",
                "worker_user_id": owners[0]["id"],
            },
            files={"file": ("паспорт.pdf", b"%PDF-1.4", "application/pdf")},
        )
        assert created.status_code == 201, created.text
        item = created.json()
        owner = next(row for row in owners if row["role"] == role.value)
        assert item["worker_user_id"] == owner["id"]
        documents[role] = item["id"]
        own = await context.client.get("/api/v1/me/documents", headers=headers)
        assert [row["id"] for row in own.json()] == [item["id"]]
        download = await context.client.get(
            f"/api/v1/me/documents/{item['id']}/file", headers=headers
        )
        assert download.status_code == 200
        assert "filename*=UTF-8" in download.headers["content-disposition"]
    for role, headers in tokens.items():
        for other, document_id in documents.items():
            if role == other:
                continue
            url = f"/api/v1/me/documents/{document_id}"
            assert (await context.client.get(f"{url}/file", headers=headers)).status_code == 404
            assert (await context.client.delete(url, headers=headers)).status_code == 404
        if role != UserRole.ADMIN:
            assert (
                await context.client.get("/api/v1/admin/document-owners", headers=headers)
            ).status_code == 403
            assert (
                await context.client.post(
                    "/api/v1/admin/documents",
                    headers=headers,
                    data={
                        "worker_user_id": owners[0]["id"],
                        "document_type": "PASSPORT",
                        "title": "Passport",
                    },
                    files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")},
                )
            ).status_code == 403
    for role, document_id in documents.items():
        assert (
            await context.client.delete(f"/api/v1/me/documents/{document_id}", headers=tokens[role])
        ).status_code == 204


async def test_admin_can_upload_for_every_role(context: AppContext) -> None:
    admin = auth(await login(context, UserRole.ADMIN))
    owners = (await context.client.get("/api/v1/admin/document-owners", headers=admin)).json()
    for owner in owners:
        created = await context.client.post(
            "/api/v1/admin/documents",
            headers=admin,
            data={"worker_user_id": owner["id"], "document_type": "MEDICAL", "title": "Медкнижка"},
            files={"file": ("medical.pdf", b"%PDF-1.4", "application/pdf")},
        )
        assert created.status_code == 201, created.text
        response = await context.client.get(
            "/api/v1/admin/documents", headers=admin, params={"worker_user_id": owner["id"]}
        )
        assert [row["id"] for row in response.json()] == [created.json()["id"]]


async def test_self_upload_validates_files(context: AppContext) -> None:
    worker = auth(await login(context, UserRole.WORKER))
    for content, status in [(b"invalid", 422), (b"%PDF-" + b"x" * (10 * 1024 * 1024), 413)]:
        response = await context.client.post(
            "/api/v1/me/documents",
            headers=worker,
            data={"document_type": "PASSPORT", "title": "Passport"},
            files={"file": ("p.pdf", content, "application/pdf")},
        )
        assert response.status_code == status
