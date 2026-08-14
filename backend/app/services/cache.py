# Redis caching layer with graceful degradation.

import asyncio
import logging
import redis
from redis import asyncio as redis_async

from app.core.config import settings

logger = logging.getLogger(__name__)

class CacheLayer:
    # Simple Redis caching with graceful degradation.

    def __init__(self):
        self.redis_client = None
        try:
            client = redis.from_url(settings.redis_url, decode_responses=True)
            client.ping()
            self.redis_client = client
            logger.info("Redis cache initialized successfully")
        except Exception as e:
            # Leave redis_client as None so subsequent calls skip Redis entirely.
            logger.warning("Redis cache unavailable, continuing without caching: %s", e)

    def get(self, key: str) -> str | None:
        if not self.redis_client:
            return None
        try:
            return self.redis_client.get(key)
        except Exception as e:
            logger.warning("Cache get error for %s: %s", key, e)
            return None

    def set(self, key: str, value: str, ttl: int = 3600) -> bool:
        if not self.redis_client:
            return False
        try:
            self.redis_client.setex(key, ttl, value)
            return True
        except Exception as e:
            logger.warning("Cache set error for %s: %s", key, e)
            return False

    def delete(self, key: str) -> bool:
        if not self.redis_client:
            return False
        try:
            self.redis_client.delete(key)
            return True
        except Exception as e:
            logger.warning("Cache delete error for %s: %s", key, e)
            return False

class AsyncCacheLayer:
    # Async Redis caching with graceful degradation.
    #
    # Initialization is lazy and happens on first use inside the event loop,
    # so importing this module does not block on redis connectivity.

    def __init__(self):
        self._redis: redis_async.Redis | None = None
        self._initialized = False
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        # Lock is created on first access inside the event loop to avoid
        # creating it at import time before a loop exists.
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _client(self) -> redis_async.Redis | None:
        if self._initialized:
            return self._redis
        async with self._get_lock():
            # Re-check inside the lock in case another coroutine already initialised.
            if self._initialized:
                return self._redis
            try:
                client = redis_async.from_url(settings.redis_url, decode_responses=True)
                await client.ping()
                self._redis = client
                logger.info("Async Redis cache initialized successfully")
            except Exception as e:
                logger.warning("Async Redis cache unavailable, continuing without caching: %s", e)
                self._redis = None
            self._initialized = True
        return self._redis

    async def get(self, key: str) -> str | None:
        client = await self._client()
        if client is None:
            return None
        try:
            return await client.get(key)
        except Exception as e:
            logger.warning("Async cache get error for %s: %s", key, e)
            return None

    async def set(self, key: str, value: str, ttl: int = 3600) -> bool:
        client = await self._client()
        if client is None:
            return False
        try:
            await client.setex(key, ttl, value)
            return True
        except Exception as e:
            logger.warning("Async cache set error for %s: %s", key, e)
            return False

    async def delete(self, key: str) -> bool:
        client = await self._client()
        if client is None:
            return False
        try:
            await client.delete(key)
            return True
        except Exception as e:
            logger.warning("Async cache delete error for %s: %s", key, e)
            return False

# Global cache instances.
_cache: CacheLayer | None = None
_async_cache: AsyncCacheLayer | None = None

def get_cache() -> CacheLayer:
    # Get or create global sync cache instance.
    global _cache
    if _cache is None:
        _cache = CacheLayer()
    return _cache

def get_async_cache() -> AsyncCacheLayer:
    # Get or create global async cache instance (safe to call from async endpoints).
    global _async_cache
    if _async_cache is None:
        _async_cache = AsyncCacheLayer()
    return _async_cache
