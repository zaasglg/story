"""Dedicated attendance worker; activation is explicit, disabled by default."""

import asyncio
import logging
import signal

from backend.access.attendance_job import reconcile_attendance
from backend.core.config import get_settings
from backend.core.database import Database


async def serve():
    settings = get_settings()
    if not settings.attendance_auto_enabled or settings.attendance_auto_from is None:
        raise SystemExit("Set attendance_auto_enabled and an explicit attendance_auto_from date")
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    database = Database(settings.database_url)
    try:
        while not stop.is_set():
            try:
                async with database.session_factory() as session:
                    await reconcile_attendance(session, settings)
            except Exception:
                logging.getLogger(__name__).error("attendance_iteration_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.attendance_poll_seconds)
            except TimeoutError:
                pass
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(serve())
