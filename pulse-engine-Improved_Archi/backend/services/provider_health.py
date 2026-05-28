from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)


@dataclass
class ProviderHealthState:
    provider: str
    channel: str
    company_id: str
    scope: str
    status: str = "healthy"
    consecutive_failures: int = 0
    last_error: str = ""
    last_success_at: float = 0.0
    last_failure_at: float = 0.0
    throttle_until: float = 0.0


_STATE: dict[str, ProviderHealthState] = {}
_LOCK = threading.Lock()


def _normalize(value: str, fallback: str = "") -> str:
    return str(value or fallback).strip().lower().replace("_", "-")


def _key(provider: str, channel: str, company_id: str = "", scope: str = "") -> str:
    return "|".join(
        (
            _normalize(provider, "provider"),
            _normalize(channel, "channel"),
            str(company_id or "").strip(),
            str(scope or "").strip(),
        )
    )


def _degraded_status(failure_count: int, *, rate_limited: bool = False) -> str:
    if rate_limited:
        return "rate_limited"
    if failure_count >= 3:
        return "degraded"
    return "warning"


def _throttle_seconds(provider: str, failure_count: int, *, rate_limited: bool = False) -> float:
    normalized_provider = _normalize(provider, "provider")
    if failure_count < 3 and not rate_limited:
        return 0.0
    if normalized_provider == "qr":
        return min(30.0, float(2 ** min(failure_count, 5)))
    if rate_limited:
        return min(20.0, float(2 ** min(failure_count, 4)))
    return 0.0


def record_provider_success(
    provider: str,
    *,
    channel: str,
    company_id: str = "",
    scope: str = "",
    operation: str = "",
) -> ProviderHealthState:
    key = _key(provider, channel, company_id, scope)
    now = time.time()
    with _LOCK:
        state = _STATE.get(
            key,
            ProviderHealthState(
                provider=_normalize(provider, "provider"),
                channel=_normalize(channel, "channel"),
                company_id=str(company_id or "").strip(),
                scope=str(scope or "").strip(),
            ),
        )
        was_degraded = state.status != "healthy"
        state.status = "healthy"
        state.consecutive_failures = 0
        state.last_error = ""
        state.last_success_at = now
        state.throttle_until = 0.0
        _STATE[key] = state
    if was_degraded:
        logger.info(
            "provider_health_recovered provider=%s channel=%s company_id=%s scope=%s operation=%s",
            state.provider,
            state.channel,
            state.company_id,
            state.scope,
            operation,
        )
    return state


def record_provider_failure(
    provider: str,
    *,
    channel: str,
    company_id: str = "",
    scope: str = "",
    operation: str = "",
    error: str = "",
    rate_limited: bool = False,
) -> ProviderHealthState:
    key = _key(provider, channel, company_id, scope)
    now = time.time()
    with _LOCK:
        state = _STATE.get(
            key,
            ProviderHealthState(
                provider=_normalize(provider, "provider"),
                channel=_normalize(channel, "channel"),
                company_id=str(company_id or "").strip(),
                scope=str(scope or "").strip(),
            ),
        )
        state.consecutive_failures += 1
        state.last_error = str(error or "")[:500]
        state.last_failure_at = now
        state.status = _degraded_status(state.consecutive_failures, rate_limited=rate_limited)
        delay = _throttle_seconds(state.provider, state.consecutive_failures, rate_limited=rate_limited)
        if delay > 0:
            state.throttle_until = max(state.throttle_until, now + delay)
        _STATE[key] = state
    if state.consecutive_failures == 1 or state.status in {"degraded", "rate_limited"}:
        logger.warning(
            "provider_health_degraded provider=%s channel=%s company_id=%s scope=%s operation=%s status=%s failures=%s throttle_seconds=%.1f error=%s",
            state.provider,
            state.channel,
            state.company_id,
            state.scope,
            operation,
            state.status,
            state.consecutive_failures,
            max(state.throttle_until - now, 0.0),
            state.last_error,
        )
    return state


def provider_is_throttled(
    provider: str,
    *,
    channel: str,
    company_id: str = "",
    scope: str = "",
) -> tuple[bool, ProviderHealthState | None]:
    key = _key(provider, channel, company_id, scope)
    now = time.time()
    with _LOCK:
        state = _STATE.get(key)
        if state is None:
            return False, None
        if state.throttle_until and state.throttle_until <= now:
            state.throttle_until = 0.0
            if state.status == "degraded" and state.consecutive_failures < 5:
                state.status = "warning"
        return bool(state.throttle_until and state.throttle_until > now), state


def provider_health_snapshot() -> list[dict]:
    with _LOCK:
        return [asdict(state) for state in _STATE.values()]

