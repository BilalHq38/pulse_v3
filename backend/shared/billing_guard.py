"""
shared/billing_guard.py — Subscription & tenant enforcement dependencies.

Centralised billing enforcement used by:
  • shared/app_factory.py middleware (all microservices, zero-config)
  • api_gateway/app.py pre-routing check (cache-only, no DB)
  • Individual route Depends() for granular overrides

Hierarchy:
  1. Token blacklist check   → 401 if token revoked
  2. Tenant enabled check    → 403 if companies.is_active = false
  3. Subscription check      → 402 if canceled/unpaid/past_due (with grace period)
  4. Payment health check    → 402 if invoice payment_status = failed

Cache-first: reads billing:{tenant_id} from Redis (60 s TTL), falls back to
DB on miss, writes back to cache.  Gateway uses cache-only path (no DB).

Usage:
    from shared.billing_guard import require_active_subscription, check_tenant_enabled

    # Applied globally via app_factory middleware — no need to add Depends per route.
    # For explicit per-route enforcement:
    @router.post("/ai/generate")
    async def generate(request: Request, _: None = Depends(require_active_subscription)):
        ...
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from fastapi import HTTPException, Request

from shared.billing_cache import (
    build_billing_from_db,
    get_cached_billing,
    is_token_blacklisted,
)

logger = logging.getLogger(__name__)


def _offline_trial_period_enforced() -> bool:
    """True when Stripe checkout is off for the app but demo-relaxed mode is off (trial end must be enforced)."""
    try:
        from services.billing_helpers import relaxed_billing_env as _relaxed, stripe_enabled_for_app as _stripe_on

        return not _relaxed() and not _stripe_on()
    except Exception:
        return False


def _relaxed_billing_env() -> bool:
    """Matches services.billing_helpers.relaxed_billing_env (no services import from shared/)."""
    for key in ("DEMO_MODE", "STRIPE_OPTIONAL"):
        raw = (os.environ.get(key, "") or "").strip().lower()
        if raw in ("1", "true", "yes", "on", "enabled"):
            return True
    return False


# Subscription statuses that block paid-feature access.
_BLOCKED_STATUSES = {"canceled", "unpaid", "past_due", "incomplete"}
# Subscription statuses that mean the subscription is healthy.
_ACTIVE_STATUSES = {"active", "trialing"}
_ACCOUNT_STATUS_MESSAGES = {
    "pending_approval": (
        "Your account is pending admin approval. You will get access to Pulse Engine after Super Admin verification."
    ),
    "rejected": "Your account request was not approved. Please contact the administrator for more information.",
    "blocked": "Your account has been blocked. Please contact your administrator to restore access.",
    "paused": "Your account has been paused. Please contact your administrator.",
    "inactive": "Your account is inactive. Please contact your administrator.",
}
_ACCOUNT_STATUS_CODES = {
    "pending_approval": "ACCOUNT_PENDING_APPROVAL",
    "rejected": "ACCOUNT_REJECTED",
    "blocked": "ACCOUNT_BLOCKED",
    "paused": "ACCOUNT_PAUSED",
    "inactive": "ACCOUNT_INACTIVE",
}
_ACCOUNT_RESTRICTED_STATUSES = set(_ACCOUNT_STATUS_MESSAGES)
_PENDING_APPROVAL_ALLOWED_PREFIXES = (
    "/api/auth/onboarding",
    "/api/auth/billing/plan",
    "/api/auth/session",
    "/api/auth/logout",
    "/api/settings/company",
)
_REJECTED_ALLOWED_PREFIXES = (
    "/api/auth/session",
    "/api/auth/logout",
)
_BLOCKED_ALLOWED_PREFIXES = ("/api/auth/logout",)

# Paths that bypass all billing checks (auth + billing management + health).
_EXEMPT_PATH_PREFIXES = (
    "/api/auth",
    "/api/account",
    "/api/billing/webhooks",
    "/api/billing/checkout-session",
    "/api/billing/customer-portal",
    "/api/billing/plans",
    "/api/billing/subscription",
    "/api/visitor/track",
    "/api/admin",
    "/api/platform/super-admin",
    "/health",
    "/ready",
    "/metrics",
    "/api/healthz",
    "/api/metrics",
)

# Narrower than _EXEMPT_PATH_PREFIXES: authenticated but not yet email-verified users may only hit these.
_EMAIL_VERIFICATION_EXEMPT_PREFIXES = (
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/register/status",
    "/api/auth/signup-billing-info",
    "/api/auth/logout",
    "/api/auth/refresh",
    "/api/auth/session",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
    "/api/auth/verify-email",
    "/api/auth/resend-verification",
    "/api/auth/reset-tokens",
    "/api/auth/google",
    "/api/auth/facebook",
    "/api/auth/invitations/accept",
    "/api/account",
    "/api/billing/webhooks",
    "/api/billing/checkout-session",
    "/api/billing/customer-portal",
    "/api/billing/plans",
    "/api/billing/subscription",
    "/api/visitor/track",
    "/api/admin",
    "/api/platform/super-admin",
    "/health",
    "/ready",
    "/metrics",
    "/api/healthz",
    "/api/metrics",
)

_ENTERPRISE_INVITE_EXEMPT_PREFIXES = (
    "/api/auth",
    "/api/account",
    "/api/billing/webhooks",
    "/api/billing/checkout-session",
    "/api/billing/customer-portal",
    "/api/billing/plans",
    "/api/billing/subscription",
    "/api/visitor/track",
    "/api/settings/company",
    "/api/settings/personal",
    "/api/admin",
    "/api/platform/super-admin",
    "/health",
    "/ready",
    "/metrics",
    "/api/healthz",
    "/api/metrics",
)


def _is_exempt(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in _EXEMPT_PATH_PREFIXES)


def _is_email_verification_exempt(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in _EMAIL_VERIFICATION_EXEMPT_PREFIXES)


def _is_enterprise_invite_exempt(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in _ENTERPRISE_INVITE_EXEMPT_PREFIXES)


def _path_matches(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix) for prefix in prefixes)


def _account_status_detail(status: str) -> dict[str, Any]:
    return {
        "code": _ACCOUNT_STATUS_CODES.get(status, "ACCOUNT_RESTRICTED"),
        "status": status,
        "message": _ACCOUNT_STATUS_MESSAGES.get(status, "Your account cannot access this resource."),
    }


def _get_auth_context(request: Request) -> dict[str, Any]:
    return getattr(request.state, "auth_context", {}) or {}


def _company_id(auth: dict[str, Any]) -> str:
    return str(auth.get("company_id") or "").strip()


def _role(auth: dict[str, Any]) -> str:
    return str(auth.get("role") or "").strip().lower()


def _user_id(auth: dict[str, Any]) -> str:
    return str(auth.get("sub") or auth.get("user_id") or "").strip()


def _jti(auth: dict[str, Any]) -> str:
    return str(auth.get("jti") or auth.get("token_jti") or "").strip()


async def _get_billing(company_id: str, db) -> dict[str, Any]:
    """Cache-first billing lookup with DB fallback."""
    cached = await get_cached_billing(company_id)
    if cached is not None:
        return cached
    try:
        return await build_billing_from_db(db, company_id)
    except Exception as exc:
        logger.warning("billing_guard: DB fallback failed company_id=%s: %s", company_id, exc)
        return {}


def _db(request: Request):
    return request.app.state.db


# ── Token blacklist check ────────────────────────────────────────────────────


def _claim_int(auth: dict[str, Any], key: str, default: int = 1) -> int:
    try:
        return int(auth.get(key, default))
    except (TypeError, ValueError):
        return default


async def check_email_verified(request: Request) -> None:
    if _relaxed_billing_env():
        return
    auth = _get_auth_context(request)
    if _role(auth) == "super_admin":
        return
    if not _company_id(auth):
        return
    if _claim_int(auth, "ev", 1) == 1:
        return
    raise HTTPException(status_code=403, detail="EMAIL_VERIFICATION_REQUIRED")


async def check_enterprise_invite_gate(request: Request) -> None:
    if _relaxed_billing_env():
        return
    auth = _get_auth_context(request)
    if _role(auth) == "super_admin":
        return
    if not _company_id(auth):
        return
    if _claim_int(auth, "ei", 1) == 1:
        return
    raise HTTPException(status_code=403, detail="ENTERPRISE_INVITE_REQUIRED")


async def check_user_status_active(request: Request) -> None:
    """
    Enforce live Super Admin pause/block/inactive controls. This deliberately
    reads the DB instead of Redis so status changes take effect immediately.
    """
    auth = _get_auth_context(request)
    # Internal service-to-service calls use a synthetic user ID ("ai-service-internal"
    # etc.) that does not exist in the users table. Skip the user status check entirely
    # for these calls — the service secret already authenticates them.
    if auth.get("is_internal_service"):
        return
    user_id = _user_id(auth)
    if not user_id:
        return
    row = await _db(request).fetchrow(
        "SELECT id,company_id,status,role,onboarding_completed,plan_selected FROM users WHERE id=$1 LIMIT 1",
        user_id,
    )
    if not row:
        raise HTTPException(status_code=401, detail="Session has been revoked. Please log in again.")
    status = str(row.get("status") or "active").strip().lower()
    if status not in _ACCOUNT_RESTRICTED_STATUSES:
        return
    path = request.url.path
    if status == "pending_approval" and _path_matches(path, _PENDING_APPROVAL_ALLOWED_PREFIXES):
        return
    if status == "rejected" and _path_matches(path, _REJECTED_ALLOWED_PREFIXES):
        return
    if status in {"blocked", "paused", "inactive"} and _path_matches(path, _BLOCKED_ALLOWED_PREFIXES):
        return
    event_name = {
        "pending_approval": "pending_user_access_denied",
        "rejected": "rejected_user_access_denied",
        "blocked": "blocked_user_access_denied",
        "paused": "paused_user_access_denied",
        "inactive": "inactive_user_access_denied",
    }.get(status, "restricted_user_access_denied")
    logger.warning(
        "%s actor_user_id=%s target_user_id=%s company_id=%s previous_status=%s new_status=%s reason=%s path=%s",
        event_name,
        user_id,
        user_id,
        str(row.get("company_id") or _company_id(auth)),
        status,
        status,
        "account_status_restricted",
        path,
    )
    raise HTTPException(
        status_code=403,
        detail=_account_status_detail(status),
    )


async def enforce_access_gates(request: Request) -> None:
    """
    Email verification + enterprise invite gate (JWT claims ev / ei).
    Runs before subscription billing checks; uses narrower path exemptions than full billing skip.
    """
    path = request.url.path
    if path in {"/health", "/ready", "/metrics", "/api/healthz", "/api/metrics"} or path.startswith("/health/"):
        return
    auth = _get_auth_context(request)
    if not _company_id(auth) and _role(auth) != "super_admin":
        return
    await check_user_status_active(request)
    if not _is_email_verification_exempt(path):
        await check_email_verified(request)
    if not _relaxed_billing_env() and not _is_enterprise_invite_exempt(path):
        await check_enterprise_invite_gate(request)


async def check_token_not_blacklisted(request: Request) -> None:
    """
    Raise 401 if the request JWT has been explicitly blacklisted (e.g. after
    tenant disable or billing failure).  Safe no-op if no jti in claims.
    """
    auth = _get_auth_context(request)
    jti = _jti(auth)
    if not jti:
        return
    if await is_token_blacklisted(jti):
        raise HTTPException(
            status_code=401,
            detail="Session has been revoked. Please log in again.",
        )


# ── Tenant enabled check ─────────────────────────────────────────────────────


async def check_tenant_enabled(request: Request) -> None:
    """
    Raise 403 if the tenant company has been disabled by a super admin.
    Uses Redis cache → DB fallback.
    """
    if _relaxed_billing_env():
        return
    auth = _get_auth_context(request)
    company_id = _company_id(auth)
    if _role(auth) == "super_admin" or not company_id:
        return

    billing = await _get_billing(company_id, _db(request))
    if billing and billing.get("is_active") is False:
        logger.warning("billing_guard: disabled tenant blocked company_id=%s", company_id)
        raise HTTPException(
            status_code=403,
            detail="This workspace has been disabled. Please contact support.",
        )


# ── Subscription / payment check ─────────────────────────────────────────────


async def require_active_subscription(request: Request) -> None:
    """
    Raise 402 when a paid tenant's subscription is not in good standing.

    Grace period: if subscription expired ≤ 3 days ago (grace_until field),
    allow through with a warning log instead of a hard block.

    Free-plan tenants and super admins are always allowed through.
    """
    if _relaxed_billing_env():
        return
    auth = _get_auth_context(request)
    company_id = _company_id(auth)
    if _role(auth) == "super_admin" or not company_id:
        return

    billing = await _get_billing(company_id, _db(request))
    if not billing:
        return  # No billing record → treat as free, allow.

    plan_code = str(billing.get("plan_code") or "free").strip().lower()
    sub_status = str(billing.get("subscription_status") or "active").strip().lower()
    payment_status = str(billing.get("payment_status") or "inactive").strip().lower()

    if plan_code == "free":
        return  # Free plan always accessible.

    if _offline_trial_period_enforced():
        end_ts = billing.get("current_period_end_ts")
        if end_ts is not None and time.time() > float(end_ts):
            logger.warning(
                "billing_guard: offline trial period ended company_id=%s plan=%s",
                company_id,
                plan_code,
            )
            raise HTTPException(
                status_code=402,
                detail=(
                    "Your trial period has ended. Online billing is not enabled for this deployment; "
                    "please contact your workspace administrator."
                ),
            )

    if sub_status in _BLOCKED_STATUSES:
        grace_until = billing.get("grace_until")
        if grace_until and time.time() < float(grace_until):
            logger.warning(
                "billing_guard: grace period active company_id=%s plan=%s status=%s",
                company_id,
                plan_code,
                sub_status,
            )
            return  # Within grace period — allow with warning.

        logger.warning(
            "billing_guard: access blocked company_id=%s plan=%s status=%s",
            company_id,
            plan_code,
            sub_status,
        )
        raise HTTPException(
            status_code=402,
            detail=("Your subscription is no longer active. Please renew your plan to continue."),
        )

    if sub_status in _ACTIVE_STATUSES and payment_status == "failed":
        logger.warning(
            "billing_guard: payment failed company_id=%s plan=%s",
            company_id,
            plan_code,
        )
        raise HTTPException(
            status_code=402,
            detail=("Your last payment failed. Please update your billing details to restore access."),
        )


# ── Combined convenience dependency ─────────────────────────────────────────


async def enforce_billing(request: Request) -> None:
    """
    Single dependency that runs all three checks in order:
      1. Token blacklist
      2. Tenant enabled
      3. Active subscription

    Used by app_factory middleware so every service enforces billing
    on every non-exempt request without touching individual routers.

    In DEMO_MODE / STRIPE_OPTIONAL, steps 2–3 are skipped (subscription and tenant
    billing gates disabled); token blacklist still applies.
    """
    if _is_exempt(request.url.path):
        return
    auth = _get_auth_context(request)
    if not _company_id(auth) and _role(auth) != "super_admin":
        return  # Unauthenticated path — gateway already handles auth.
    await check_token_not_blacklisted(request)
    if _relaxed_billing_env():
        return
    await check_tenant_enabled(request)
    await require_active_subscription(request)
