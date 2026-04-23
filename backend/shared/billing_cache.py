"""
shared/billing_cache.py — Redis-backed tenant billing status cache.

Key:   billing:{tenant_id}
Value: {is_active, subscription_status, payment_status, plan_code,
        cached_at, grace_until, current_period_end_ts}
TTL:   60 s (configurable via BILLING_CACHE_TTL_SECONDS env var)

All services read from this cache before querying the DB.
The gateway checks it before proxying (no DB required at gateway level).
Invalidated explicitly on every billing mutation, tenant disable, or
Stripe webhook event.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from shared.cache import get_cache_client

logger = logging.getLogger(__name__)

_TTL: int = max(10, int(os.environ.get("BILLING_CACHE_TTL_SECONDS", "60") or 60))
_CACHE_NS = "pulse"
_KEY_PREFIX = "billing"
_BLACKLIST_NS = "pulse"
_BLACKLIST_PREFIX = "token_blacklist"
_BLACKLIST_TTL: int = max(300, int(os.environ.get("TOKEN_BLACKLIST_TTL_SECONDS", "86400") or 86400))


def _billing_key(tenant_id: str) -> str:
    return f"{_KEY_PREFIX}:{str(tenant_id or '').strip()}"


def _blacklist_key(token_jti: str) -> str:
    return f"{_BLACKLIST_PREFIX}:{str(token_jti or '').strip()}"


# ── Billing cache ────────────────────────────────────────────────────────────


async def get_cached_billing(tenant_id: str) -> dict[str, Any] | None:
    """
    Return cached billing record for the tenant, or None on cache miss.
    Gracefully degrades to None if Redis is unavailable.
    """
    if not tenant_id:
        return None
    client = get_cache_client(namespace=_CACHE_NS)
    try:
        return await client.get_json(_billing_key(tenant_id))
    except Exception as exc:
        logger.warning("billing_cache get failed tenant=%s: %s", tenant_id, exc)
        return None


async def set_cached_billing(tenant_id: str, payload: dict[str, Any]) -> None:
    """
    Store a billing record in the cache.  Always include cached_at so consumers
    can reason about freshness.
    """
    if not tenant_id:
        return
    client = get_cache_client(namespace=_CACHE_NS)
    try:
        data = {**payload, "cached_at": time.time()}
        await client.set_json(_billing_key(tenant_id), data, ttl_seconds=_TTL)
    except Exception as exc:
        logger.warning("billing_cache set failed tenant=%s: %s", tenant_id, exc)


async def invalidate_conversation_usage_month_cache(tenant_id: str) -> None:
    """Invalidate Redis monthly conversation total cache for UTC calendar month."""
    if not tenant_id:
        return
    month = datetime.now(timezone.utc).strftime("%Y%m")
    client = get_cache_client(namespace=_CACHE_NS)
    try:
        await client.delete(f"usage:{tenant_id}:conversation:{month}")
    except Exception as exc:
        logger.warning("conversation usage cache invalidate failed tenant=%s: %s", tenant_id, exc)


async def invalidate_billing_cache(tenant_id: str) -> None:
    """
    Remove the billing cache entry for a tenant.  Call this after any billing
    mutation (override, webhook, tenant disable) so the next request re-reads
    from DB and re-populates the cache.
    Also clears the conversation usage rollup cache so limits reflect plan changes immediately.
    """
    if not tenant_id:
        return
    client = get_cache_client(namespace=_CACHE_NS)
    try:
        await client.delete(_billing_key(tenant_id))
        logger.debug("billing_cache invalidated tenant=%s", tenant_id)
    except Exception as exc:
        logger.warning("billing_cache invalidate failed tenant=%s: %s", tenant_id, exc)
    await invalidate_conversation_usage_month_cache(tenant_id)


async def build_billing_from_db(db, company_id: str) -> dict[str, Any]:
    """
    Query DB for company + subscription + billing_customer status, store in
    cache, and return the combined record.  Called on every cache miss.
    """
    import asyncio

    company_row, sub_row, bc_row = await asyncio.gather(
        db.fetchrow(
            "SELECT id, is_active FROM companies WHERE id=$1 LIMIT 1",
            company_id,
        ),
        db.fetchrow(
            "SELECT plan_code, status, current_period_end, ai_credit_limit, monthly_conversation_limit, max_users "
            "FROM subscriptions WHERE company_id=$1 LIMIT 1",
            company_id,
        ),
        db.fetchrow(
            "SELECT payment_status FROM billing_customers WHERE company_id=$1 LIMIT 1",
            company_id,
        ),
    )

    is_active = bool((company_row or {}).get("is_active", True))
    plan_code = str((sub_row or {}).get("plan_code") or "free").strip().lower()
    sub_status = str((sub_row or {}).get("status") or "active").strip().lower()
    payment_status = str((bc_row or {}).get("payment_status") or "inactive").strip().lower()
    ai_credit_limit = int((sub_row or {}).get("ai_credit_limit") or 2500)
    monthly_conversation_limit = int((sub_row or {}).get("monthly_conversation_limit") or 0)
    max_users = int((sub_row or {}).get("max_users") or 0)

    period_end = (sub_row or {}).get("current_period_end")
    grace_until: float | None = None
    current_period_end_ts: float | None = None
    if period_end:
        import calendar

        try:
            ts = calendar.timegm(period_end.timetuple())
            current_period_end_ts = float(ts)
            grace_until = ts + (3 * 24 * 3600)  # 3-day grace period
        except Exception:
            pass

    record: dict[str, Any] = {
        "tenant_id": company_id,
        "is_active": is_active,
        "subscription_status": sub_status,
        "payment_status": payment_status,
        "plan_code": plan_code,
        "ai_credit_limit": ai_credit_limit,
        "monthly_conversation_limit": monthly_conversation_limit,
        "max_users": max_users,
        "grace_until": grace_until,
        "current_period_end_ts": current_period_end_ts,
    }
    await set_cached_billing(company_id, record)
    return record


# ── Token blacklist ──────────────────────────────────────────────────────────


async def blacklist_token(jti: str, *, reason: str = "") -> None:
    """
    Blacklist a JWT by its jti claim.  Used when a tenant is disabled or
    billing fails so active sessions are invalidated immediately.
    """
    if not jti:
        return
    client = get_cache_client(namespace=_BLACKLIST_NS)
    try:
        await client.set_json(
            _blacklist_key(jti),
            {"jti": jti, "reason": reason, "blacklisted_at": time.time()},
            ttl_seconds=_BLACKLIST_TTL,
        )
    except Exception as exc:
        logger.warning("token blacklist set failed jti=%s: %s", jti, exc)


async def is_token_blacklisted(jti: str) -> bool:
    """Return True if the token has been blacklisted."""
    if not jti:
        return False
    client = get_cache_client(namespace=_BLACKLIST_NS)
    try:
        entry = await client.get_json(_blacklist_key(jti))
        return entry is not None
    except Exception:
        return False


async def blacklist_all_tenant_sessions(db, company_id: str, *, reason: str = "") -> None:
    """
    Blacklist all active session tokens for a tenant by fetching their JTIs
    from the sessions table and adding each to the Redis blacklist.
    """
    try:
        rows = await db.fetch(
            "SELECT token_hash FROM sessions WHERE company_id=$1 AND revoked_at IS NULL",
            company_id,
        )
        for row in rows:
            jti = str(row["token_hash"] or "").strip()
            if jti:
                await blacklist_token(jti, reason=reason)
        await db.execute(
            "UPDATE sessions SET revoked_at=NOW() WHERE company_id=$1 AND revoked_at IS NULL",
            company_id,
        )
        logger.info(
            "billing_cache: blacklisted %d sessions for company_id=%s reason=%s",
            len(rows),
            company_id,
            reason,
        )
    except Exception as exc:
        logger.warning(
            "billing_cache: session blacklist failed company_id=%s: %s",
            company_id,
            exc,
        )
