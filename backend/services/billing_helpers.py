from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Optional

from fastapi import HTTPException

from core.utils import make_id
from services.db_helpers import r, runtime_schema_ready

logger = logging.getLogger(__name__)

USER_LIMIT_REACHED_CODE = "USER_LIMIT_REACHED"
TEAM_MEMBER_LIMIT_REACHED_MESSAGE = "User limit reached. Please upgrade your package to add more users."


def user_limit_reached_detail(*, used: int | None = None, limit: int | None = None) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "code": USER_LIMIT_REACHED_CODE,
        "message": TEAM_MEMBER_LIMIT_REACHED_MESSAGE,
    }
    if used is not None:
        detail["used"] = int(used)
    if limit is not None:
        detail["limit"] = int(limit)
    return detail


async def ensure_subscriptions_limit_columns(db) -> None:
    """
    Databases created before sql_migrations/001_subscription_conversation_and_seats.sql
    may be missing conversation/seat columns on subscriptions. Runtime DDL is disabled;
    deployment must apply the migration before startup.
    """
    if getattr(ensure_subscriptions_limit_columns, "_done", False):
        return
    await runtime_schema_ready(
        db,
        "subscription_limits",
        required_columns=(
            ("subscriptions", "monthly_conversation_limit"),
            ("subscriptions", "max_users"),
        ),
        raise_on_missing=True,
    )
    ensure_subscriptions_limit_columns._done = True  # type: ignore[attr-defined]


def trial_period_days() -> int:
    """Length of self-serve trial periods (default 30 days)."""
    return max(1, int(os.environ.get("TRIAL_PERIOD_DAYS", "30") or 30))


try:
    import stripe
except Exception:  # pragma: no cover
    stripe = None


_PLACEHOLDER_PREFIXES = (
    "replace-with-",
    "changeme",
    "change-me",
    "your-",
)

STRIPE_SECRET_KEY = (os.environ.get("STRIPE_SECRET_KEY", "") or "").strip()
STRIPE_WEBHOOK_SECRET = (os.environ.get("STRIPE_WEBHOOK_SECRET", "") or "").strip()

if stripe and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


PLAN_CATALOG = {
    "free": {
        "code": "free",
        "name": "Free",
        "monthly_price_cents": 0,
        "ai_credit_limit": 2500,
        "monthly_conversation_limit": 250,
        "max_users": 1,
        "stripe_price_env": "",
    },
    "pro": {
        "code": "pro",
        "name": "Pro",
        "monthly_price_cents": 2900,
        "ai_credit_limit": 50000,
        "monthly_conversation_limit": 2500,
        "max_users": 1,
        "stripe_price_env": "STRIPE_PRICE_PRO",
    },
    "enterprise": {
        "code": "enterprise",
        "name": "Enterprise",
        "monthly_price_cents": 9900,
        "ai_credit_limit": 250000,
        "monthly_conversation_limit": 10000,
        "max_users": 3,
        "stripe_price_env": "STRIPE_PRICE_ENTERPRISE",
    },
}


def _looks_placeholder(value: str) -> bool:
    lowered = (value or "").strip().lower()
    if not lowered:
        return True
    return any(lowered.startswith(prefix) for prefix in _PLACEHOLDER_PREFIXES)


def stripe_configured() -> bool:
    return bool(stripe) and not _looks_placeholder(STRIPE_SECRET_KEY)


def relaxed_billing_env() -> bool:
    """
    DEMO_MODE or STRIPE_OPTIONAL: Stripe is not treated as a hard dependency for
    auth, subscription gates, or gateway pre-flight (local / CI / demo stacks).
    """
    for key in ("DEMO_MODE", "STRIPE_OPTIONAL"):
        raw = (os.environ.get(key, "") or "").strip().lower()
        if raw in ("1", "true", "yes", "on", "enabled"):
            return True
    return False


def is_demo_mode() -> bool:
    return (os.environ.get("DEMO_MODE", "") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    )


def stripe_webhook_configured() -> bool:
    return stripe_configured() and not _looks_placeholder(STRIPE_WEBHOOK_SECRET)


def stripe_enabled_for_app() -> bool:
    """
    Whether this deployment should attempt live Stripe API calls.

    STRIPE_ENABLED:
      - auto (default): on only when STRIPE_SECRET_KEY looks configured (demo-friendly).
      - true / 1 / on / yes / enabled: insist on Stripe (missing keys -> errors at API boundaries).
      - false / 0 / no / off / disabled: demo / offline; auth and onboarding never depend on Stripe.

    When DEMO_MODE or STRIPE_OPTIONAL is enabled, this always returns False so Stripe
    is never required for app flows (checkout still returns 422 for paid plans without keys).
    """
    if relaxed_billing_env():
        return False
    raw = (os.environ.get("STRIPE_ENABLED", "auto") or "auto").strip().lower()
    if raw in ("false", "0", "no", "off", "disabled"):
        return False
    if raw in ("true", "1", "yes", "on", "enabled"):
        return True
    return stripe_configured()


