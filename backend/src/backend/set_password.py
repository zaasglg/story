"""Set an existing employee's password using an interactive prompt."""

import argparse
import asyncio
from getpass import getpass

from sqlalchemy import select, update

from backend.audit.service import add_audit_event
from backend.core.config import get_settings
from backend.core.database import Database
from backend.core.time import utc_now
from backend.identity.models import AuthSession, User
from backend.identity.phone import normalize_phone
from backend.identity.security import hash_password


async def set_password(phone: str, password: str) -> None:
    database = Database(get_settings().database_url)
    try:
        async with database.session_factory() as session:
            user = await session.scalar(
                select(User).where(User.phone == normalize_phone(phone)).with_for_update()
            )
            if user is None:
                raise ValueError("Employee was not found")
            user.password_hash = hash_password(password)
            user.auth_version += 1
            user.updated_at = utc_now()
            await session.execute(
                update(AuthSession)
                .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=utc_now(), revoke_reason="password_changed")
            )
            add_audit_event(
                session, action="AUTH_PASSWORD_SET", entity_type="user", entity_id=user.id
            )
            await session.commit()
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phone")
    args = parser.parse_args()
    password = getpass("New password (8–128 characters): ")
    if not 8 <= len(password) <= 128:
        parser.error("Password must contain 8–128 characters")
    if password != getpass("Confirm password: "):
        parser.error("Passwords do not match")
    asyncio.run(set_password(args.phone, password))
    print("Password saved; previous sessions revoked.")


if __name__ == "__main__":
    main()
