import pytest
from conftest import AppContext, login

from backend.identity.models import UserRole


@pytest.mark.parametrize("role", list(UserRole))
async def test_receipt_upload_roles(context: AppContext, role):
    headers = {"Authorization": f"Bearer {(await login(context, role))['access_token']}"}
    response = await context.client.post(
        "/api/v1/finance/receipts",
        headers=headers,
        files={"file": ("receipt.pdf", b"%PDF-1.4\nreceipt", "application/pdf")},
    )
    assert response.status_code == (201 if role in (UserRole.BOSS, UserRole.ADMIN) else 403)
    if response.status_code == 201:
        download = await context.client.get("/api/v1" + response.json()["url"], headers=headers)
        assert download.status_code == 200
        assert download.content == b"%PDF-1.4\nreceipt"
        assert download.headers["content-type"] == "application/pdf"
        for other in (UserRole.WORKER, UserRole.MASTER):
            token = (await login(context, other))["access_token"]
            denied = await context.client.get(
                "/api/v1" + response.json()["url"], headers={"Authorization": f"Bearer {token}"}
            )
            assert denied.status_code == 403


async def test_invalid_and_oversize_receipt(context: AppContext):
    headers = {"Authorization": f"Bearer {(await login(context, UserRole.BOSS))['access_token']}"}
    for content, status in [(b"not a pdf", 422), (b"%PDF-" + b"x" * (10 * 1024 * 1024), 413)]:
        response = await context.client.post(
            "/api/v1/finance/receipts",
            headers=headers,
            files={"file": ("receipt.pdf", content, "application/pdf")},
        )
        assert response.status_code == status
