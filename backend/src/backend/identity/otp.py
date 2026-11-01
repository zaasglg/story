from collections import defaultdict
from typing import Protocol

from redis.asyncio import Redis


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
