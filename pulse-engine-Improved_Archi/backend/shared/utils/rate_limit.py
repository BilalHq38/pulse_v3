from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import Protocol

try:
    import redis.asyncio as redis
except Exception:  # pragma: no cover - optional dependency guard
    redis = None

logger = logging.getLogger(__name__)


class RateLimiter(Protocol):
    async def allow(self, key: str) -> tuple[bool, int]: ...

    async def close(self) -> None: ...


class InMemoryRateLimiter:
    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()
        self._last_cleanup = time.monotonic()

    def _cleanup(self, now: float, *, skip_key: str | None = None) -> None:
        cutoff = now - self.window_seconds
        for key in list(self._buckets.keys()):
            if skip_key is not None and key == skip_key:
                continue
            bucket = self._buckets.get(key)
            if bucket is None:
                continue
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if not bucket:
                self._buckets.pop(key, None)
        self._last_cleanup = now

    async def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        async with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] <= now - self.window_seconds:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                retry_after = max(1, int(self.window_seconds - (now - bucket[0])))
                return False, retry_after
            bucket.append(now)
            if now - self._last_cleanup >= self.window_seconds or len(self._buckets) > 1000:
                self._cleanup(now, skip_key=key)
            return True, 0

    async def close(self) -> None:
        return None


class RedisRateLimiter:
    def __init__(
        self,
        *,
        redis_url: str,
        max_requests: int,
        window_seconds: int,
        key_prefix: str = "pulse:rate_limit",
    ) -> None:
        if redis is None:
            raise RuntimeError("redis package is not available")
        self.max_requests = max_requests
        self.window_seconds = max(1, window_seconds)
        self.key_prefix = (key_prefix or "pulse:rate_limit").strip()
        self._client = redis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            health_check_interval=30,
            retry_on_timeout=True,
        )

    def _bucket_key(self, key: str) -> str:
        now_bucket = int(time.time() // self.window_seconds)
        return f"{self.key_prefix}:{now_bucket}:{key}"

    async def allow(self, key: str) -> tuple[bool, int]:
        bucket_key = self._bucket_key(key)
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.incr(bucket_key)
            pipe.expire(bucket_key, self.window_seconds, nx=True)
            result = await pipe.execute()
        current = int(result[0] or 0)
        if current > self.max_requests:
            ttl = await self._client.ttl(bucket_key)
            retry_after = max(1, int(ttl if ttl and ttl > 0 else self.window_seconds))
            return False, retry_after
        return True, 0

    async def close(self) -> None:
        await self._client.aclose()


def build_rate_limiter(
    *,
    max_requests: int,
    window_seconds: int,
    redis_url: str = "",
    key_prefix: str = "pulse:rate_limit",
) -> RateLimiter:
    if redis_url and redis is not None:
        try:
            return RedisRateLimiter(
                redis_url=redis_url,
                max_requests=max_requests,
                window_seconds=window_seconds,
                key_prefix=key_prefix,
            )
        except Exception as exc:
            logger.warning("Redis rate limiter disabled, falling back to in-memory: %s", exc)
    return InMemoryRateLimiter(max_requests=max_requests, window_seconds=window_seconds)
