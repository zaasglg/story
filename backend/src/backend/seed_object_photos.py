"""Explicit, idempotent local demo photo population; existing photos are preserved."""

import asyncio
from pathlib import Path

from sqlalchemy import select

from backend.core.config import get_settings
from backend.core.database import Database
from backend.core.time import utc_now
from backend.workforce.models import WorkObject, WorkObjectPhoto


async def seed_object_photos() -> None:
    database = Database(get_settings().database_url)
    assets = Path(__file__).parent / "demo_assets" / "objects"
    try:
        async with database.session_factory() as session:
            items = (
                await session.scalars(select(WorkObject).where(WorkObject.archived_at.is_(None)))
            ).all()
            count = 0
            for index, item in enumerate(items):
                if item.photo_url:
                    continue
                name = item.name.casefold()
                kind = (
                    "warehouse"
                    if "склад" in name
                    else "residential"
                    if "жк" in name
                    else "business"
                    if "центр" in name
                    else ["business", "residential", "warehouse"][index % 3]
                )
                content = (assets / f"{kind}.png").read_bytes()
                photo = await session.get(WorkObjectPhoto, item.id)
                if photo is None:
                    session.add(
                        WorkObjectPhoto(object_id=item.id, content=content, created_at=utc_now())
                    )
                item.photo_url = f"/object-photos/{item.id}"
                item.photo_is_demo = True
                count += 1
            await session.commit()
            print(f"Added demo photos: {count}; active objects: {len(items)}")
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(seed_object_photos())
