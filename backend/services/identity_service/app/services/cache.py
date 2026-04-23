from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status

from services.identity_service.app.db.database import REDIS_URL

try:
    import redis.asyncio as redis
except Exception:  # pragma: no cover - runtime dependency fallback
    redis = None

logger = logging.getLogger(__name__)
redis_client: Any = None
_fallback_cache: dict[str, tuple[float, str]] = {}
_fallback_rate_limit: dict[str, int] = {}
_fallback_lock = asyncio.Lock()


def _ttl_seconds(value: int | None) -> int:
    raw = int(value or 0)
    return 1 if raw <= 0 else raw


async def _fallback_cache_get(key: str) -> Any:
    now = time.time()
    async with _fallback_lock:
        payload = _fallback_cache.get(key)
        if not payload:
            return None
        expires_at, serialized = payload
        if expires_at <= now:
            _fallback_cache.pop(key, None)
            return None
        try:
            return json.loads(serialized)
        except Exception:
            _fallback_cache.pop(key, None)
            return None


async def _fallback_cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    serialized = json.dumps(value, default=str)
    expires_at = time.time() + _ttl_seconds(ttl_seconds)
    async with _fallback_lock:
        _fallback_cache[key] = (expires_at, serialized)
        if len(_fallback_cache) > 20000:
            now = time.time()
            stale_keys = [item for item, (expiry, _val) in _fallback_cache.items() if expiry <= now]
            for stale_key in stale_keys[:5000]:
                _fallback_cache.pop(stale_key, None)


async def _fallback_cache_delete(keys: list[str]) -> None:
    if not keys:
        return
    async with _fallback_lock:
        for key in keys:
            _fallback_cache.pop(key, None)


async def _fallback_rate_limit_check(tenant_id: str, limit_per_minute: int) -> None:
    bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    bucket_key = f"{tenant_id}:{bucket}"
    async with _fallback_lock:
        _fallback_rate_limit[bucket_key] = _fallback_rate_limit.get(bucket_key, 0) + 1
        current = _fallback_rate_limit[bucket_key]
        if len(_fallback_rate_limit) > 40000:
            stale_prefix = datetime.now(timezone.utc).strftime("%Y%m%d%H")
            stale_keys = [key for key in _fallback_rate_limit if stale_prefix not in key]
            for stale_key in stale_keys[:12000]:
                _fallback_rate_limit.pop(stale_key, None)

    if current > limit_per_minute:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Tenant rate limit exceeded")


async def init_redis():
    global redis_client
    if redis_client is None:
        if redis is None:
            logger.warning("identity cache redis package unavailable, using in-memory fallback")
            return
        try:
            candidate = redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
            await candidate.ping()
            redis_client = candidate
            logger.info("identity cache connected to redis")
        except Exception as exc:
            redis_client = None
            logger.warning("identity cache redis unavailable, using in-memory fallback: %s", exc)


async def close_redis():
    global redis_client
    if redis_client is not None:
        try:
            await redis_client.close()
        except Exception as exc:
            logger.warning("identity cache redis close failed: %s", exc)
        redis_client = None


async def get_json(key: str) -> Any:
    if redis_client is not None:
        try:
            value = await redis_client.get(key)
            if value:
                return json.loads(value)
        except Exception as exc:
            logger.warning("identity cache redis get failed, falling back to memory: %s", exc)
    return await _fallback_cache_get(key)


async def set_json(key: str, value: Any, ttl_seconds: int = 300):
    ttl = _ttl_seconds(ttl_seconds)
    if redis_client is not None:
        try:
            await redis_client.set(key, json.dumps(value, default=str), ex=ttl)
        except Exception as exc:
            logger.warning("identity cache redis set failed, using memory fallback: %s", exc)
            await _fallback_cache_set(key, value, ttl)
            return
    else:
        await _fallback_cache_set(key, value, ttl)


async def delete_keys(keys: list[str]):
    if not keys:
        return
    if redis_client is not None:
        try:
            await redis_client.delete(*keys)
        except Exception as exc:
            logger.warning("identity cache redis delete failed, using memory fallback: %s", exc)
    await _fallback_cache_delete(keys)


async def enforce_rate_limit(tenant_id: str, limit_per_minute: int | None = None):
    limit = limit_per_minute or int(os.environ.get("TENANT_RATE_LIMIT_PER_MINUTE", "1000"))
    if redis_client is not None:
        try:
            now = datetime.now(timezone.utc)
            bucket = now.strftime("%Y%m%d%H%M")
            key = f"rate-limit:{tenant_id}:{bucket}"
            current = await redis_client.incr(key)
            if current == 1:
                await redis_client.expire(key, 70)
            if current > limit:
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Tenant rate limit exceeded")
            return
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("identity rate limit redis failure, using memory fallback: %s", exc)

    await _fallback_rate_limit_check(tenant_id, limit)


class CacheService:
    async def get(self, key: str) -> Any:
        return await get_json(key)

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        await set_json(key, value, ttl_seconds if ttl_seconds is not None else 300)

    async def delete(self, key: str) -> None:
        await delete_keys([key])


_cache_service = CacheService()


def get_cache_service() -> CacheService:
    return _cache_service
