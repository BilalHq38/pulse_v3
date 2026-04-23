from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

import httpx
from fastapi import HTTPException

from shared.config import (
    ai_service_circuit_breaker_failures,
    ai_service_circuit_breaker_recovery_seconds,
    ai_service_retry_attempts,
    ai_service_retry_backoff_seconds,
    internal_service_secret,
)
from shared.tracing import current_trace_headers, ensure_trace_context

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


def build_internal_headers(
    *,
    authorization: str = "",
    company_id: str = "",
    user_id: str = "",
    user_role: str = "",
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if authorization:
        headers["Authorization"] = authorization
    if company_id:
        headers["X-Company-Id"] = company_id
    if user_id:
        headers["X-User-Id"] = user_id
    if user_role:
        headers["X-User-Role"] = user_role
    secret = internal_service_secret()
    if secret:
        headers["X-Internal-Service-Secret"] = secret
    ensure_trace_context()
    headers.update(current_trace_headers())
    return headers


@dataclass(slots=True)
class RetryPolicy:
    attempts: int = 3
    base_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 5.0


@dataclass(slots=True)
class CircuitBreaker:
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 30.0
    failure_count: int = 0
    opened_at: float | None = None
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def allow(self) -> None:
        with self._lock:
            if self.opened_at is None:
                return
            elapsed = time.monotonic() - self.opened_at
            if elapsed < self.recovery_timeout_seconds:
                raise RuntimeError("Circuit breaker is open")
            self.failure_count = 0
            self.opened_at = None

    def record_success(self) -> None:
        with self._lock:
            self.failure_count = 0
            self.opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self.failure_count += 1
            if self.failure_count >= self.failure_threshold:
                self.opened_at = time.monotonic()


def _retry_delay_seconds(policy: RetryPolicy, attempt: int, response: httpx.Response | None = None) -> float:
    if response is not None:
        retry_after = response.headers.get("retry-after", "").strip()
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
    delay = min(policy.max_backoff_seconds, policy.base_backoff_seconds * (2**attempt))
    jitter = delay * 0.25 * random.random()
    return delay + jitter


class ServiceClient:
    def __init__(
        self,
        base_url: str,
        timeout: float | None = None,
        *,
        retry_attempts: int | None = None,
        service_name: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout if timeout is not None else 30.0
        self.retry_policy = RetryPolicy(
            attempts=max(1, retry_attempts or ai_service_retry_attempts()),
            base_backoff_seconds=ai_service_retry_backoff_seconds(),
            max_backoff_seconds=max(ai_service_retry_backoff_seconds() * 8, 3.0),
        )
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=ai_service_circuit_breaker_failures(),
            recovery_timeout_seconds=ai_service_circuit_breaker_recovery_seconds(),
        )
        self.service_name = service_name or self.base_url.split("//", 1)[-1]
        self._timeout = httpx.Timeout(
            self.timeout,
            connect=min(5.0, self.timeout),
            read=self.timeout,
            write=self.timeout,
            pool=min(5.0, self.timeout),
        )
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=100,
                keepalive_expiry=30.0,
            ),
        )

    async def _request_once(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> httpx.Response:
        return await self._client.request(
            method=method,
            url=f"{self.base_url}{path}",
            headers=headers,
            params=params,
            json=json,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.retry_policy.attempts):
            try:
                self.circuit_breaker.allow()
                response = await self._request_once(
                    method,
                    path,
                    headers=headers,
                    params=params,
                    json=json,
                )
                if response.status_code >= 400:
                    if response.status_code in RETRYABLE_STATUS_CODES and attempt + 1 < self.retry_policy.attempts:
                        self.circuit_breaker.record_failure()
                        delay_seconds = _retry_delay_seconds(self.retry_policy, attempt, response)
                        logger.warning(
                            "service_client retry service=%s method=%s path=%s attempt=%s status=%s delay_seconds=%.2f",
                            self.service_name,
                            method,
                            path,
                            attempt + 1,
                            response.status_code,
                            delay_seconds,
                        )
                        await asyncio.sleep(delay_seconds)
                        continue
                    if response.status_code >= 500 or response.status_code == 429:
                        self.circuit_breaker.record_failure()
                    else:
                        self.circuit_breaker.record_success()
                    detail = "Unauthorized" if response.status_code in {401, 403} else "Upstream request failed"
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=detail,
                    )
                self.circuit_breaker.record_success()
                if not response.content:
                    return {}
                content_type = response.headers.get("content-type", "")
                if "application/json" in content_type:
                    return response.json()
                return {"raw": response.text}
            except HTTPException:
                raise
            except RuntimeError as exc:
                if "Circuit breaker is open" in str(exc):
                    raise HTTPException(
                        status_code=503,
                        detail="Upstream service temporarily unavailable",
                    ) from exc
                last_error = exc
                self.circuit_breaker.record_failure()
            except (httpx.RequestError, httpx.TimeoutException) as exc:
                last_error = exc
                self.circuit_breaker.record_failure()
                if attempt + 1 < self.retry_policy.attempts:
                    delay_seconds = _retry_delay_seconds(self.retry_policy, attempt)
                    logger.warning(
                        "service_client retry service=%s method=%s path=%s attempt=%s error=%s delay_seconds=%.2f",
                        self.service_name,
                        method,
                        path,
                        attempt + 1,
                        exc.__class__.__name__,
                        delay_seconds,
                    )
                    await asyncio.sleep(delay_seconds)
                    continue
            except Exception as exc:
                last_error = exc
                self.circuit_breaker.record_failure()
                break
        logger.error(
            "service_client unavailable service=%s method=%s path=%s error=%s",
            self.service_name,
            method,
            path,
            last_error.__class__.__name__ if last_error else "unknown",
        )
        raise HTTPException(status_code=503, detail="Upstream request failed")
