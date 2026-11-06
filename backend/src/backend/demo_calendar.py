"""Add repeatable calendar examples to existing demo objects only."""

import asyncio
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from backend.core.config import get_settings
from backend.core.database import Database
from backend.core.time import utc_now
from backend.demo_seed import demo_id
from backend.identity.models import User, UserRole
from backend.operations.models import ObjectCalendarDay
from backend.workforce.models import WorkObject


async def populate_calendar(session, today: date) -> int:
    actor = await session.scalar(
        select(User).where(User.role == UserRole.ADMIN, User.archived_at.is_(None))
    )
    if actor is None:
        return 0
    created = 0
    for i, working, reason in [
        (0, False, "Демо: плановое обслуживание оборудования"),
        (1, True, "Демо: дополнительный рабочий день для приёмки материалов"),
        (2, False, "Демо: технический перерыв на объекте"),
    ]:
        identity = demo_id(f"object-calendar/{i}")
        obj = await session.get(WorkObject, demo_id(f"object/{i}"))
        if (
            obj is None
            or obj.archived_at is not None
            or await session.get(ObjectCalendarDay, identity)
        ):
            continue
        day = today + timedelta(days=7 + i)
        existing = await session.scalar(
            select(ObjectCalendarDay.id).where(
                ObjectCalendarDay.object_id == obj.id, ObjectCalendarDay.work_date == day
            )
        )
        if existing:
            continue
        now = utc_now()
        session.add(
            ObjectCalendarDay(
                id=identity,
                object_id=obj.id,
                work_date=day,
                is_working_day=working,
                reason=reason,
                created_by=actor.id,
                updated_by=actor.id,
                created_at=now,
                updated_at=now,
            )
        )
        created += 1
    await session.commit()
    return created


async def main():
    settings = get_settings()
    if settings.environment != "development":
        raise ValueError("Demo calendar is only available in development")
    database = Database(settings.database_url)
    try:
        async with database.session_factory() as session:
            print(
                {
                    "calendar_days_added": await populate_calendar(
                        session, utc_now().astimezone(ZoneInfo("Asia/Almaty")).date()
                    )
                }
            )
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
