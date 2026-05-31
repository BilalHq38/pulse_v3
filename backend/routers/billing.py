"""Billing and subscription routes."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from core.utils import make_id
from services.billing_helpers import (
    PLAN_CATALOG,
    assert_stripe_ready,
    get_plan,
    get_or_create_billing_customer,
    normalize_plan_code,
    normalize_plan_code_from_lookup_key,
    require_stripe_webhook_secret,
    stripe_configured,
    stripe_enabled_for_app,
    stripe_price_id,
    stripe_webhook_configured,
    update_company_billing_summary,
    upsert_subscription,
    uses_local_billing_customer_id,
)
from services.db_helpers import get_current_user_flexible, require_roles, r
from services.public_signup_service import finalize_public_registration_from_checkout

try:
    import stripe
except Exception:  # pragma: no cover
    stripe = None

router = APIRouter()

FRONTEND_BASE_URL = (
    os.environ.get("FRONTEND_BASE_URL", "") or os.environ.get("APP_URL", "") or "http://localhost:3000"
).rstrip("/")


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    plan_code: str = Field(..., min_length=1, max_length=32)
    success_url: str = Field(default="", max_length=2000)
    cancel_url: str = Field(default="", max_length=2000)


def _db(req: Request):
    return req.app.state.db


async def _get_or_create_billing_customer(db, company_id: str, current_user: dict) -> dict:
    return await get_or_create_billing_customer(
        db,
        company_id,
        billing_email=(current_user.get("email") or "").strip().lower(),
        billing_name=(current_user.get("name") or "").strip(),
        payment_status="inactive",
    )


async def _upsert_subscription(
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
) -> dict:
    return await upsert_subscription(
        db,
        company_id,
        billing_customer_id=billing_customer_id,
        plan_code=plan_code,
        status=status,
        stripe_subscription_id=stripe_subscription_id,
        billing_interval=billing_interval,
        current_period_start=current_period_start,
        current_period_end=current_period_end,
        canceled_at=canceled_at,
    )


def _stripe_price_id(plan_code: str) -> str:
    return stripe_price_id(plan_code)


async def _create_stripe_customer_record(billing_customer: dict) -> dict:
    assert_stripe_ready()
    stripe_customer_id = str(billing_customer.get("stripe_customer_id") or "").strip()
    if stripe_customer_id and not uses_local_billing_customer_id(stripe_customer_id):
        return billing_customer
    customer = await asyncio.to_thread(
        stripe.Customer.create,
        email=billing_customer.get("billing_email", ""),
        name=billing_customer.get("billing_name", ""),
        metadata={"billing_customer_id": billing_customer["id"]},
    )
    billing_customer["stripe_customer_id"] = customer["id"]
    return billing_customer


@router.get("/billing/plans")
async def list_plans():
    return {"plans": list(PLAN_CATALOG.values())}


@router.get("/billing/subscription")
async def get_subscription(request: Request):
    db = _db(request)
    current_user = await get_current_user_flexible(request)
    company_id = (current_user.get("company_id", "") or "").strip()
    billing_customer = r(await db.fetchrow("SELECT * FROM billing_customers WHERE company_id=$1 LIMIT 1", company_id))
    subscription = r(await db.fetchrow("SELECT * FROM subscriptions WHERE company_id=$1 LIMIT 1", company_id))
    if not subscription:
        subscription = await _upsert_subscription(
            db,
            company_id,
            billing_customer_id=(billing_customer or {}).get("id"),
            plan_code="free",
            status="active",
        )
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    usage = await db.fetch(
        "SELECT usage_type, COALESCE(SUM(usage_units),0) AS usage_units FROM usage_ledger "
        "WHERE company_id=$1 AND occurred_at >= $2 GROUP BY usage_type ORDER BY usage_type",
        company_id,
        month_start,
    )
    meta_usage = await db.fetch(
        "SELECT channel, COALESCE(SUM(request_count),0) AS request_count, COALESCE(SUM(billable_units),0) AS billable_units "  # noqa: E501
        "FROM meta_api_usage WHERE company_id=$1 AND recorded_at >= $2 GROUP BY channel ORDER BY channel",
        company_id,
        month_start,
    )
    return {
        "subscription": subscription,
        "billing_customer": billing_customer or {},
        "usage": [dict(row) for row in usage],
        "meta_usage": [dict(row) for row in meta_usage],
    }


@router.post("/billing/checkout-session")
async def create_checkout_session(payload: CheckoutRequest, request: Request):
    db = _db(request)
    current_user = await require_roles(request, ["admin", "super_admin"])
    company_id = (current_user.get("company_id", "") or "").strip()
    plan_code = normalize_plan_code(payload.plan_code)
    get_plan(plan_code)
    if plan_code == "free":
        subscription = await _upsert_subscription(
            db,
            company_id,
            plan_code="free",
            status="active",
        )
        return {"ok": True, "mode": "local", "subscription": subscription}

    if not stripe_configured() or not stripe_enabled_for_app():
        raise HTTPException(
            422,
            "Paid checkout requires Stripe (STRIPE_SECRET_KEY and STRIPE_PRICE_*). "
            "Use the free plan, set STRIPE_ENABLED=auto (default), or enable Stripe in your environment.",
        )
    assert_stripe_ready()
    price_id = _stripe_price_id(plan_code)
    if not price_id:
        raise HTTPException(501, f"Stripe price is not configured for plan {plan_code}")
    billing_customer = await _get_or_create_billing_customer(db, company_id, current_user)
    billing_customer = await _create_stripe_customer_record(billing_customer)
    await db.execute(
        "UPDATE billing_customers SET stripe_customer_id=$1, updated_at=NOW() WHERE id=$2",
        billing_customer["stripe_customer_id"],
        billing_customer["id"],
    )
    session = await asyncio.to_thread(
        stripe.checkout.Session.create,
        mode="subscription",
        customer=billing_customer["stripe_customer_id"],
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=(payload.success_url or f"{FRONTEND_BASE_URL}/settings?tab=billing&checkout=success").strip(),
        cancel_url=(payload.cancel_url or f"{FRONTEND_BASE_URL}/pricing?checkout=cancelled").strip(),
        metadata={"company_id": company_id, "plan_code": plan_code},
        subscription_data={"metadata": {"company_id": company_id, "plan_code": plan_code}},
        allow_promotion_codes=True,
    )
    await _upsert_subscription(
        db,
        company_id,
        billing_customer_id=billing_customer["id"],
        plan_code=plan_code,
        status="pending",
    )
    return {"ok": True, "checkout_url": session.url, "session_id": session.id}


@router.post("/billing/customer-portal")
async def create_customer_portal(request: Request):
    db = _db(request)
    current_user = await require_roles(request, ["admin", "super_admin"])
    company_id = (current_user.get("company_id", "") or "").strip()
    billing_customer = r(await db.fetchrow("SELECT * FROM billing_customers WHERE company_id=$1 LIMIT 1", company_id))
    if not billing_customer or not billing_customer.get("stripe_customer_id"):
        raise HTTPException(404, "Stripe customer is not configured for this workspace")
    if not stripe_configured() or not stripe_enabled_for_app():
        raise HTTPException(
            422,
            "Billing portal requires Stripe. Configure STRIPE_SECRET_KEY or disable this action in demo mode.",
        )
    assert_stripe_ready()
    session = await asyncio.to_thread(
        stripe.billing_portal.Session.create,
        customer=billing_customer["stripe_customer_id"],
        return_url=f"{FRONTEND_BASE_URL}/settings?tab=billing",
    )
    return {"ok": True, "portal_url": session.url}


@router.post("/billing/usage/events")
async def record_usage_event(request: Request):
    db = _db(request)
    current_user = await require_roles(request, ["admin", "super_admin"])
    company_id = (current_user.get("company_id", "") or "").strip()
    body = await request.json()
    await db.execute(
        "INSERT INTO usage_ledger(id,company_id,subscription_id,usage_type,usage_units,usage_window,reference_id,metadata,usage_idempotency_key,occurred_at,created_at) "  # noqa: E501
        "VALUES($1,$2,(SELECT id FROM subscriptions WHERE company_id=$2 LIMIT 1),$3,$4,$5,$6,$7,'',NOW(),NOW())",
        make_id(),
        company_id,
        str(body.get("usage_type") or "ai_tokens"),
        float(body.get("usage_units") or 0),
        str(body.get("usage_window") or "monthly"),
        str(body.get("reference_id") or ""),
        json.dumps(body.get("metadata") or {}, ensure_ascii=True),
    )
    return {"ok": True}


@router.post("/billing/webhooks/stripe")
async def stripe_webhook(request: Request):
    db = _db(request)
    if not stripe_enabled_for_app() or not stripe_webhook_configured():
        return {"ok": True, "ignored": True, "reason": "stripe_disabled_or_unconfigured"}
    webhook_secret = require_stripe_webhook_secret()
    payload = await request.body()
    signature = request.headers.get("Stripe-Signature", "")
    try:
        event = await asyncio.to_thread(
            stripe.Webhook.construct_event,
            payload,
            signature,
            webhook_secret,
        )
    except Exception as exc:
        raise HTTPException(400, "Invalid Stripe webhook signature") from exc

    event_id = str(event.get("id") or "").strip()
    event_type = str(event.get("type") or "").strip()
    if not event_id:
        raise HTTPException(400, "Invalid Stripe event")
    if await db.fetchval(
        "SELECT id FROM stripe_webhook_events WHERE stripe_event_id=$1 LIMIT 1",
        event_id,
    ):
        return {"ok": True, "duplicate": True}

    company_id = ""
    event_object = (event.get("data") or {}).get("object") or {}
    metadata = event_object.get("metadata") or {}
    company_id = str(metadata.get("company_id") or "").strip()

    await db.execute(
        "INSERT INTO stripe_webhook_events(id,company_id,stripe_event_id,event_type,payload,processed,created_at) "
        "VALUES($1,$2,$3,$4,$5,FALSE,NOW())",
        make_id(),
        company_id,
        event_id,
        event_type,
        json.dumps(event, ensure_ascii=True, default=str),
    )

    if event_type == "checkout.session.completed":
        pending_signup_id = str(metadata.get("pending_signup_id") or "").strip()
        if pending_signup_id:
            result = await finalize_public_registration_from_checkout(
                db,
                request,
                event_object,
                stripe_event_id=event_id,
            )
            company_id = str(result.get("company_id") or company_id).strip()
        else:
            company_id = str(metadata.get("company_id") or company_id).strip()
            plan_code = normalize_plan_code_from_lookup_key(str(metadata.get("plan_code") or "pro").strip())
            customer_id = str(event_object.get("customer") or "").strip()
            subscription_id = str(event_object.get("subscription") or "").strip()
            if company_id:
                async with db.transaction() as conn:
                    billing_customer = await conn.fetchval(
                        "SELECT id FROM billing_customers WHERE company_id=$1 LIMIT 1",
                        company_id,
                    )
                    if billing_customer:
                        await conn.execute(
                            "UPDATE billing_customers SET stripe_customer_id=$1,payment_status='active',updated_at=NOW() WHERE id=$2",  # noqa: E501
                            customer_id,
                            billing_customer,
                        )
                        await update_company_billing_summary(conn, company_id, billing_status="active")
                    await _upsert_subscription(
                        conn,
                        company_id,
                        billing_customer_id=billing_customer,
                        plan_code=plan_code,
                        status="active",
                        stripe_subscription_id=subscription_id,
                    )
                    await conn.execute(
                        "UPDATE users SET status='active',plan_selected=TRUE,billing_status='active',updated_at=NOW() WHERE company_id=$1",
                        company_id,
                    )
                try:
                    from core.socket import emit_company_event
                    await emit_company_event(
                        company_id,
                        "plan_updated",
                        {"plan_code": plan_code, "status": "active"},
                    )
                except Exception:
                    pass  # Non-fatal — polling fallback handles this

    if event_type.startswith("customer.subscription."):
        subscription_id = str(event_object.get("id") or "").strip()
        customer_id = str(event_object.get("customer") or "").strip()
        if not company_id and customer_id:
            company_id = str(
                await db.fetchval(
                    "SELECT company_id FROM billing_customers WHERE stripe_customer_id=$1 LIMIT 1",
                    customer_id,
                )
                or ""
            ).strip()
        if company_id:
            billing_customer_id = await db.fetchval(
                "SELECT id FROM billing_customers WHERE company_id=$1 LIMIT 1",
                company_id,
            )
            price = ((event_object.get("items") or {}).get("data") or [{}])[0].get("price") or {}
            plan_code = normalize_plan_code_from_lookup_key(
                str(price.get("lookup_key") or "").strip()
                or str((event_object.get("metadata") or {}).get("plan_code") or "pro").strip(),
                default="pro",
            )
            await _upsert_subscription(
                db,
                company_id,
                billing_customer_id=billing_customer_id,
                plan_code=plan_code,
                status=str(event_object.get("status") or "active"),
                stripe_subscription_id=subscription_id,
                billing_interval=str(((price.get("recurring") or {}).get("interval") or "month")),
                current_period_start=datetime.fromtimestamp(
                    int(event_object.get("current_period_start") or 0), timezone.utc
                )
                if event_object.get("current_period_start")
                else None,
                current_period_end=datetime.fromtimestamp(
                    int(event_object.get("current_period_end") or 0), timezone.utc
                )
                if event_object.get("current_period_end")
                else None,
                canceled_at=datetime.fromtimestamp(int(event_object.get("canceled_at") or 0), timezone.utc)
                if event_object.get("canceled_at")
                else None,
            )
            status = str(event_object.get("status") or "active").strip().lower()
            if status in {"active", "trialing"}:
                await db.execute(
                    "UPDATE users SET plan_selected=TRUE,billing_status=$1,updated_at=NOW() WHERE company_id=$2",
                    "trial" if status == "trialing" else "active",
                    company_id,
                )

    if event_type in {"invoice.payment_failed", "invoice.payment_succeeded"}:
        customer_id = str(event_object.get("customer") or "").strip()
        if customer_id:
            payment_status = "failed" if event_type == "invoice.payment_failed" else "active"
            await db.execute(
                "UPDATE billing_customers SET payment_status=$1,updated_at=NOW() WHERE stripe_customer_id=$2",
                payment_status,
                customer_id,
            )
            invoice_company_id = str(
                await db.fetchval(
                    "SELECT company_id FROM billing_customers WHERE stripe_customer_id=$1 LIMIT 1",
                    customer_id,
                )
                or ""
            ).strip()
            if invoice_company_id:
                company_id = company_id or invoice_company_id
                await update_company_billing_summary(db, invoice_company_id, billing_status=payment_status)

    await db.execute(
        "UPDATE stripe_webhook_events SET processed=TRUE,processed_at=NOW() WHERE stripe_event_id=$1",
        event_id,
    )

    # Invalidate billing cache so next request reads fresh state from DB
    if company_id:
        try:
            from shared.billing_cache import invalidate_billing_cache

            await invalidate_billing_cache(company_id)
        except Exception as _cache_exc:
            pass  # Non-fatal — downstream service will re-populate on next request

    return {"ok": True}