def assert_stripe_ready() -> None:
    """Call immediately before any Stripe SDK usage. Raises if Stripe cannot run."""
    if relaxed_billing_env():
        # Safety net: signup/login paths must never hard-fail Stripe in DEMO_MODE / STRIPE_OPTIONAL.
        return
    if not stripe:
        raise HTTPException(501, "Stripe SDK is not installed")
    if _looks_placeholder(STRIPE_SECRET_KEY):
        raise HTTPException(503, "Stripe billing is not configured")


def require_stripe() -> None:
    """
    Soft gate: when Stripe is disabled for the app (STRIPE_ENABLED off or auto without keys),
    returns without raising so auth-adjacent imports never hard-fail. Billing routes must still
    call assert_stripe_ready() or check stripe_configured() before touching the SDK.
    """
    if not stripe_enabled_for_app():
        return
    assert_stripe_ready()


def require_stripe_webhook_secret() -> str:
    if relaxed_billing_env():
        raise HTTPException(503, "Stripe webhooks are disabled in DEMO_MODE / STRIPE_OPTIONAL")
    if not stripe_enabled_for_app():
        raise HTTPException(503, "Stripe webhooks are disabled (STRIPE_ENABLED off or auto without keys)")
    assert_stripe_ready()
    if _looks_placeholder(STRIPE_WEBHOOK_SECRET):
        raise HTTPException(503, "STRIPE_WEBHOOK_SECRET is not configured")
    return STRIPE_WEBHOOK_SECRET


def get_plan(plan_code: str) -> dict[str, Any]:
    plan = PLAN_CATALOG.get((plan_code or "").strip().lower())
    if not plan:
        raise HTTPException(400, "Invalid plan")
    return dict(plan)


def effective_monthly_conversation_limit(sub_row: dict[str, Any] | None) -> int:
    """Resolve stored override or plan-catalog default (monthly messages across all channels)."""
    code = str((sub_row or {}).get("plan_code") or "free").strip().lower()
    plan = PLAN_CATALOG.get(code, PLAN_CATALOG["free"])
    stored = int((sub_row or {}).get("monthly_conversation_limit") or 0)
    if stored > 0:
        return stored
    return int(plan.get("monthly_conversation_limit") or 250)


def effective_max_users(sub_row: dict[str, Any] | None) -> int:
    code = str((sub_row or {}).get("plan_code") or "free").strip().lower()
    plan = PLAN_CATALOG.get(code, PLAN_CATALOG["free"])
    stored = int((sub_row or {}).get("max_users") or 0)
    if stored > 0:
        return stored
    return int(plan.get("max_users") or 1)


async def count_workspace_seats_used(db, company_id: str) -> int:
    return int(
        await db.fetchval(
            "SELECT COUNT(*) FROM users WHERE company_id=$1 AND status='active' "
            "AND role IN ('admin', 'company_agent')",
            company_id,
        )
        or 0
    )


async def count_pending_invitations(db, company_id: str) -> int:
    return int(
        await db.fetchval(
            "SELECT COUNT(*) FROM invitations WHERE company_id=$1 AND status='pending'",
            company_id,
        )
        or 0
    )


async def assert_workspace_seat_available(db, company_id: str) -> None:
    """Raise HTTPException when a new user/invite would exceed max_users for the workspace."""
    await db.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"workspace-seats:{company_id}")
    sub = await db.fetchrow("SELECT plan_code, max_users FROM subscriptions WHERE company_id=$1 FOR UPDATE", company_id)
    cap = effective_max_users(dict(sub) if sub else None)
    used = await count_workspace_seats_used(db, company_id)
    pending = await count_pending_invitations(db, company_id)
    logger.info(
        "plan_limit_check company_id=%s limit_type=user used=%s pending=%s limit=%s",
        company_id,
        used,
        pending,
        cap,
    )
    if used + pending >= cap:
        logger.warning(
            "plan_limit_blocked company_id=%s limit_type=user used=%s pending=%s limit=%s code=%s",
            company_id,
            used,
            pending,
            cap,
            USER_LIMIT_REACHED_CODE,
        )
        raise HTTPException(
            status_code=403,
            detail=user_limit_reached_detail(used=used + pending, limit=cap),
        )
    logger.info(
        "plan_limit_allowed company_id=%s limit_type=user used=%s pending=%s limit=%s",
        company_id,
        used,
        pending,
        cap,
    )


def get_public_signup_plans() -> list[dict[str, Any]]:
    return [dict(plan) for code, plan in PLAN_CATALOG.items() if code != "free"]


def stripe_price_id(plan_code: str) -> str:
    plan = get_plan(plan_code)
    env_name = str(plan.get("stripe_price_env") or "").strip()
    if not env_name:
        return ""
    return (os.environ.get(env_name, "") or "").strip()


def uses_local_billing_customer_id(stripe_customer_id: str) -> bool:
    return (stripe_customer_id or "").strip().startswith("cus_local_")


def normalize_plan_code_from_lookup_key(value: str, default: str = "pro") -> str:
    candidate = (value or "").strip().lower()
    if candidate in PLAN_CATALOG:
        return candidate
    return default


