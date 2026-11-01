"""Dedicated push worker. It does not consume the queue when disabled."""

import asyncio
import logging
import signal

from backend.communications.dispatch import PushProvider, dispatch_one
from backend.communications.fcm import FirebasePushProvider
from backend.core.config import Settings, get_settings
from backend.core.crypto import PiiCipher
from backend.core.database import Database

logger = logging.getLogger("backend.push-worker")


async def run_worker(
    settings: Settings, database: Database, provider: PushProvider, stop: asyncio.Event
) -> None:
    cipher = PiiCipher(settings.pii_encryption_key.get_secret_value())
    while not stop.is_set():
        try:
            async with database.session_factory() as session:
                processed = await dispatch_one(session, provider, cipher)
            if processed:
                continue
        except Exception:
            # Provider/database exceptions can contain credentials; log a fixed code.
            logger.warning("push_iteration_failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.push_poll_seconds)
        except TimeoutError:
            pass


async def serve(settings: Settings) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    if not settings.push_enabled:
        logger.info("push_disabled; queue is not consumed")
        await stop.wait()
        return
    database = Database(settings.database_url)
    try:
        provider = FirebasePushProvider(settings)
    except Exception:
        logger.error("push_configuration_invalid")
        await database.dispose()
        raise SystemExit(2) from None
    try:
        logger.info("push_worker_started")
        await run_worker(settings, database, provider, stop)
    finally:
        provider.close()
        await database.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve(get_settings()))
