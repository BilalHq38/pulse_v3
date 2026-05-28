from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from shared.config import cache_redis_url

try:
    import redis.asyncio as redis
except Exception:  # pragma: no cover - optional dependency guard
    redis = None

logger = logging.getLogger(__name__)


class _InMemoryTTLCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, str]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> str | None:
        now = time.time()
        async with self._lock:
            payload = self._store.get(key)
            if payload is None:
                return None
            expires_at, value = payload
            if expires_at <= now:
                self._store.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        expires_at = time.time() + max(1, int(ttl_seconds))
        async with self._lock:
            self._store[key] = (expires_at, value)

    async def delete(self, *keys: str) -> None:
        async with self._lock:
            for key in keys:
                self._store.pop(key, None)


class CacheClient:
    def __init__(self, *, namespace: str) -> None:
        self.namespace = (namespace or "pulse").strip() or "pulse"
        self._redis_url = cache_redis_url()
        self._memory = _InMemoryTTLCache()
        self._redis = None
        if self._redis_url and redis is not None:
            try:
                self._redis = redis.from_url(
                    self._redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    health_check_interval=30,
                    retry_on_timeout=True,
                )
            except Exception as exc:
                logger.warning("Redis cache client init failed (%s), using in-memory cache", exc)
                self._redis = None

    @property
    def using_redis(self) -> bool:
        return self._redis is not None

    def _key(self, key: str) -> str:
        return f"{self.namespace}:{str(key or '').strip()}"

    @staticmethod
    def _json_default(value: Any) -> Any:
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        return str(value)

    async def get_json(self, key: str) -> Any:
        storage_key = self._key(key)
        raw: str | None = None
        if self._redis is not None:
            try:
                raw = await self._redis.get(storage_key)
            except Exception as exc:
                logger.warning("Redis cache read failed (%s), using in-memory cache", exc)
        if raw is None:
            raw = await self._memory.get(storage_key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def set_json(self, key: str, value: Any, *, ttl_seconds: int = 60) -> None:
        storage_key = self._key(key)
        payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=self._json_default)
        if self._redis is not None:
            try:
                await self._redis.set(storage_key, payload, ex=max(1, int(ttl_seconds)))
                return
            except Exception as exc:
                logger.warning("Redis cache write failed (%s), using in-memory cache", exc)
        await self._memory.set(storage_key, payload, ttl_seconds=max(1, int(ttl_seconds)))

    async def delete(self, *keys: str) -> None:
        storage_keys = [self._key(key) for key in keys if str(key or "").strip()]
        if not storage_keys:
            return
        if self._redis is not None:
            try:
                await self._redis.delete(*storage_keys)
            except Exception as exc:
                logger.warning("Redis cache delete failed (%s)", exc)
        await self._memory.delete(*storage_keys)

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            finally:
                self._redis = None


_CACHE_CLIENTS: dict[str, CacheClient] = {}


def get_cache_client(*, namespace: str = "pulse") -> CacheClient:
    key = (namespace or "pulse").strip() or "pulse"
    client = _CACHE_CLIENTS.get(key)
    if client is None:
        client = CacheClient(namespace=key)
        _CACHE_CLIENTS[key] = client
    return client


async def close_cache_clients() -> None:
    clients = list(_CACHE_CLIENTS.values())
    _CACHE_CLIENTS.clear()
    for client in clients:
        await client.close()


__all__ = ["CacheClient", "close_cache_clients", "get_cache_client"]
