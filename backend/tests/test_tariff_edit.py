from datetime import date
from uuid import UUID

import pytest
from conftest import login
from test_operations_api import auth, prepare_assignment

from backend.core.errors import ApiError
from backend.identity.models import UserRole
from backend.operations.routes import applicable_tariff


async def test_edit_tariff_and_inclusive_periods(context):
    admin = await login(context, UserRole.ADMIN)
    _, _, _, obj = await prepare_assignment(context, admin)
    payload = {
        "object_id": obj,
        "rate_per_m3": "100",
        "currency": "RUB",
        "effective_from": "2026-10-01",
        "effective_to": "2026-10-10",
    }
    first = await context.client.post("/api/v1/admin/tariffs", headers=auth(admin), json=payload)
    assert first.status_code == 201, first.text
    next_payload = {**payload, "effective_from": "2026-10-11", "effective_to": "2026-10-20"}
    second = await context.client.post(
        "/api/v1/admin/tariffs", headers=auth(admin), json=next_payload
    )
    assert second.status_code == 201
    url = f"/api/v1/admin/tariffs/{first.json()['id']}"
    edit = {key: value for key, value in payload.items() if key != "object_id"}
    updated = await context.client.patch(
        url, headers=auth(admin), json={**edit, "rate_per_m3": "150"}
    )
    assert updated.status_code == 200, updated.text
    assert float(updated.json()["rate_per_m3"]) == 150
    overlap = await context.client.patch(
        url, headers=auth(admin), json={**edit, "effective_to": "2026-10-11"}
    )
    assert overlap.status_code == 409
    invalid = await context.client.patch(
        url, headers=auth(admin), json={**edit, "effective_to": "2026-09-01"}
    )
    assert invalid.status_code == 422
    worker = await login(context, UserRole.WORKER)
    denied = await context.client.patch(url, headers=auth(worker), json=edit)
    assert denied.status_code == 403
    async with context.database.session_factory() as session:
        assert (await applicable_tariff(session, UUID(obj), date(2026, 10, 10))).id == UUID(
            first.json()["id"]
        )
        assert (await applicable_tariff(session, UUID(obj), date(2026, 10, 11))).id == UUID(
            second.json()["id"]
        )
        with pytest.raises(ApiError):
            await applicable_tariff(session, UUID(obj), date(2026, 10, 21))
