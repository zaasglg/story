"""Opt-in PostgreSQL acceptance test; each run uses its own disposable database."""

import asyncio
import os
from datetime import timedelta
from uuid import uuid4

import asyncpg
import pytest
from conftest import AppContext, login
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from test_operations_api import auth, prepare_assignment

from backend.access import routes as pass_routes
from backend.access import shifts
from backend.access.models import ShiftScanEvent
from backend.core.config import Settings
from backend.core.database import Database
from backend.core.time import utc_now
from backend.identity.models import AccountStatus, Base, EmployeeProfile, User, UserRole
from backend.identity.otp import MemoryRateLimiter
from backend.identity.security import hash_password
from backend.main import create_app
from backend.workforce.models import City


@pytest.mark.skipif(
    not os.environ.get("M1917_TEST_POSTGRES_URL"),
    reason="Dedicated PostgreSQL test server required",
)
async def test_concurrent_auto_scans_commit_one_action_per_worker(monkeypatch):
    server_url = make_url(os.environ["M1917_TEST_POSTGRES_URL"])
    db_name = f"qr_acceptance_{uuid4().hex}"
    connection = await asyncpg.connect(
        server_url.set(drivername="postgresql").render_as_string(hide_password=False)
    )
    await connection.execute(f'CREATE DATABASE "{db_name}"')
    database_url = server_url.set(database=db_name).render_as_string(hide_password=False)
    database = Database(database_url)
    try:
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        settings = Settings(
            environment="test",
            database_url=database_url,
            redis_url="redis://unused",
            jwt_secret="independent-postgres-acceptance-test-jwt-secret",
            otp_pepper="independent-postgres-acceptance-test-client-secret",
        )
        phones = {role: f"+7700000000{index}" for index, role in enumerate(UserRole, 1)}
        now = utc_now()
        async with database.session_factory() as session:
            city = City(name="Lipetsk", timezone="Europe/Moscow", created_at=now)
            session.add(city)
            await session.flush()
            for role, phone in phones.items():
                user = User(
                    phone=phone,
                    role=role,
                    password_hash=hash_password("Test-password-123"),
                    status=AccountStatus.ACTIVE,
                    created_at=now,
                    updated_at=now,
                )
                session.add(user)
                await session.flush()
                session.add(
                    EmployeeProfile(
                        user_id=user.id,
                        city_id=city.id,
                        full_name=role.value,
                        employee_id=role.value,
                        created_at=now,
                        updated_at=now,
                    )
                )
            await session.commit()
        app = create_app(settings, database=database, rate_limiter=MemoryRateLimiter())
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                context = AppContext(client, database, phones, settings)
                admin = await login(context, UserRole.ADMIN)
                await prepare_assignment(context, admin)
                worker = auth(await login(context, UserRole.WORKER))
                master = auth(await login(context, UserRole.MASTER))
                clock = utc_now()
                monkeypatch.setattr(shifts, "utc_now", lambda: clock)
                monkeypatch.setattr(pass_routes, "utc_now", lambda: clock)

                async def pair():
                    tokens = [
                        (await client.get("/api/v1/workers/me/pass", headers=worker)).json()[
                            "qr_payload"
                        ]
                        for _ in range(2)
                    ]
                    return await asyncio.gather(
                        *[
                            client.post(
                                "/api/v1/master/shift-scans",
                                headers=master,
                                json={"qr_payload": token},
                            )
                            for token in tokens
                        ]
                    )

                arrival = await pair()
                assert sorted(response.status_code for response in arrival) == [200, 409]
                assert (
                    next(response for response in arrival if response.status_code == 200).json()[
                        "action"
                    ]
                    == "CHECK_IN"
                )
                clock += timedelta(hours=8)
                departure = await pair()
                assert sorted(response.status_code for response in departure) == [200, 409]
                assert (
                    next(response for response in departure if response.status_code == 200).json()[
                        "action"
                    ]
                    == "CHECK_OUT"
                )
                async with database.session_factory() as session:
                    assert (
                        await session.scalar(select(func.count()).select_from(ShiftScanEvent)) == 2
                    )
    finally:
        await database.dispose()
        await connection.execute(f'DROP DATABASE "{db_name}" WITH (FORCE)')
        await connection.close()
