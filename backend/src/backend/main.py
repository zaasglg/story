from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy import text

from backend.access.routes import router as access_router
from backend.communications.push import router as push_router
from backend.communications.routes import router as communications_router
from backend.core.config import Settings, get_settings
from backend.core.database import Database
from backend.core.errors import ApiError, install_error_handlers
from backend.core.middleware import RequestIdMiddleware
from backend.equipment.routes import router as equipment_router
from backend.finance.company import router as company_router
from backend.finance.payroll import router as payroll_router
from backend.finance.routes import router as finance_router
from backend.identity.otp import OtpSender, RateLimiter, RedisRateLimiter, build_otp_sender
from backend.identity.routes import router as identity_router
from backend.identity.service import AuthService
from backend.operations.routes import router as operations_router
from backend.workforce.routes import router as workforce_router


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    rate_limiter: RateLimiter | None = None,
    otp_sender: OtpSender | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    app_database = database or Database(app_settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        redis: Redis | None = None
        limiter = rate_limiter
        if limiter is None:
            redis = Redis.from_url(app_settings.redis_url, decode_responses=True)
            limiter = RedisRateLimiter(redis)
        sender = otp_sender or build_otp_sender(app_settings)
        app.state.settings = app_settings
        app.state.database = app_database
        app.state.redis = redis
        app.state.auth_service = AuthService(app_settings, sender, limiter)
        yield
        if redis is not None:
            await redis.aclose()
        await app_database.dispose()

    app = FastAPI(
        title=app_settings.app_name,
        version="0.1.0",
        openapi_url=f"{app_settings.api_prefix}/openapi.json",
        docs_url=f"{app_settings.api_prefix}/docs",
        lifespan=lifespan,
    )
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key"],
    )
    install_error_handlers(app)
    app.include_router(identity_router, prefix=app_settings.api_prefix)
    app.include_router(workforce_router, prefix=app_settings.api_prefix)
    app.include_router(operations_router, prefix=app_settings.api_prefix)
    app.include_router(equipment_router, prefix=app_settings.api_prefix)
    app.include_router(finance_router, prefix=app_settings.api_prefix)
    app.include_router(payroll_router, prefix=app_settings.api_prefix)
    app.include_router(company_router, prefix=app_settings.api_prefix)
    app.include_router(access_router, prefix=app_settings.api_prefix)
    app.include_router(communications_router, prefix=app_settings.api_prefix)
    app.include_router(push_router, prefix=app_settings.api_prefix)

    @app.get("/health/live", tags=["health"])
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    async def readiness(request: Request) -> dict[str, str]:
        try:
            async with request.app.state.database.session_factory() as session:
                await session.execute(text("SELECT 1"))
            redis = request.app.state.redis
            if redis is not None:
                await redis.ping()
        except Exception as exc:
            raise ApiError(503, "not_ready", "A required service is unavailable") from exc
        return {"status": "ready"}

    @app.get("/health", include_in_schema=False)
    async def legacy_health() -> dict[str, str]:
        return {"status": "ok"}

    return app
