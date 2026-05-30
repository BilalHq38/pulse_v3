"""FastAPI dependency factory for IP-based rate limiting.

This wraps shared.utils.rate_limit.build_rate_limiter (which prefers Redis but
falls back to an in-process LRU) so endpoints can declare
`Depends(rate_limit("scope", limit=5, window_seconds=60))` and get back a 429
response with a Retry-After header when the IP exceeds the bucket.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from fastapi import HTTPException, Request

from shared.config import rate_limit_redis_url
from shared.utils.rate_limit import InMemoryRateLimiter, RateLimiter, build_rate_limiter

logger = logging.getLogger(__name__)

_LIMITERS: dict[tuple[str, int, int], RateLimiter] = {}


def _get_limiter(scope: str, max_requests: int, window_seconds: int) -> RateLimiter:
    key = (scope, max_requests, window_seconds)
    limiter = _LIMITERS.get(key)
    if limiter is None:
        limiter = build_rate_limiter(
            max_requests=max_requests,
            window_seconds=window_seconds,
            redis_url=rate_limit_redis_url(),
            key_prefix=f"pulse:rate_limit:{scope}",
        )
        _LIMITERS[key] = limiter
    return limiter


def _client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    client = request.client
    if client and client.host:
        return client.host
    return "unknown"


def rate_limit(
    scope: str, *, limit: int, window_seconds: int
) -> Callable[[Request], Awaitable[None]]:
    """Return a FastAPI dependency that enforces a per-IP rate limit.

    The limiter is shared across requests (one instance per `(scope, limit,
    window)` triple). On Redis failures the limiter falls back to in-memory —
    that's a deliberate availability tradeoff, not a silent bypass: the bucket
    is still enforced, just per-process instead of globally.
    """

    async def _dependency(request: Request) -> None:
        limiter = _get_limiter(scope, limit, window_seconds)
        ip = _client_ip(request)
        try:
            allowed, retry_after = await limiter.allow(ip)
        except Exception as exc:
            logger.warning("rate_limit_check_failed scope=%s ip=%s error=%s", scope, ip, exc)
            limiter = InMemoryRateLimiter(max_requests=limit, window_seconds=window_seconds)
            _LIMITERS[(scope, limit, window_seconds)] = limiter
            allowed, retry_after = await limiter.allow(ip)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="rate_limit_exceeded",
                headers={"Retry-After": str(retry_after)},
            )

    return _dependency


__all__ = ["rate_limit"]
