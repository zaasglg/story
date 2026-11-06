import pytest
from conftest import login
from test_operations_api import auth, prepare_assignment

from backend.identity.models import UserRole


@pytest.mark.parametrize("result", [{"hours": "8"}, {"m3": "2.5"}])
async def test_admin_can_record_one_unit(context, result):
    admin = await login(context, UserRole.ADMIN)
    worker, _, brigade, obj = await prepare_assignment(context, admin)
    tariff = await context.client.post(
        "/api/v1/admin/tariffs",
        headers=auth(admin),
        json={
            "object_id": obj,
            "rate_per_m3": "100",
            "currency": "RUB",
            "effective_from": "2026-01-01",
        },
    )
    assert tariff.status_code == 201
    payload = {
        "worker_user_id": worker,
        "brigade_id": brigade,
        "object_id": obj,
        "work_date": "2026-10-01",
        **result,
    }
    response = await context.client.post(
        "/api/v1/admin/work-days", headers=auth(admin), json=payload
    )
    assert response.status_code == 201, response.text
    row = response.json()
    missing = "m3" if "hours" in result else "hours"
    assert float(row[missing]) == 0
    edit = await context.client.patch(
        f"/api/v1/admin/work-days/{row['id']}", headers=auth(admin), json={"hours": "4", "m3": "0"}
    )
    assert edit.status_code == 200
    assert float(edit.json()["hours"]) == 4
    empty = await context.client.patch(
        f"/api/v1/admin/work-days/{row['id']}", headers=auth(admin), json={"hours": "0", "m3": "0"}
    )
    assert empty.status_code == 422
