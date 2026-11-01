"""Lossy invalidation hints; the database remains the source of chat history."""

import asyncio
import logging
from contextlib import asynccontextmanager
from uuid import UUID

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class ChatEvents:
    def __init__(self, redis: Redis | None) -> None:
        self.redis = redis
        self.listeners: dict[UUID, set[asyncio.Queue]] = {}

    async def publish(self, thread_id: UUID) -> None:
        try:
            if self.redis is not None:
                await asyncio.wait_for(
                    self.redis.publish(f"alash:chat:{thread_id}", "changed"), timeout=1
                )
            else:
                for queue in self.listeners.get(thread_id, set()):
                    if queue.empty():
                        queue.put_nowait(True)
        except Exception:
            # A failed hint must not turn an already committed message into a 500.
            logger.warning("chat_realtime_publish_failed")

    @asynccontextmanager
    async def subscribe(self, thread_id: UUID):
        if self.redis is not None:
            async with self.redis.pubsub() as subscription:
                await asyncio.wait_for(subscription.subscribe(f"alash:chat:{thread_id}"), timeout=5)
                acknowledgement = await subscription.get_message(timeout=5)
                if acknowledgement is None or acknowledgement["type"] != "subscribe":
                    raise TimeoutError("chat_subscription_not_ready")

                async def receive() -> bool:
                    message = await subscription.get_message(
                        ignore_subscribe_messages=True, timeout=25
                    )
                    return message is not None

                yield receive
        else:
            # In-process transport for injected test/development rate limiters only.
            queue: asyncio.Queue = asyncio.Queue(maxsize=1)
            self.listeners.setdefault(thread_id, set()).add(queue)

            async def receive() -> bool:
                try:
                    await asyncio.wait_for(queue.get(), timeout=25)
                    return True
                except TimeoutError:
                    return False

            try:
                yield receive
            finally:
                self.listeners[thread_id].discard(queue)
                if not self.listeners[thread_id]:
                    del self.listeners[thread_id]
