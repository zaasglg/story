import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url

from backend.communications.models import ChatThread
from backend.core.config import Settings
from backend.core.database import Database
from backend.core.time import utc_now
from backend.identity.models import AccountStatus, Base, EmployeeProfile, User, UserRole
from backend.identity.otp import MemoryRateLimiter
from backend.identity.security import hash_password
from backend.main import create_app
from backend.workforce.models import City


@dataclass
class AppContext:
    client: AsyncClient
    database: Database
    phones: dict[UserRole, str]
    settings: Settings


@pytest.fixture
async def context(tmp_path) -> AsyncIterator[AppContext]:
    postgres_url = os.environ.get("M1917_TEST_CONTEXT_POSTGRES_URL")
    admin_connection = None
    database_name = None
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    if postgres_url:
        server = make_url(postgres_url)
        admin_connection = await asyncpg.connect(
            server.set(drivername="postgresql").render_as_string(hide_password=False)
        )
        database_name = f"defect_test_{uuid4().hex}"
        await admin_connection.execute(f'CREATE DATABASE "{database_name}"')
        database_url = server.set(
            drivername="postgresql+asyncpg", database=database_name
        ).render_as_string(hide_password=False)
    database = Database(database_url)
    settings = Settings(
        environment="test",
        database_url=database_url,
        redis_url="redis://unused",
        jwt_secret="test-jwt-secret-that-is-long-and-independent",
        otp_pepper="test-otp-pepper-that-is-long-and-independent",
    )
    limiter = MemoryRateLimiter()
    app = create_app(settings, database=database, rate_limiter=limiter)

    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    phones = {
        UserRole.BOSS: "+77011234567",
        UserRole.ADMIN: "+77021234567",
        UserRole.MASTER: "+77051234567",
        UserRole.WORKER: "+77071234567",
    }
    async with database.session_factory() as session:
        users: dict[UserRole, User] = {}
        profiles: dict[UserRole, EmployeeProfile] = {}
        now = utc_now()
        session.add(ChatThread(key="GENERAL", created_at=now))
        city = City(name="Lipetsk", timezone="Europe/Moscow", created_at=now)
        session.add(city)
        await session.flush()
        for index, (role, phone) in enumerate(phones.items(), start=1):
            user = User(
                phone=phone,
                password_hash=hash_password("Test-password-123"),
                role=role,
                status=AccountStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )
            session.add(user)
            await session.flush()
            users[role] = user
            profile = EmployeeProfile(
                user_id=user.id,
                full_name=f"{role.value.title()} User",
                employee_id=f"EMP-{index:03d}",
                city="Almaty",
                city_id=city.id,
                created_at=now,
                updated_at=now,
            )
            session.add(profile)
            profiles[role] = profile
        await session.flush()
        profiles[UserRole.WORKER].master_user_id = users[UserRole.MASTER].id
        await session.commit()

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            try:
                yield AppContext(client, database, phones, settings)
            finally:
                await database.dispose()
                if admin_connection is not None:
                    await admin_connection.execute(f'DROP DATABASE "{database_name}" WITH (FORCE)')
                    await admin_connection.close()


async def login(context: AppContext, role: UserRole) -> dict[str, object]:
    phone = context.phones[role]
    response = await context.client.post(
        "/api/v1/auth/login",
        json={"phone": phone, "password": "Test-password-123", "device_name": "pytest"},
    )
    assert response.status_code == 200, response.text
    return response.json()
