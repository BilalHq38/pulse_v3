"""
shared/usage_guard.py — Plan-based usage limit enforcement.

Conversation usage uses a single canonical ledger type (`conversation_message`) plus
per-event idempotency keys. Reservations use PostgreSQL advisory locks + subscription
FOR UPDATE so concurrent requests cannot bypass monthly_conversation_limit.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import HTTPException, Request

from services.billing_helpers import effective_monthly_conversation_limit
from shared.billing_cache import (
    build_billing_from_db,
    get_cached_billing,
    invalidate_conversation_usage_month_cache,
)
from shared.cache import get_cache_client

logger = logging.getLogger(__name__)

USAGE_TYPE_CONVERSATION = "conversation_message"
CONVERSATION_LIMIT_REACHED_CODE = "CONVERSATION_LIMIT_REACHED"
CONVERSATION_LIMIT_REACHED_MESSAGE = "Conversation limit reached. Please upgrade your package to continue this service."

# Must match uq_usage_ledger_company_conv_idem (partial unique index) for INSERT ... ON CONFLICT.
_CONV_USAGE_ON_CONFLICT_RETURNING = (
    " ON CONFLICT (company_id, usage_idempotency_key) "
    "WHERE usage_type = 'conversation_message' AND BTRIM(usage_idempotency_key) <> '' "
    "DO NOTHING RETURNING id"
)

ReservationResult = Literal["recorded", "duplicate", "denied", "recorded_relaxed"]

InboundGateResult = Literal["allow", "denied", "duplicate"]


def conversation_limit_completed_message(limit: int, used: int | float | None = None) -> str:
    assigned = max(0, int(limit or 0))
    usage = "" if used is None else f" Used: {int(float(used or 0)):,}/{assigned:,}."
    return f"{CONVERSATION_LIMIT_REACHED_MESSAGE}{usage}"


def conversation_limit_reached_detail(limit: int, used: int | float | None = None) -> dict[str, Any]:
    return {
        "code": CONVERSATION_LIMIT_REACHED_CODE,
        "message": conversation_limit_completed_message(limit, used),
        "limit": int(limit or 0),
        "used": int(float(used or 0)),
    }


def _relaxed_billing_env() -> bool:
    for key in ("DEMO_MODE", "STRIPE_OPTIONAL"):
        raw = (os.environ.get(key, "") or "").strip().lower()
        if raw in ("1", "true", "yes", "on", "enabled"):
            return True
    return False


_USAGE_CACHE_NS = "pulse"
_USAGE_TTL: int = max(10, int(os.environ.get("USAGE_CACHE_TTL_SECONDS", "60") or 60))


def _db(request: Request):
    return request.app.state.db


def _auth(request: Request) -> dict[str, Any]:
    return getattr(request.state, "auth_context", {}) or {}


def _company_id(auth: dict[str, Any]) -> str:
    return str(auth.get("company_id") or "").strip()


def _role(auth: dict[str, Any]) -> str:
    return str(auth.get("role") or "").strip().lower()


def month_start_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def _get_plan_limits(company_id: str, db) -> dict[str, Any]:
    billing = await get_cached_billing(company_id)
    if billing is None:
        billing = await build_billing_from_db(db, company_id)
    sub_like = {
        "plan_code": billing.get("plan_code"),
        "monthly_conversation_limit": billing.get("monthly_conversation_limit"),
    }
    conv_cap = effective_monthly_conversation_limit(sub_like)
    return {
        "ai_credit_limit": int(billing.get("ai_credit_limit") or 2500),
        "monthly_conversation_limit": conv_cap,
        "plan_code": str(billing.get("plan_code") or "free"),
    }


async def _sum_conversation_usage_month(conn, company_id: str, month_start: datetime) -> float:
    v = await conn.fetchval(
        "SELECT COALESCE(SUM(usage_units), 0) FROM usage_ledger "
        "WHERE company_id=$1 AND occurred_at >= $2 AND usage_type=$3",
        company_id,
        month_start,
        USAGE_TYPE_CONVERSATION,
    )
    return float(v or 0)


async def advisory_lock_billing_month(conn, company_id: str, month_start: datetime) -> None:
    await conn.fetchval(
        "SELECT pg_advisory_xact_lock(hashtext($1::text), hashtext($2::text))",
        company_id,
        month_start.strftime("%Y-%m"),
    )


async def inbound_conversation_billing_precheck(
    conn,
    company_id: str,
    *,
    idempotency_key: str,
) -> InboundGateResult:
    """
    Under billing month lock: idempotency dedupe + capacity check.
    Does not insert. Caller wraps convo/message inserts after 'allow'.
    """
    ik = (idempotency_key or "").strip()
    if not ik:
        return "denied"

    if _relaxed_billing_env():
        return "allow"

    month_start = month_start_utc()
    await advisory_lock_billing_month(conn, company_id, month_start)

    dup = await conn.fetchval(
        "SELECT 1 FROM usage_ledger WHERE company_id=$1 AND usage_idempotency_key=$2 "
        "AND usage_type=$3 LIMIT 1",
        company_id,
        ik,
        USAGE_TYPE_CONVERSATION,
    )
    if dup:
        return "duplicate"

    sub = await conn.fetchrow(
        "SELECT id, plan_code, monthly_conversation_limit FROM subscriptions WHERE company_id=$1 FOR UPDATE",
        company_id,
    )
    cap = effective_monthly_conversation_limit(dict(sub) if sub else None)
    total = await _sum_conversation_usage_month(conn, company_id, month_start)
    logger.info(
        "plan_limit_check company_id=%s limit_type=conversation used=%.0f limit=%s idempotency_key=%s",
        company_id,
        total,
        cap,
        ik,
    )
    if total >= cap:
        logger.warning(
            "plan_limit_blocked company_id=%s limit_type=conversation used=%.0f limit=%s code=%s idempotency_key=%s",
            company_id,
            total,
            cap,
            CONVERSATION_LIMIT_REACHED_CODE,
            ik,
        )
        return "denied"
    logger.info(
        "plan_limit_allowed company_id=%s limit_type=conversation used=%.0f limit=%s idempotency_key=%s",
        company_id,
        total,
        cap,
        ik,
    )
    return "allow"


async def insert_conversation_usage_row(
    conn,
    company_id: str,
    *,
    channel: str,
    idempotency_key: str,
    units: float = 1.0,
) -> bool:
    """
    Insert ONE conversation_message ledger row.
    Call only after inbound_conversation_billing_precheck returned 'allow' (or in DEMO mode),
    inside the same transaction and with the billing month lock still held.
    Returns True if a row was inserted.
    """
    from core.utils import make_id

    ik = (idempotency_key or "").strip()
    if not ik:
        return False

    subscription_id = await conn.fetchval(
        "SELECT id FROM subscriptions WHERE company_id=$1 LIMIT 1",
        company_id,
    )

    row = await conn.fetchrow(
        "INSERT INTO usage_ledger(id,company_id,subscription_id,usage_type,usage_units,"
        "usage_window,reference_id,metadata,usage_idempotency_key,occurred_at,created_at) "
        "VALUES($1,$2,$3,$4,$5,'monthly','',$6,$7,NOW(),NOW())"
        + _CONV_USAGE_ON_CONFLICT_RETURNING,
        make_id(),
        company_id,
        subscription_id,
        USAGE_TYPE_CONVERSATION,
        float(units),
        json.dumps({"channel": channel}, ensure_ascii=True),
        ik,
    )
    await invalidate_conversation_usage_month_cache(company_id)
    return row is not None


async def insert_conversation_usage_relaxed(conn, company_id: str, *, channel: str, idempotency_key: str, units: float = 1.0) -> ReservationResult:
    """DEMO / STRIPE_OPTIONAL: idempotent insert without cap enforcement."""
    from core.utils import make_id

    ik = (idempotency_key or "").strip()
    if not ik:
        return "denied"
    subscription_id = await conn.fetchval(
        "SELECT id FROM subscriptions WHERE company_id=$1 LIMIT 1",
        company_id,
    )
    row = await conn.fetchrow(
        "INSERT INTO usage_ledger(id,company_id,subscription_id,usage_type,usage_units,"
        "usage_window,reference_id,metadata,usage_idempotency_key,occurred_at,created_at) "
        "VALUES($1,$2,$3,$4,$5,'monthly','',$6,$7,NOW(),NOW())"
        + _CONV_USAGE_ON_CONFLICT_RETURNING,
        make_id(),
        company_id,
        subscription_id,
        USAGE_TYPE_CONVERSATION,
        float(units),
        json.dumps({"channel": channel}, ensure_ascii=True),
        ik,
    )
    await invalidate_conversation_usage_month_cache(company_id)
    return "duplicate" if row is None else "recorded_relaxed"


async def _get_monthly_conversation_usage(company_id: str, db) -> float:
    cache_key = f"usage:{company_id}:conversation:{month_start_utc().strftime('%Y%m')}"
    client = get_cache_client(namespace=_USAGE_CACHE_NS)
    cached = await client.get_json(cache_key)
    if cached is not None:
        return float(cached.get("total", 0))

    month_start = month_start_utc()
    try:
        total = await db.fetchval(
            "SELECT COALESCE(SUM(usage_units), 0) FROM usage_ledger "
            "WHERE company_id=$1 AND occurred_at >= $2 AND usage_type=$3",
            company_id,
            month_start,
            USAGE_TYPE_CONVERSATION,
        )
        total = float(total or 0)
    except Exception as exc:
        logger.warning("usage_guard: conversation usage query failed company_id=%s: %s", company_id, exc)
        return 0.0

    await client.set_json(cache_key, {"total": total}, ttl_seconds=_USAGE_TTL)
    return total


async def _get_monthly_usage_ai(company_id: str, db) -> float:
    cache_key = f"usage:{company_id}:ai:{month_start_utc().strftime('%Y%m')}"
    client = get_cache_client(namespace=_USAGE_CACHE_NS)
    cached = await client.get_json(cache_key)
    if cached is not None:
        return float(cached.get("total", 0))

    month_start = month_start_utc()
    try:
        total = await db.fetchval(
            "SELECT COALESCE(SUM(usage_units), 0) FROM usage_ledger "
            "WHERE company_id=$1 AND usage_type ILIKE $2 AND occurred_at >= $3",
            company_id,
            "ai%",
            month_start,
        )
        total = float(total or 0)
    except Exception as exc:
        logger.warning("usage_guard: usage query failed company_id=%s: %s", company_id, exc)
        return 0.0

    await client.set_json(cache_key, {"total": total}, ttl_seconds=_USAGE_TTL)
    return total


async def check_ai_usage_limit(request: Request) -> None:
    auth = _auth(request)
    company_id = _company_id(auth)
    if _role(auth) == "super_admin" or not company_id:
        return
    if _relaxed_billing_env():
        return

    db = _db(request)
    limits = await _get_plan_limits(company_id, db)
    ai_limit = limits["ai_credit_limit"]

    if limits["plan_code"] == "enterprise":
        return

    current = await _get_monthly_usage_ai(company_id, db)
    if current >= ai_limit:
        logger.warning(
            "usage_guard: AI limit exceeded company_id=%s used=%.0f limit=%d",
            company_id,
            current,
            ai_limit,
        )
        raise HTTPException(
            status_code=429,
            detail=(f"Monthly AI credit limit reached ({int(current):,}/{ai_limit:,}). Upgrade your plan to continue."),
        )


async def check_conversation_limit(request: Request) -> None:
    auth = _auth(request)
    company_id = _company_id(auth)
    if _role(auth) == "super_admin" or not company_id:
        return
    if _relaxed_billing_env():
        return

    db = _db(request)
    limits = await _get_plan_limits(company_id, db)
    msg_limit = limits["monthly_conversation_limit"]
    month_start = month_start_utc()

    async with db.transaction() as conn:
        await advisory_lock_billing_month(conn, company_id, month_start)
        total = await _sum_conversation_usage_month(conn, company_id, month_start)

    if total >= msg_limit:
        logger.warning(
            "plan_limit_blocked company_id=%s limit_type=conversation used=%.0f limit=%d code=%s",
            company_id,
            total,
            msg_limit,
            CONVERSATION_LIMIT_REACHED_CODE,
        )
        raise HTTPException(
            status_code=429,
            detail=conversation_limit_reached_detail(msg_limit, total),
        )
    logger.info(
        "plan_limit_allowed company_id=%s limit_type=conversation used=%.0f limit=%d",
        company_id,
        total,
        msg_limit,
    )


check_message_limit = check_conversation_limit


async def conversation_quota_allows_under_lock(conn, company_id: str) -> bool:
    if not company_id or _relaxed_billing_env():
        return True
    month_start = month_start_utc()
    await advisory_lock_billing_month(conn, company_id, month_start)
    sub = await conn.fetchrow(
        "SELECT plan_code, monthly_conversation_limit FROM subscriptions WHERE company_id=$1 FOR UPDATE",
        company_id,
    )
    cap = effective_monthly_conversation_limit(dict(sub) if sub else None)
    total = await _sum_conversation_usage_month(conn, company_id, month_start)
    logger.info(
        "plan_limit_check company_id=%s limit_type=conversation used=%.0f limit=%s",
        company_id,
        total,
        cap,
    )
    allowed = total < cap
    logger.log(
        logging.INFO if allowed else logging.WARNING,
        "%s company_id=%s limit_type=conversation used=%.0f limit=%s code=%s",
        "plan_limit_allowed" if allowed else "plan_limit_blocked",
        company_id,
        total,
        cap,
        "" if allowed else CONVERSATION_LIMIT_REACHED_CODE,
    )
    return allowed


async def conversation_limit_status_under_lock(conn, company_id: str) -> dict[str, Any]:
    if not company_id or _relaxed_billing_env():
        return {"allowed": True, "used": 0, "limit": 0, "relaxed": True}
    month_start = month_start_utc()
    await advisory_lock_billing_month(conn, company_id, month_start)
    sub = await conn.fetchrow(
        "SELECT plan_code, monthly_conversation_limit FROM subscriptions WHERE company_id=$1 FOR UPDATE",
        company_id,
    )
    cap = effective_monthly_conversation_limit(dict(sub) if sub else None)
    total = await _sum_conversation_usage_month(conn, company_id, month_start)
    logger.info(
        "plan_limit_check company_id=%s limit_type=conversation used=%.0f limit=%s",
        company_id,
        total,
        cap,
    )
    allowed = total < cap
    logger.log(
        logging.INFO if allowed else logging.WARNING,
        "%s company_id=%s limit_type=conversation used=%.0f limit=%s code=%s",
        "plan_limit_allowed" if allowed else "plan_limit_blocked",
        company_id,
        total,
        cap,
        "" if allowed else CONVERSATION_LIMIT_REACHED_CODE,
    )
    return {
        "allowed": allowed,
        "used": int(total),
        "limit": int(cap),
        "relaxed": False,
    }


async def conversation_quota_allows(db, company_id: str) -> bool:
    if not company_id or _relaxed_billing_env():
        return True
    async with db.transaction() as conn:
        return await conversation_quota_allows_under_lock(conn, company_id)


async def conversation_limit_status(db, company_id: str) -> dict[str, Any]:
    if not company_id or _relaxed_billing_env():
        return {"allowed": True, "used": 0, "limit": 0, "relaxed": True}
    async with db.transaction() as conn:
        return await conversation_limit_status_under_lock(conn, company_id)


def raise_conversation_limit_completed(status: dict[str, Any]) -> None:
    raise HTTPException(
        status_code=429,
        detail=conversation_limit_reached_detail(
            int(status.get("limit") or 0),
            status.get("used"),
        ),
    )


async def reserve_conversation_usage(
    db,
    company_id: str,
    *,
    channel: str,
    idempotency_key: str,
    units: float = 1.0,
) -> ReservationResult:
    """Standalone atomic reservation (pool transaction)."""
    if _relaxed_billing_env():
        async with db.transaction() as conn:
            return await insert_conversation_usage_relaxed(conn, company_id, channel=channel, idempotency_key=idempotency_key, units=units)

    async with db.transaction() as conn:
        gate = await inbound_conversation_billing_precheck(conn, company_id, idempotency_key=idempotency_key)
        if gate == "duplicate":
            return "duplicate"
        if gate == "denied":
            return "denied"
        ok = await insert_conversation_usage_row(
            conn,
            company_id,
            channel=channel,
            idempotency_key=idempotency_key,
            units=units,
        )
        return "recorded" if ok else "duplicate"


async def record_ai_usage(db, company_id: str, *, units: float = 1.0, reference_id: str = "") -> None:
    from core.utils import make_id

    try:
        subscription_id = await db.fetchval(
            "SELECT id FROM subscriptions WHERE company_id=$1 LIMIT 1",
            company_id,
        )
        await db.execute(
            "INSERT INTO usage_ledger(id,company_id,subscription_id,usage_type,usage_units,"
            "usage_window,reference_id,metadata,usage_idempotency_key,occurred_at,created_at) "
            "VALUES($1,$2,$3,'ai_tokens',$4,'monthly',$5,$6,'',NOW(),NOW())",
            make_id(),
            company_id,
            subscription_id,
            float(units),
            reference_id or "",
            json.dumps({}, ensure_ascii=True),
        )
        month = datetime.now(timezone.utc).strftime("%Y%m")
        client = get_cache_client(namespace=_USAGE_CACHE_NS)
        await client.delete(f"usage:{company_id}:ai:{month}")
    except Exception as exc:
        logger.warning("record_ai_usage failed company_id=%s: %s", company_id, exc)


async def record_message_usage(db, company_id: str, *, channel: str = "whatsapp", units: float = 1.0) -> None:
    from core.utils import make_id

    await reserve_conversation_usage(
        db,
        company_id,
        channel=channel,
        idempotency_key=f"legacy:{company_id}:{make_id()}",
        units=units,
    )


async def invalidate_conversation_usage_cache(company_id: str) -> None:
    await invalidate_conversation_usage_month_cache(company_id)