async def get_or_create_billing_customer(
    db,
    company_id: str,
    *,
    billing_email: str,
    billing_name: str,
    payment_status: str = "inactive",
) -> dict[str, Any]:
    row = r(
        await db.fetchrow(
            "SELECT * FROM billing_customers WHERE company_id=$1 LIMIT 1",
            company_id,
        )
    )
    if row:
        return row
    # Some DBs still enforce NOT NULL on stripe_customer_id; local/demo uses an internal id (see upsert_subscription).
    hexco = (company_id or "").replace("-", "")
    local_cus = f"cus_local_{hexco[:32]}"
    customer = {
        "id": make_id(),
        "company_id": company_id,
        "stripe_customer_id": local_cus,
        "billing_email": (billing_email or "").strip().lower(),
        "billing_name": (billing_name or "").strip(),
        "payment_status": payment_status,
    }
    await db.execute(
        "INSERT INTO billing_customers(id,company_id,stripe_customer_id,billing_email,billing_name,payment_status,created_at,updated_at) "  # noqa: E501
        "VALUES($1,$2,$3,$4,$5,$6,NOW(),NOW())",
        customer["id"],
        company_id,
        customer["stripe_customer_id"],
        customer["billing_email"],
        customer["billing_name"],
        customer["payment_status"],
    )
    return customer


async def update_billing_customer_status(
    db,
    *,
    company_id: str,
    stripe_customer_id: str = "",
    payment_status: str,
    billing_email: str = "",
    billing_name: str = "",
) -> dict[str, Any]:
    billing_customer = await get_or_create_billing_customer(
        db,
        company_id,
        billing_email=billing_email,
        billing_name=billing_name,
        payment_status=payment_status,
    )
    normalized_stripe_customer_id = (
        (stripe_customer_id or billing_customer.get("stripe_customer_id") or "").strip() or None
    )
    if not normalized_stripe_customer_id:
        normalized_stripe_customer_id = f"cus_local_{(company_id or '').replace('-', '')[:32]}"
    await db.execute(
        "UPDATE billing_customers SET stripe_customer_id=$1,billing_email=$2,billing_name=$3,payment_status=$4,updated_at=NOW() WHERE id=$5",  # noqa: E501
        normalized_stripe_customer_id,
        (billing_email or billing_customer.get("billing_email", "")).strip().lower(),
        (billing_name or billing_customer.get("billing_name", "")).strip(),
        payment_status,
        billing_customer["id"],
    )
    return r(
        await db.fetchrow(
            "SELECT * FROM billing_customers WHERE id=$1",
            billing_customer["id"],
        )
    )


async def upsert_subscription(
    db,
    company_id: str,
    *,
    billing_customer_id: str | None = None,
    plan_code: str,
    status: str,
    stripe_subscription_id: str = "",
    billing_interval: str = "month",
    current_period_start: Optional[datetime] = None,
    current_period_end: Optional[datetime] = None,
    canceled_at: Optional[datetime] = None,
) -> dict[str, Any]:
    await ensure_subscriptions_limit_columns(db)
    plan = get_plan(plan_code)
    normalized_stripe_subscription_id = (stripe_subscription_id or "").strip() or None
    # DB column is NOT NULL; non-Stripe (demo / local trial) uses an internal id.
    if not normalized_stripe_subscription_id:
        normalized_stripe_subscription_id = f"sub_local_{company_id.replace('-', '')[:16]}"
    existing = await db.fetchval(
        "SELECT id FROM subscriptions WHERE company_id=$1 LIMIT 1",
        company_id,
    )
    if existing:
        await db.execute(
            "UPDATE subscriptions SET billing_customer_id=$1, stripe_subscription_id=$2, plan_code=$3, status=$4, "
            "billing_interval=$5, ai_credit_limit=$6, monthly_conversation_limit=$7, max_users=$8, "
            "current_period_start=$9, current_period_end=$10, canceled_at=$11, updated_at=NOW() "
            "WHERE id=$12 AND company_id=$13",
            billing_customer_id,
            normalized_stripe_subscription_id,
            plan["code"],
            status,
            billing_interval,
            plan["ai_credit_limit"],
            plan["monthly_conversation_limit"],
            plan["max_users"],
            current_period_start,
            current_period_end,
            canceled_at,
            existing,
            company_id,
        )
        return r(await db.fetchrow("SELECT * FROM subscriptions WHERE id=$1", existing))
    subscription_id = make_id()
    await db.execute(
        "INSERT INTO subscriptions(id,company_id,billing_customer_id,stripe_subscription_id,plan_code,status,billing_interval,"  # noqa: E501
        "ai_credit_limit,monthly_conversation_limit,max_users,current_period_start,current_period_end,canceled_at,created_at,updated_at) "  # noqa: E501
        "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,NOW(),NOW())",
        subscription_id,
        company_id,
        billing_customer_id,
        normalized_stripe_subscription_id,
        plan["code"],
        status,
        billing_interval,
        plan["ai_credit_limit"],
        plan["monthly_conversation_limit"],
        plan["max_users"],
        current_period_start,
        current_period_end,
        canceled_at,
    )
    return r(await db.fetchrow("SELECT * FROM subscriptions WHERE id=$1", subscription_id))
