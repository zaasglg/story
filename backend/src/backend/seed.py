import asyncio
import json
from typing import Any

from pydantic import BaseModel, Field, TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.audit.service import add_audit_event
from backend.core.config import get_settings
from backend.core.database import Database
from backend.core.time import utc_now
from backend.identity.models import AccountStatus, EmployeeProfile, User, UserRole
from backend.identity.phone import normalize_phone
from backend.identity.security import hash_password


class BootstrapUser(BaseModel):
    phone: str
    password: str = Field(min_length=8, max_length=128, repr=False)
    role: UserRole
    full_name: str = Field(min_length=1, max_length=255)
    employee_id: str = Field(min_length=1, max_length=64)
    city: str | None = Field(default=None, max_length=128)


async def seed() -> None:
    settings = get_settings()
    payload: Any = json.loads(settings.bootstrap_users_json)
    users = TypeAdapter(list[BootstrapUser]).validate_python(payload)
    database = Database(settings.database_url)
    try:
        async with database.session_factory() as session:
            for item in users:
                phone = normalize_phone(item.phone)
                result = await session.execute(
                    select(User).where(User.phone == phone).options(selectinload(User.profile))
                )
                existing = result.scalar_one_or_none()
                if existing is not None:
                    if existing.role is not item.role:
                        raise RuntimeError(
                            f"Refusing to change role for existing bootstrap user {phone}"
                        )
                    if existing.password_hash is None:
                        existing.password_hash = hash_password(item.password)
                        existing.auth_version += 1
                    continue
                now = utc_now()
                user = User(
                    phone=phone,
                    password_hash=hash_password(item.password),
                    role=item.role,
                    status=AccountStatus.ACTIVE,
                    created_at=now,
                    updated_at=now,
                )
                session.add(user)
                await session.flush()
                session.add(
                    EmployeeProfile(
                        user_id=user.id,
                        full_name=item.full_name,
                        employee_id=item.employee_id,
                        city={
                            "Lipetsk": "Липецк",
                            "Kursk": "Курск",
                            "Voronezh": "Воронеж",
                            "Belgorod": "Белгород",
                            "Oryol": "Орёл",
                            "Orel": "Орёл",
                        }.get(item.city, item.city),
                        created_at=now,
                        updated_at=now,
                    )
                )
                add_audit_event(
                    session,
                    action="USER_BOOTSTRAPPED",
                    entity_type="user",
                    entity_id=user.id,
                    new_value={"role": user.role.value, "status": user.status.value},
                )
            await session.commit()
    finally:
        await database.dispose()


def main() -> None:
    asyncio.run(seed())
