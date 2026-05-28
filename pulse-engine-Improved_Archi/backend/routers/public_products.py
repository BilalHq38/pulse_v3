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
from pydantic import BaseModel, EmailStr, Field, ValidationError, model_validator

from core.utils import make_id
from shared.cache import get_cache_client
from shared.database import platform_admin_context
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


class PublicBuyRequest(BaseModel):
    customer_name: str = Field(min_length=1, max_length=120)
    customer_phone: str = Field(default="", max_length=80)
    customer_email: Optional[EmailStr] = None
    quantity: int = Field(default=1, ge=1, le=99)
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

    async with platform_admin_context(db) as conn:
        company_id = await _resolve_company_id(conn, company_slug)
        if not company_id:
            raise HTTPException(404, "product not found")

        try:
            async with conn.transaction():
                product_row = await conn.fetchrow(
                    "SELECT id, name, stock_quantity, status, public_page_enabled "
                    "FROM company_products "
                    "WHERE company_id = $1 AND slug = $2 LIMIT 1 FOR UPDATE",
                    company_id,
                    product_slug,
                )
                if not product_row:
                    raise HTTPException(404, "product not found")
                if not product_row["public_page_enabled"] or product_row["status"] != "active":
                    raise HTTPException(404, "product not found")

                stock = product_row["stock_quantity"]
                if stock is not None and stock < buy.quantity:
                    raise HTTPException(409, "insufficient_stock")

                if stock is not None:
                    await conn.execute(
                        "UPDATE company_products SET stock_quantity = stock_quantity - $1, updated_at = NOW() "
                        "WHERE id = $2",
                        buy.quantity,
                        product_row["id"],
                    )

                order_id = make_id()
                try:
                    await conn.execute(
                        "INSERT INTO orders("
                        "id, company_id, conversation_id, lead_id, customer_id, product_id, product_name, "
                        "quantity, variant, size, color, customer_name, customer_email, customer_phone, "
                        "delivery_address, notes, status, source_channel, created_by, raw_details, "
                        "missing_fields, idempotency_key, created_at, updated_at"
                        ") VALUES($1,$2,'','','',$3,$4,$5,'','','',$6,$7,$8,$9,$10,'pending',$11,'public',"
                        "'{}'::jsonb,'[]'::jsonb,$12,NOW(),NOW())",
                        order_id,
                        company_id,
                        str(product_row["id"]),
                        str(product_row["name"] or ""),
                        buy.quantity,
                        buy.customer_name.strip(),
                        customer_email,
                        customer_phone,
                        buy.shipping_address.strip(),
                        buy.notes.strip(),
                        PUBLIC_BUY_SOURCE_CHANNEL,
                        idempotency_key,
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
                        str(product_row["id"]),
                    )
                    if not existing:
                        raise
                    return {
                        "order_id": str(existing["id"]),
                        "status": str(existing["status"] or "pending"),
                        "deduplicated": True,
                    }
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "public_buy_failed company_id=%s product_slug=%s", company_id, product_slug
            )
            raise HTTPException(500, "order_creation_failed")

    cache = get_cache_client(namespace=AVAILABILITY_CACHE_NAMESPACE)
    await cache.delete(f"{company_slug}:{product_slug}")

    return {"order_id": order_id, "status": "pending", "deduplicated": False}


__all__ = ["router"]
