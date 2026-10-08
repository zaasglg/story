import logging
from collections import defaultdict
from typing import Protocol

import httpx
from redis.asyncio import Redis

from backend.core.config import Settings
from backend.core.errors import ApiError

logger = logging.getLogger(__name__)


class OtpSender(Protocol):
    async def send(self, phone: str, code: str) -> None: ...


class ConsoleOtpSender:
    async def send(self, phone: str, code: str) -> None:
        logger.warning("Development OTP for %s: %s", phone, code)


class SmsOtpSender:
    def __init__(self, settings: Settings) -> None:
        if settings.sms_gateway_url is None or settings.sms_api_token is None:
            raise RuntimeError("SMS provider adapter is not configured")
        self.url = settings.sms_gateway_url
        self.token = settings.sms_api_token.get_secret_value()
        self.sender_name = settings.sms_sender_name

    async def send(self, phone: str, code: str) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                self.url,
                headers={"Authorization": f"Bearer {self.token}"},
                json={
                    "to": phone,
                    "sender": self.sender_name,
                    "message": f"ALASH M-1917: {code}",
                },
            )
            response.raise_for_status()


class RateLimiter(Protocol):
    async def hit(self, key: str, limit: int, window_seconds: int) -> bool: ...

    async def ttl(self, key: str) -> int: ...


class RedisRateLimiter:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        value = await self.redis.eval(
            """
            local current = redis.call('INCR', KEYS[1])
            if current == 1 then
                redis.call('EXPIRE', KEYS[1], ARGV[1])
            end
            return current
            """,
            1,
            key,
            window_seconds,
        )
        return int(value) <= limit

    async def ttl(self, key: str) -> int:
        value = await self.redis.ttl(key)
        return max(value, 0)


class MemoryRateLimiter:
    """Deterministic limiter for tests; it intentionally has no wall-clock expiry."""

    def __init__(self) -> None:
        self.values: defaultdict[str, int] = defaultdict(int)

    async def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        self.values[key] += 1
        return self.values[key] <= limit

    async def ttl(self, key: str) -> int:
        return 60


def build_otp_sender(settings: Settings) -> OtpSender:
    if settings.otp_provider in {"console", "test"}:
        return ConsoleOtpSender()
    if settings.otp_provider == "sms":
        return SmsOtpSender(settings)
    raise ApiError(500, "otp_provider_invalid", "OTP provider is invalid")
