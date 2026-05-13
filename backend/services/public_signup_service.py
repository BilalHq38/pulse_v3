from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import BackgroundTasks, HTTPException, Request

from core.request_helpers import resolve_frontend_base_url
from core.utils import make_id, validate_password
from models.reference_data import ensure_company_reference_data, resolve_role_id
from services.billing_helpers import (
    assert_stripe_ready,
    get_plan,
    relaxed_billing_env,
    stripe,
    stripe_configured,
    stripe_enabled_for_app,
    stripe_price_id,
    trial_period_days,
    update_billing_customer_status,
    upsert_subscription,
)
from services.db_helpers import (
    build_email_verification_meta,
    get_email_verification_resend_control,
    r,
    record_auth_event,
    record_system_log,
    send_email_verification_message,
    set_company_context,
    set_public_auth_context,
)
from shared.auth.jwt import hash_password
from shared.billing_cache import invalidate_billing_cache

logger = logging.getLogger(__name__)

PENDING_SIGNUP_TTL_HOURS = max(1, int(os.environ.get("PENDING_SIGNUP_TTL_HOURS", "24") or 24))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stringify_error(exc: Exception) -> str:
    detail = str(exc).strip()
    return detail[:300] if detail else exc.__class__.__name__


def _stripe_value(obj: Any, key: str, default: Any = "") -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


