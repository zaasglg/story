from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from httpx import ASGITransport, AsyncClient

from backend.communications.models import ChatThread
from backend.core.config import Settings
from backend.core.database import Database
from backend.core.time import utc_now
from backend.identity.models import AccountStatus, Base, EmployeeProfile, User, UserRole
from backend.identity.otp import MemoryRateLimiter
from backend.main import create_app
from backend.workforce.models import City


class CapturingOtpSender:
    def __init__(self) -> None:
        self.codes: dict[str, str] = {}

    async def send(self, phone: str, code: str) -> None:
        self.codes[phone] = code


@dataclass
class AppContext:
    client: AsyncClient
    database: Database
    otp_sender: CapturingOtpSender
    phones: dict[UserRole, str]
    settings: Settings


@pytest.fixture
async def context(tmp_path) -> AsyncIterator[AppContext]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    settings = Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        redis_url="redis://unused",
        jwt_secret="test-jwt-secret-that-is-long-and-independent",
        otp_pepper="test-otp-pepper-that-is-long-and-independent",
        otp_provider="test",
        otp_resend_seconds=10,
        otp_phone_hour_limit=10,
        otp_ip_hour_limit=100,
    )
    sender = CapturingOtpSender()
    limiter = MemoryRateLimiter()
    app = create_app(settings, database=database, rate_limiter=limiter, otp_sender=sender)

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
            yield AppContext(client, database, sender, phones, settings)


async def login(context: AppContext, role: UserRole) -> dict[str, object]:
    phone = context.phones[role]
    response = await context.client.post("/api/v1/auth/request-otp", json={"phone": phone})
    assert response.status_code == 202
    code = context.otp_sender.codes[phone]
    response = await context.client.post(
        "/api/v1/auth/verify-otp",
        json={"phone": phone, "code": code, "device_name": "pytest"},
    )
    assert response.status_code == 200, response.text
    return response.json()
