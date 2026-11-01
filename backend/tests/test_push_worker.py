import asyncio
from unittest.mock import AsyncMock

from conftest import AppContext

from backend.communications import worker


async def test_worker_recovers_without_logging_exception_contents(
    context: AppContext, monkeypatch, caplog
):
    stop = asyncio.Event()
    attempts = 0

    async def dispatch(*args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("private-token-and-key")
        stop.set()
        return False

    monkeypatch.setattr(worker, "dispatch_one", dispatch)
    settings = context.settings.model_copy(update={"push_poll_seconds": 0.001})
    await asyncio.wait_for(worker.run_worker(settings, context.database, object(), stop), timeout=2)
    assert attempts == 2
    assert "push_iteration_failed" in caplog.text
    assert "private-token-and-key" not in caplog.text


async def test_disabled_worker_never_creates_database_or_provider(context: AppContext, monkeypatch):
    database = AsyncMock()
    provider = AsyncMock()
    monkeypatch.setattr(worker, "Database", database)
    monkeypatch.setattr(worker, "FirebasePushProvider", provider)
    # Trigger shutdown through the installed signal callback, without real process signals.
    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "add_signal_handler",
        lambda signum, callback: callback(),
    )
    await worker.serve(context.settings.model_copy(update={"push_enabled": False}))
    database.assert_not_called()
    provider.assert_not_called()