async def _migrate_pending_signup_stripe_columns(db) -> None:
    """Legacy tables used UNIQUE(stripe_checkout_session_id) with DEFAULT '' — only one pending row could exist."""
    for constraint_name in (
        "uq_pending_signups_checkout",
        "pending_signups_stripe_checkout_session_id_key",
    ):
        try:
            await db.execute(f"ALTER TABLE public.pending_signups DROP CONSTRAINT IF EXISTS {constraint_name}")
        except Exception:
            logger.debug("pending_signups drop constraint %s skipped", constraint_name, exc_info=True)
    for col in (
        "stripe_checkout_session_id",
        "stripe_customer_id",
        "stripe_subscription_id",
        "stripe_event_id",
    ):
        try:
            await db.execute(
                f"UPDATE public.pending_signups SET {col} = NULL WHERE {col} IS NOT NULL AND BTRIM({col}::text) = ''"
            )
        except Exception:
            logger.debug("pending_signups normalize %s skipped", col, exc_info=True)
        try:
            await db.execute(f"ALTER TABLE public.pending_signups ALTER COLUMN {col} DROP NOT NULL")
        except Exception:
            pass
    try:
        await db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_pending_signups_checkout_nonempty
            ON public.pending_signups (stripe_checkout_session_id)
            WHERE stripe_checkout_session_id IS NOT NULL
              AND BTRIM(stripe_checkout_session_id) <> ''
            """
        )
    except Exception:
        logger.warning("pending_signups partial unique index create failed", exc_info=True)


async def ensure_pending_signup_primitives(db) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS public.pending_signups (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            company_name TEXT NOT NULL DEFAULT '',
            company_industry TEXT NOT NULL DEFAULT '',
            timezone TEXT NOT NULL DEFAULT 'UTC',
            plan_code TEXT NOT NULL DEFAULT 'pro',
            status TEXT NOT NULL DEFAULT 'pending_payment',
            payment_status TEXT NOT NULL DEFAULT 'pending',
            stripe_checkout_session_id TEXT,
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            stripe_event_id TEXT,
            company_id TEXT NOT NULL DEFAULT '',
            user_id TEXT NOT NULL DEFAULT '',
            verification_email_sent_at TIMESTAMPTZ,
            verification_error TEXT NOT NULL DEFAULT '',
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_pending_signups_email UNIQUE (email)
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_pending_signups_status ON public.pending_signups(status, updated_at DESC)"
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_pending_signups_expires_at ON public.pending_signups(expires_at)")
    await _migrate_pending_signup_stripe_columns(db)


async def _expire_stale_pending_signups(db) -> None:
    await db.execute(
        "UPDATE public.pending_signups "
        "SET status='expired',updated_at=NOW() "
        "WHERE status NOT IN ('account_created','expired') AND expires_at < NOW()"
    )


async def _pending_signup_by_email(db, email: str) -> dict[str, Any] | None:
    return r(
        await db.fetchrow(
            "SELECT * FROM public.pending_signups WHERE LOWER(email)=LOWER($1) LIMIT 1",
            (email or "").strip().lower(),
        )
    )


async def _pending_signup_by_session(db, session_id: str) -> dict[str, Any] | None:
    return r(
        await db.fetchrow(
            "SELECT * FROM public.pending_signups WHERE stripe_checkout_session_id=$1 LIMIT 1",
            (session_id or "").strip(),
        )
    )


async def _pending_signup_by_id(db, pending_signup_id: str) -> dict[str, Any] | None:
    return r(
        await db.fetchrow(
            "SELECT * FROM public.pending_signups WHERE id=$1 LIMIT 1",
            (pending_signup_id or "").strip(),
        )
    )


async def _email_verification_status_for_user(db, user: dict) -> dict[str, Any]:
    verification = r(
        await db.fetchrow(
            "SELECT * FROM email_verifications WHERE user_id=$1 ORDER BY created_at DESC LIMIT 1",
            user["id"],
        )
    )
    ctrl = await get_email_verification_resend_control(db, user)
    count = int(ctrl.get("resend_count", 0) or 0)
    if verification:
        return build_email_verification_meta(
            {
                "email": verification.get("email") or (user.get("email") or "").strip().lower(),
                "expires_at": (
                    verification["expires_at"].isoformat()
                    if hasattr(verification.get("expires_at"), "isoformat")
                    else verification.get("expires_at")
                ),
            },
            count,
            lock_until=ctrl.get("lock_until"),
            resend_available_at=ctrl.get("resend_available_at"),
        )
    return {
        "required": True,
        "email": (user.get("email") or "").strip().lower(),
        "message": "Verification email is pending.",
    }


async def prepare_public_registration(
    db,
    request: Request,
    payload: dict[str, Any],
    *,
    background_tasks: BackgroundTasks | None = None,
) -> dict[str, Any]:
    await ensure_pending_signup_primitives(db)
    await _expire_stale_pending_signups(db)

    email = (payload.get("email") or "").strip().lower()
    name = (payload.get("name") or "").strip()
    # Company profile details now belong to the later onboarding step, not manual signup.
    company_name = "My Company"
    company_industry = ""
    timezone_name = (payload.get("timezone") or "").strip() or "UTC"
    password = payload.get("password", "")
    plan = get_plan(payload.get("plan_code") or "pro")

    if not email:
        raise HTTPException(400, "Email is required")
    if not name:
        raise HTTPException(400, "Name is required")
    if plan["code"] == "free":
        raise HTTPException(400, "A paid plan is required before workspace creation")

    is_valid, errors = validate_password(password)
    if not is_valid:
        raise HTTPException(400, "; ".join(errors))

    await set_public_auth_context(db, email=email)
    existing_user = await db.fetchval(
        "SELECT id FROM users WHERE LOWER(email)=LOWER($1) LIMIT 1",
        email,
    )
    if existing_user:
        raise HTTPException(409, "Email already registered")

    pending_signup = await _pending_signup_by_email(db, email)
    pending_signup_id = pending_signup["id"] if pending_signup else make_id()
    expires_at = _utc_now() + timedelta(hours=PENDING_SIGNUP_TTL_HOURS)
    await db.execute(
        "INSERT INTO public.pending_signups("
        "id,email,password_hash,name,company_name,company_industry,timezone,plan_code,status,payment_status,"
        "stripe_checkout_session_id,stripe_customer_id,stripe_subscription_id,stripe_event_id,company_id,user_id,"
        "verification_email_sent_at,verification_error,expires_at,created_at,updated_at"
        ") VALUES("
        "$1,$2,$3,$4,$5,$6,$7,$8,'pending_payment','pending',NULL,NULL,NULL,NULL,'','',NULL,'', $9,NOW(),NOW()"
        ") ON CONFLICT (email) DO UPDATE SET "
        "password_hash=EXCLUDED.password_hash,"
        "name=EXCLUDED.name,"
        "company_name=EXCLUDED.company_name,"
        "company_industry=EXCLUDED.company_industry,"
        "timezone=EXCLUDED.timezone,"
        "plan_code=EXCLUDED.plan_code,"
        "status='pending_payment',"
        "payment_status='pending',"
        "stripe_checkout_session_id=NULL,"
        "stripe_customer_id=NULL,"
        "stripe_subscription_id=NULL,"
        "stripe_event_id=NULL,"
        "company_id='',"
        "user_id='',"
        "verification_email_sent_at=NULL,"
        "verification_error='',"
        "expires_at=EXCLUDED.expires_at,"
        "updated_at=NOW()",
        pending_signup_id,
        email,
        hash_password(password),
        name,
        company_name,
        company_industry,
        timezone_name,
        plan["code"],
        expires_at,
    )

    _raw_stripe_env = (os.environ.get("STRIPE_ENABLED", "auto") or "auto").strip().lower()
    _stripe_explicit_on = _raw_stripe_env in ("true", "1", "yes", "on", "enabled")
    if _stripe_explicit_on and not stripe_configured() and not relaxed_billing_env():
        assert_stripe_ready()

    if relaxed_billing_env() or not stripe_configured() or not stripe_enabled_for_app():
        logger.info(
            "prepare_public_registration skipping Stripe (relaxed=%s enabled_for_app=%s configured=%s) pending=%s",
            relaxed_billing_env(),
            stripe_enabled_for_app(),
            stripe_configured(),
            pending_signup_id,
        )
        created = await finalize_public_registration_offline_trial(db, request, pending_signup_id)
        ev = created.get("email_verification") or {}
        relaxed = relaxed_billing_env()
        return {
            "status": created.get("status") or "account_created",
            "billing_mode": "trial_relaxed" if relaxed else "trial_no_payment",
            "message": (
                "Your workspace is ready. Verify your email to continue, then complete onboarding."
                if relaxed
                else (
                    f"Your workspace is ready. You have a {trial_period_days()}-day trial with no payment required. "
                    "Verify your email to sign in."
                )
            ),
            "checkout_url": "",
            "session_id": pending_signup_id,
            "plan": plan,
            "email": email,
            "trial_ends_at": created.get("trial_ends_at"),
            "email_verification": ev,
            "verification_error": created.get("verification_error") or "",
            "company_id": created.get("company_id", ""),
            "user_id": created.get("user_id", ""),
        }

    assert_stripe_ready()
    price_id = stripe_price_id(plan["code"])
    if not price_id or price_id.lower().startswith("replace-with-"):
        raise HTTPException(503, f"Stripe price is not configured for plan {plan['code']}")

    frontend_base_url = resolve_frontend_base_url(request)
    success_url = f"{frontend_base_url}/signup/complete?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{frontend_base_url}/signup?checkout=cancelled"
    try:
        checkout_session = await asyncio.to_thread(
            stripe.checkout.Session.create,
            mode="subscription",
            customer_email=email,
            client_reference_id=pending_signup_id,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "pending_signup_id": pending_signup_id,
                "plan_code": plan["code"],
                "signup_email": email,
                "signup_flow": "public",
            },
            subscription_data={
                "metadata": {
                    "pending_signup_id": pending_signup_id,
                    "plan_code": plan["code"],
                    "signup_email": email,
                    "signup_flow": "public",
                }
            },
            allow_promotion_codes=True,
        )
    except Exception as exc:
        logger.exception("Stripe checkout session creation failed for pending_signup_id=%s", pending_signup_id)
        raise HTTPException(502, "Unable to start secure checkout") from exc

    await db.execute(
        "UPDATE public.pending_signups "
        "SET stripe_checkout_session_id=$1,status='checkout_created',payment_status='pending',updated_at=NOW() "
        "WHERE id=$2",
        str(_stripe_value(checkout_session, "id", "") or "").strip(),
        pending_signup_id,
    )
    return {
        "status": "checkout_required",
        "message": "Secure checkout started. Complete payment to create your workspace.",
        "checkout_url": str(_stripe_value(checkout_session, "url", "") or "").strip(),
        "session_id": str(_stripe_value(checkout_session, "id", "") or "").strip(),
        "plan": plan,
        "email": email,
    }


async def get_public_registration_status(db, session_id: str) -> dict[str, Any]:
    await ensure_pending_signup_primitives(db)
    await _expire_stale_pending_signups(db)

    pending_signup = await _pending_signup_by_session(db, session_id)
    if not pending_signup:
        raise HTTPException(404, "Signup session not found")

    response = {
        "status": pending_signup.get("status") or "pending_payment",
        "payment_status": pending_signup.get("payment_status") or "pending",
        "email": pending_signup.get("email", ""),
        "plan_code": pending_signup.get("plan_code", ""),
        "account_created": bool(pending_signup.get("user_id")),
        "verification_error": pending_signup.get("verification_error", ""),
    }
    user_id = str(pending_signup.get("user_id") or "").strip()
    if not user_id:
        return response

    user = r(await db.fetchrow("SELECT * FROM users WHERE id=$1 LIMIT 1", user_id))
    if not user:
        return response
    response["account_created"] = True
    response["company_id"] = user.get("company_id", "")
    response["email_verified"] = bool(user.get("email_verified"))
    if not user.get("email_verified"):
        response["email_verification"] = await _email_verification_status_for_user(db, user)
    return response


async def _complete_pending_signup_workspace(
    db,
    request: Request,
    pending_signup: dict[str, Any],
    *,
    stripe_customer_id: str,
    stripe_subscription_id: str,
    billing_payment_status: str,
    subscription_status: str,
    current_period_start: datetime | None,
    current_period_end: datetime | None,
    pending_signup_final_payment_status: str,
    auth_event_name: str,
    system_log_event: str,
) -> dict[str, Any]:
    """
    Create or attach workspace after payment (Stripe) or offline trial.
    Caller must have updated pending_signups pre-state (e.g. payment_succeeded).
    """
    existing_user = r(
        await db.fetchrow(
            "SELECT * FROM users WHERE LOWER(email)=LOWER($1) LIMIT 1",
            pending_signup["email"],
        )
    )
    if existing_user:
        billing_customer = await update_billing_customer_status(
            db,
            company_id=existing_user.get("company_id", ""),
            stripe_customer_id=stripe_customer_id,
            payment_status=billing_payment_status,
            billing_email=pending_signup["email"],
            billing_name=pending_signup.get("name", ""),
        )
        await upsert_subscription(
            db,
            existing_user.get("company_id", ""),
            billing_customer_id=billing_customer["id"],
            plan_code=pending_signup.get("plan_code") or "pro",
            status=subscription_status,
            stripe_subscription_id=stripe_subscription_id,
            current_period_start=current_period_start,
            current_period_end=current_period_end,
        )
        await invalidate_billing_cache(str(existing_user.get("company_id", "")).strip())
        if not existing_user.get("email_verified"):
            verification_error = ""
            verification_meta: dict[str, Any] = {}
            try:
                _, verification_meta = await send_email_verification_message(
                    db,
                    existing_user,
                    request,
                    "register",
                    background_tasks,
                )
            except Exception as exc:
                verification_error = _stringify_error(exc)
                logger.exception(
                    "Verification resend after signup finalize failed for user_id=%s",
                    existing_user["id"],
                )
            await db.execute(
                "UPDATE public.pending_signups "
                "SET company_id=$1,user_id=$2,status='account_created',payment_status=$3,verification_email_sent_at=$4,verification_error=$5,updated_at=NOW() "  # noqa: E501
                "WHERE id=$6",
                existing_user.get("company_id", ""),
                existing_user["id"],
                pending_signup_final_payment_status,
                _utc_now() if not verification_error else None,
                verification_error,
                pending_signup["id"],
            )
            return {
                "status": "account_created",
                "company_id": existing_user.get("company_id", ""),
                "user_id": existing_user["id"],
                "email_verification": verification_meta,
                "verification_error": verification_error,
            }
        await db.execute(
            "UPDATE public.pending_signups "
            "SET company_id=$1,user_id=$2,status='account_created',payment_status=$3,verification_error='',updated_at=NOW() "  # noqa: E501
            "WHERE id=$4",
            existing_user.get("company_id", ""),
            existing_user["id"],
            pending_signup_final_payment_status,
            pending_signup["id"],
        )
        return {
            "status": "account_created",
            "company_id": existing_user.get("company_id", ""),
            "user_id": existing_user["id"],
        }

    company_id = make_id()
    await set_company_context(db, company_id)
    await db.execute(
        "INSERT INTO companies(id,name,is_active,created_at,updated_at) VALUES($1,$2,TRUE,NOW(),NOW()) ON CONFLICT DO NOTHING",  # noqa: E501
        company_id,
        (pending_signup.get("company_name") or "My Company").strip() or "My Company",
    )
    await ensure_company_reference_data(db, company_id)
    await db.execute(
        "INSERT INTO company_settings(id,company_id,industry,timezone,ai_enabled,ai_confidence_threshold,auto_assign,created_at,updated_at) "  # noqa: E501
        "VALUES($1,$1,$2,$3,TRUE,0.70,TRUE,NOW(),NOW()) ON CONFLICT DO NOTHING",
        company_id,
        (pending_signup.get("company_industry") or "").strip(),
        (pending_signup.get("timezone") or "UTC").strip() or "UTC",
    )

    admin_role_id = await resolve_role_id(db, "admin")
    user_id = make_id()
    await db.execute(
        "INSERT INTO users(id,email,password_hash,name,role,role_id,sub_role,status,avatar,company_id,phone,onboarding_completed,plan_selected,billing_status,auth_provider,email_verified,created_at,updated_at) "  # noqa: E501
        "VALUES($1,$2,$3,$4,'admin',$5,'','active','',$6,'',FALSE,FALSE,'active','email',FALSE,NOW(),NOW())",
        user_id,
        (pending_signup["email"] or "").strip().lower(),
        pending_signup["password_hash"],
        (pending_signup.get("name") or "").strip() or pending_signup["email"].split("@")[0],
        admin_role_id,
        company_id,
    )

    await record_auth_event(
        db,
        user_id,
        auth_event_name,
        request,
        True,
        pending_signup["email"],
    )
    await record_system_log(
        db,
        {"sub": user_id, "company_id": company_id, "role": "admin"},
        system_log_event,
        "user",
        user_id,
        {
            "email": pending_signup["email"],
            "plan_code": pending_signup.get("plan_code") or "pro",
        },
    )

    billing_customer = await update_billing_customer_status(
        db,
        company_id=company_id,
        stripe_customer_id=stripe_customer_id,
        payment_status=billing_payment_status,
        billing_email=pending_signup["email"],
        billing_name=pending_signup.get("name", ""),
    )
    await upsert_subscription(
        db,
        company_id,
        billing_customer_id=billing_customer["id"],
        plan_code=pending_signup.get("plan_code") or "pro",
        status=subscription_status,
        stripe_subscription_id=stripe_subscription_id,
        current_period_start=current_period_start,
        current_period_end=current_period_end,
    )
    await invalidate_billing_cache(company_id)

    user = r(await db.fetchrow("SELECT * FROM users WHERE id=$1 LIMIT 1", user_id))
    verification_meta = {}
    verification_error = ""
    verification_sent_at = None
    try:
        _, verification_meta = await send_email_verification_message(
            db,
            user,
            request,
            "register",
            background_tasks,
        )
        verification_sent_at = _utc_now()
    except Exception as exc:
        verification_error = _stringify_error(exc)
        logger.exception("Verification email failed after signup finalize for user_id=%s", user_id)

    await db.execute(
        "UPDATE public.pending_signups "
        "SET company_id=$1,user_id=$2,status='account_created',payment_status=$3,verification_email_sent_at=$4,verification_error=$5,updated_at=NOW() "  # noqa: E501
        "WHERE id=$6",
        company_id,
        user_id,
        pending_signup_final_payment_status,
        verification_sent_at,
        verification_error,
        pending_signup["id"],
    )
    return {
        "status": "account_created",
        "company_id": company_id,
        "user_id": user_id,
        "email_verification": verification_meta,
        "verification_error": verification_error,
    }


async def finalize_public_registration_offline_trial(
    db,
    request: Request,
    pending_signup_id: str,
) -> dict[str, Any]:
    """Provision workspace without Stripe when checkout is disabled (non-demo deployments)."""
    await ensure_pending_signup_primitives(db)
    pending_signup = await _pending_signup_by_id(db, pending_signup_id)
    if not pending_signup:
        raise HTTPException(404, "Signup session not found")
    st = str(pending_signup.get("status") or "").strip().lower()
    if st == "account_created" and str(pending_signup.get("user_id") or "").strip():
        user = r(await db.fetchrow("SELECT * FROM users WHERE id=$1 LIMIT 1", pending_signup["user_id"]))
        if user and not user.get("email_verified"):
            return {
                "status": "account_created",
                "company_id": user.get("company_id", ""),
                "user_id": user["id"],
                "email_verification": await _email_verification_status_for_user(db, user),
                "trial_ends_at": None,
            }
        return {
            "status": "account_created",
            "company_id": pending_signup.get("company_id", ""),
            "user_id": pending_signup.get("user_id", ""),
        }

    if st not in ("pending_payment", "checkout_created"):
        raise HTTPException(
            409,
            "This signup cannot be completed. Start again or sign in if you already have an account.",
        )

    now = _utc_now()
    days = trial_period_days()
    period_end = now + timedelta(days=days)

    await db.execute(
        "UPDATE public.pending_signups "
        "SET status='payment_succeeded',payment_status='trial',stripe_checkout_session_id=NULL,"
        "stripe_customer_id=NULL,stripe_subscription_id=NULL,stripe_event_id=NULL,updated_at=NOW() "
        "WHERE id=$1",
        pending_signup["id"],
    )
    pending_signup = await _pending_signup_by_id(db, pending_signup_id)
    if not pending_signup:
        raise HTTPException(500, "Pending signup lost after update")

    result = await _complete_pending_signup_workspace(
        db,
        request,
        pending_signup,
        stripe_customer_id="",
        stripe_subscription_id="",
        billing_payment_status="inactive",
        subscription_status="trialing",
        current_period_start=now,
        current_period_end=period_end,
        pending_signup_final_payment_status="trial",
        auth_event_name="register_offline_trial",
        system_log_event="register_offline_trial",
    )
    return {**result, "trial_ends_at": period_end.isoformat()}


async def finalize_public_registration_from_checkout(
    db,
    request: Request,
    checkout_session: Any,
    *,
    stripe_event_id: str = "",
) -> dict[str, Any]:
    await ensure_pending_signup_primitives(db)

    metadata = _stripe_value(checkout_session, "metadata", {}) or {}
    pending_signup_id = str(metadata.get("pending_signup_id") or "").strip()
    checkout_session_id = str(_stripe_value(checkout_session, "id", "") or "").strip()
    customer_id = str(_stripe_value(checkout_session, "customer", "") or "").strip()
    subscription_id = str(_stripe_value(checkout_session, "subscription", "") or "").strip()

    pending_signup = (
        await _pending_signup_by_id(db, pending_signup_id) if pending_signup_id else None
    ) or await _pending_signup_by_session(db, checkout_session_id)
    if not pending_signup:
        return {"status": "ignored", "reason": "pending_signup_not_found"}

    await db.execute(
        "UPDATE public.pending_signups "
        "SET stripe_checkout_session_id=$1,stripe_customer_id=$2,stripe_subscription_id=$3,stripe_event_id=$4,"
        "payment_status='paid',status='payment_succeeded',updated_at=NOW() "
        "WHERE id=$5",
        checkout_session_id,
        customer_id,
        subscription_id,
        stripe_event_id,
        pending_signup["id"],
    )
    pending_signup = (
        await _pending_signup_by_id(db, str(pending_signup["id"]))
        or pending_signup
    )
    return await _complete_pending_signup_workspace(
        db,
        request,
        pending_signup,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        billing_payment_status="active",
        subscription_status="active",
        current_period_start=None,
        current_period_end=None,
        pending_signup_final_payment_status="paid",
        auth_event_name="register_paid",
        system_log_event="register_paid",
    )
