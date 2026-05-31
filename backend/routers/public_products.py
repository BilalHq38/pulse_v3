"""routers/public_products.py — Public product pages and buy endpoint.

These endpoints serve unauthenticated traffic from the dynamic product page
at /c/{company_slug}/product/{product_slug}. Reads bypass RLS via the
platform_admin context. The buy endpoint is rate-limited per IP, runs the
stock check and order insert in one transaction with FOR UPDATE row locking,
and is idempotent on retries.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
import re as _re

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from core.utils import make_id
from shared.cache import get_cache_client
from shared.database import platform_admin_context
from shared.product_ref_token import decode_ref_token
from shared.rate_limit import rate_limit

logger = logging.getLogger(__name__)
router = APIRouter()

PUBLIC_BUY_SOURCE_CHANNEL = "public_product_page"
AVAILABILITY_CACHE_TTL_SECONDS = 30
AVAILABILITY_CACHE_NAMESPACE = "public_products_availability"

_PUBLIC_PRODUCT_SELECT_SQL = (
    "SELECT p.id, p.company_id, p.name, p.product_title, p.description, p.price, p.price_currency, "
    "p.category, p.product_type, p.status, p.links, p.link_source, p.slug, p.public_page_enabled, "
    "p.stock_quantity, p.created_at, p.updated_at, "
    "COALESCE(img.images, '[]'::json) AS images, "
    "COALESCE(feat.features, '[]'::json) AS features "
    "FROM company_products p "
    "LEFT JOIN LATERAL ("
    "SELECT json_agg(image_url ORDER BY sort_order) AS images "
    "FROM product_images WHERE product_id = p.id"
    ") img ON TRUE "
    "LEFT JOIN LATERAL ("
    "SELECT json_agg(feature ORDER BY sort_order) AS features "
    "FROM product_features WHERE product_id = p.id"
    ") feat ON TRUE "
    "WHERE p.company_id = $1 AND p.slug = $2 AND p.public_page_enabled = TRUE "
    "AND p.status = 'active' LIMIT 1"
)


def _db(request: Request):
    return request.app.state.db


def _coerce_list(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _hydrate(record) -> dict:
    record = dict(record)
    record["images"] = _coerce_list(record.get("images"))
    record["features"] = _coerce_list(record.get("features"))
    return record


async def _load_company_public_profile(conn, company_id: str) -> dict:
    company_row = await conn.fetchrow(
        "SELECT id, name, slug FROM companies WHERE id = $1 LIMIT 1", company_id
    )
    settings_row = await conn.fetchrow(
        "SELECT industry, tagline, description, logo_url, support_email, website_address "
        "FROM company_settings WHERE company_id = $1 LIMIT 1",
        company_id,
    )
    company = dict(company_row) if company_row else {}
    settings = dict(settings_row) if settings_row else {}
    return {
        "id": company.get("id", ""),
        "name": company.get("name", ""),
        "slug": company.get("slug", ""),
        "industry": settings.get("industry", "") or "",
        "tagline": settings.get("tagline", "") or "",
        "description": settings.get("description", "") or "",
        "logo_url": settings.get("logo_url", "") or "",
        "support_email": settings.get("support_email", "") or "",
        "website_address": settings.get("website_address", "") or "",
    }


async def _resolve_company_id(conn, company_slug: str) -> Optional[str]:
    row = await conn.fetchrow(
        "SELECT id FROM companies WHERE slug = $1 AND is_active = TRUE AND deleted_at IS NULL LIMIT 1",
        company_slug,
    )
    if row:
        return str(row["id"])
    return None


@router.get("/public/companies/{company_slug}/products/{product_slug}")
async def get_public_product(company_slug: str, product_slug: str, request: Request):
    db = _db(request)
    async with platform_admin_context(db) as conn:
        company_id = await _resolve_company_id(conn, company_slug)
        if not company_id:
            raise HTTPException(404, "product not found")
        row = await conn.fetchrow(_PUBLIC_PRODUCT_SELECT_SQL, company_id, product_slug)
        if not row:
            raise HTTPException(404, "product not found")
        product = _hydrate(row)
        company = await _load_company_public_profile(conn, company_id)
    return {"product": product, "company": company}


@router.get("/public/companies/{company_slug}/products/{product_slug}/availability")
async def get_public_product_availability(
    company_slug: str, product_slug: str, request: Request
):
    db = _db(request)
    cache = get_cache_client(namespace=AVAILABILITY_CACHE_NAMESPACE)
    cache_key = f"{company_slug}:{product_slug}"
    cached = await cache.get_json(cache_key)
    if cached is not None:
        return cached

    async with platform_admin_context(db) as conn:
        company_id = await _resolve_company_id(conn, company_slug)
        if not company_id:
            raise HTTPException(404, "product not found")
        row = await conn.fetchrow(
            "SELECT stock_quantity, status FROM company_products "
            "WHERE company_id = $1 AND slug = $2 AND public_page_enabled = TRUE LIMIT 1",
            company_id,
            product_slug,
        )
        if not row:
            raise HTTPException(404, "product not found")
        payload = {
            "stock_quantity": row["stock_quantity"],
            "status": str(row["status"] or ""),
        }
    await cache.set_json(cache_key, payload, ttl_seconds=AVAILABILITY_CACHE_TTL_SECONDS)
    return payload


_EMAIL_RE = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PublicBuyRequest(BaseModel):
    customer_name: str = Field(min_length=1, max_length=120)
    customer_phone: str = Field(default="", max_length=80)
    customer_email: Optional[str] = Field(default=None, max_length=254)
    quantity: int = Field(default=1, ge=1, le=99)

    @field_validator("customer_email", mode="before")
    @classmethod
    def _validate_email(cls, v):
        if v is None or v == "":
            return None
        if not _EMAIL_RE.match(str(v)):
            raise ValueError("invalid email address")
        return str(v).lower()
    shipping_address: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=500)
    client_request_id: str = Field(default="", max_length=64)

    @model_validator(mode="after")
    def _validate_contact_and_idempotency(self) -> "PublicBuyRequest":
        phone = (self.customer_phone or "").strip()
        email = (self.customer_email or "").strip() if self.customer_email else ""
        if not phone and not email:
            raise ValueError("contact_required")
        if not phone and len(self.client_request_id.strip()) < 16:
            raise ValueError("client_request_id_required_for_anonymous_buy")
        return self


@router.post(
    "/public/companies/{company_slug}/products/{product_slug}/buy",
    dependencies=[Depends(rate_limit("public_buy", limit=5, window_seconds=60))],
)
async def public_buy_product(
    company_slug: str, product_slug: str, request: Request
):
    try:
        raw_body = await request.json()
    except Exception as exc:
        raise HTTPException(400, "request body must be valid JSON") from exc
    try:
        buy = PublicBuyRequest.model_validate(raw_body)
    except ValidationError as exc:
        errors = exc.errors()
        detail = "invalid_request"
        for err in errors:
            msg = err.get("msg") or ""
            if "contact_required" in str(msg):
                detail = "contact_required"
                break
            if "client_request_id_required_for_anonymous_buy" in str(msg):
                detail = "client_request_id_required_for_anonymous_buy"
                break
        raise HTTPException(422, detail) from exc

    db = _db(request)
    customer_phone = (buy.customer_phone or "").strip()
    customer_email = (str(buy.customer_email) if buy.customer_email else "").strip()
    idempotency_key = (buy.client_request_id or "").strip()
    # Decode the signed ref token injected by the conversation engine.
    # The token is opaque — it contains customer_id, session_id, company_id
    # and an expiry, all HMAC-signed. We resolve company_id first so we can
    # validate the token belongs to this company.
    _ref_raw = str(request.query_params.get("ref") or "").strip()
    tracked_customer_id = ""
    tracked_session_id = ""
    _ref_payload: dict | None = None
    if _ref_raw:
        # company_id not yet resolved at this point — decode without the
        # company check here, then re-validate after we resolve the slug.
        _ref_payload = decode_ref_token(_ref_raw)

    async with platform_admin_context(db) as conn:
        company_id = await _resolve_company_id(conn, company_slug)
        if not company_id:
            raise HTTPException(404, "product not found")

        # Now that company_id is known, validate the ref token is for THIS company.
        if _ref_payload:
            token_company = str(_ref_payload.get("company_id") or "")
            if not token_company or token_company == company_id:
                tracked_customer_id = str(_ref_payload.get("customer_id") or "")
                tracked_session_id = str(_ref_payload.get("session_id") or "")
            else:
                logger.warning(
                    "ref_token_company_mismatch expected=%s got=%s",
                    company_id, token_company,
                )

        # Validate the tracked customer belongs to this company before trusting it.
        resolved_customer_id = ""
        resolved_lead_id = ""
        if tracked_customer_id:
            cust_row = await conn.fetchrow(
                "SELECT id, lead_id, name, phone, email FROM customers "
                "WHERE id=$1 AND company_id=$2 LIMIT 1",
                tracked_customer_id,
                company_id,
            )
            if cust_row:
                resolved_customer_id = str(cust_row["id"])
                resolved_lead_id = str(cust_row["lead_id"] or "")

        try:
            async with conn.transaction():
                product_row = await conn.fetchrow(
                    "SELECT id, name, stock_quantity, status, public_page_enabled, price, price_currency "
                    "FROM company_products "
                    "WHERE company_id = $1 AND slug = $2 LIMIT 1 FOR UPDATE",
                    company_id,
                    product_slug,
                )
                if not product_row:
                    raise HTTPException(404, "product not found")
                product_data = dict(product_row)
                if not product_data.get("public_page_enabled") or product_data.get("status") != "active":
                    raise HTTPException(404, "product not found")

                stock = product_data.get("stock_quantity")
                if stock is not None and stock < buy.quantity:
                    raise HTTPException(409, "insufficient_stock")

                if stock is not None:
                    await conn.execute(
                        "UPDATE company_products SET stock_quantity = stock_quantity - $1, updated_at = NOW() "
                        "WHERE id = $2",
                        buy.quantity,
                        product_data.get("id"),
                    )

                order_id = make_id()
                # Use the conversation session as conversation_id if available.
                conversation_id = tracked_session_id or ""
                # Calculate total price from product price × quantity.
                unit_price = product_data.get("price")
                total_price = (float(unit_price) * buy.quantity) if unit_price is not None else None
                try:
                    await conn.execute(
                        "INSERT INTO orders("
                        "id, company_id, conversation_id, lead_id, customer_id, product_id, product_name, "
                        "quantity, variant, size, color, customer_name, customer_email, customer_phone, "
                        "delivery_address, notes, status, source_channel, created_by, raw_details, "
                        "missing_fields, idempotency_key, total_price, created_at, updated_at"
                        ") VALUES($1,$2,$3,$4,$5,$6,$7,$8,'','','',$9,$10,$11,$12,$13,'pending',$14,'public',"
                        "'{}'::jsonb,'[]'::jsonb,$15,$16,NOW(),NOW())",
                        order_id,
                        company_id,
                        conversation_id,
                        resolved_lead_id,
                        resolved_customer_id,
                        str(product_data.get("id") or ""),
                        str(product_data.get("name") or ""),
                        buy.quantity,
                        buy.customer_name.strip(),
                        customer_email,
                        customer_phone,
                        buy.shipping_address.strip(),
                        buy.notes.strip(),
                        PUBLIC_BUY_SOURCE_CHANNEL,
                        idempotency_key,
                        total_price,
                    )
                except asyncpg.UniqueViolationError:
                    existing = await conn.fetchrow(
                        "SELECT id, status FROM orders "
                        "WHERE company_id = $1 AND source_channel = $2 AND ("
                        "  (BTRIM(idempotency_key) <> '' AND idempotency_key = $3) OR "
                        "  (BTRIM(customer_phone) <> '' AND customer_phone = $4 "
                        "   AND product_id = $5 "
                        "   AND created_at >= date_trunc('minute', NOW()))"
                        ") ORDER BY created_at DESC LIMIT 1",
                        company_id,
                        PUBLIC_BUY_SOURCE_CHANNEL,
                        idempotency_key,
                        customer_phone,
                        str(product_data.get("id") or ""),
                    )
                    if not existing:
                        raise
                    _eid = str(existing["id"])
                    return {
                        "order_id": _eid,
                        "order_ref": "ORD-" + _eid.replace("-", "").upper()[:6],
                        "status": str(existing["status"] or "pending"),
                        "deduplicated": True,
                    }

                # Update customer record: fill in any missing contact details
                # provided by the buyer and mark them as a customer.
                if resolved_customer_id:
                    await conn.execute(
                        """
                        UPDATE customers
                        SET
                            name        = CASE WHEN BTRIM(name) = '' OR name IS NULL
                                          THEN $1 ELSE name END,
                            phone       = CASE WHEN BTRIM(phone) = '' OR phone IS NULL
                                          THEN $2 ELSE phone END,
                            email       = CASE WHEN BTRIM(email) = '' OR email IS NULL
                                          THEN $3 ELSE email END,
                            lifecycle_stage = 'customer',
                            updated_at  = NOW()
                        WHERE id = $4 AND company_id = $5
                        """,
                        buy.customer_name.strip(),
                        customer_phone or None,
                        customer_email or None,
                        resolved_customer_id,
                        company_id,
                    )

                # Convert the linked lead to customer status.
                if resolved_lead_id:
                    await conn.execute(
                        """
                        UPDATE leads
                        SET
                            status     = 'converted',
                            name       = CASE WHEN BTRIM(name) = '' OR name IS NULL
                                         THEN $1 ELSE name END,
                            phone      = CASE WHEN BTRIM(phone) = '' OR phone IS NULL
                                         THEN $2 ELSE phone END,
                            email      = CASE WHEN BTRIM(email) = '' OR email IS NULL
                                         THEN $3 ELSE email END,
                            metadata   = metadata || $4::jsonb,
                            updated_at = NOW()
                        WHERE id = $5 AND company_id = $6
                        """,
                        buy.customer_name.strip(),
                        customer_phone or None,
                        customer_email or None,
                        json.dumps({"lifecycle_stage": "customer", "converted_via": "public_buy"}),
                        resolved_lead_id,
                        company_id,
                    )
                    logger.info(
                        "lead_converted_via_order company_id=%s lead_id=%s customer_id=%s order_id=%s",
                        company_id, resolved_lead_id, resolved_customer_id, order_id,
                    )

        except HTTPException:
            raise
        except asyncpg.UndefinedColumnError as exc:
            logger.error(
                "public_buy_schema_error company_id=%s product_slug=%s missing_column=%s",
                company_id, product_slug, exc,
            )
            raise HTTPException(500, "order_creation_failed: database schema mismatch. Please apply latest migrations.") from exc
        except Exception as exc:
            logger.exception(
                "public_buy_failed company_id=%s product_slug=%s error=%s",
                company_id, product_slug, exc,
            )
            raise HTTPException(500, "order_creation_failed")

    cache = get_cache_client(namespace=AVAILABILITY_CACHE_NAMESPACE)
    await cache.delete(f"{company_slug}:{product_slug}")

    order_ref = "ORD-" + order_id.replace("-", "").upper()[:6]
    return {
        "order_id": order_id,
        "order_ref": order_ref,
        "status": "pending",
        "deduplicated": False,
        "customer_id": resolved_customer_id or None,
        "lead_converted": bool(resolved_lead_id),
    }


@router.post("/public/track/click")
@router.post("/public/companies/{company_slug}/track/click")
async def track_product_link_click(request: Request, company_slug: str = ""):
    """Record a product link click from the conversation engine.

    Called by the frontend product page on load when a ref token is present.
    Creates a pending order if one doesn't exist yet for this customer+product.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    ref_raw = str(body.get("ref") or request.query_params.get("ref") or "").strip()
    product_id = str(body.get("product_id") or "").strip()
    product_slug_from_body = str(body.get("product_slug") or "").strip()

    click_id = make_id()
    ip_address = str(
        request.headers.get("X-Forwarded-For") or
        request.headers.get("X-Real-IP") or
        (request.client.host if request.client else "")
    ).split(",")[0].strip()[:64]
    user_agent = str(request.headers.get("User-Agent") or "")[:256]

    ref_payload = decode_ref_token(ref_raw) if ref_raw else None
    customer_id = str((ref_payload or {}).get("customer_id") or "")
    session_id = str((ref_payload or {}).get("session_id") or "")
    token_company_id = str((ref_payload or {}).get("company_id") or "")

    db = _db(request)
    try:
        async with platform_admin_context(db) as conn:
            # Resolve company_id from slug if not available from token
            resolved_company = token_company_id
            if not resolved_company and company_slug:
                resolved_company = await _resolve_company_id(conn, company_slug)

            if not resolved_company:
                return {"tracked": False, "reason": "company_not_resolved"}

            # Resolve product_id from slug if needed
            if not product_id and product_slug_from_body:
                row = await conn.fetchrow(
                    "SELECT id FROM company_products WHERE company_id=$1 AND slug=$2 LIMIT 1",
                    resolved_company, product_slug_from_body,
                )
                if row:
                    product_id = str(row["id"])

            await conn.execute(
                "INSERT INTO link_clicks(id,company_id,customer_id,product_id,session_id,ref_token,ip_address,user_agent,clicked_at,created_at) "
                "VALUES($1,$2,$3,$4,$5,$6,$7,$8,NOW(),NOW())",
                click_id, resolved_company, customer_id, product_id,
                session_id, ref_raw[:500], ip_address, user_agent,
            )
            logger.info(
                "product_link_clicked company_id=%s customer_id=%s product_id=%s click_id=%s",
                resolved_company, customer_id, product_id, click_id,
            )
    except Exception as exc:
        logger.warning("link_click_tracking_failed: %s", exc)
        return {"tracked": False, "reason": str(exc)[:100]}

    return {"tracked": True, "click_id": click_id}


__all__ = ["router"]
