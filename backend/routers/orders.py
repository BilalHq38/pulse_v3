from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request

from services.db_helpers import get_company_id, get_current_user_flexible, require_roles
from services.order_service import get_order, list_orders, update_order_status

logger = logging.getLogger(__name__)

router = APIRouter()


def _db(request: Request):
    return request.app.state.db


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


@router.get("/orders")
async def list_company_orders(
    request: Request,
    status: str = Query("", max_length=40),
    channel: str = Query("", max_length=40),
    customer: str = Query("", max_length=120),
    limit: int = Query(100, ge=1, le=300),
):
    cu = await get_current_user_flexible(request)
    company_id = get_company_id(cu)
    if not company_id:
        raise HTTPException(401, "Invalid tenant context")
    items = await list_orders(
        _db(request),
        company_id=company_id,
        status=_text(status, 40),
        channel=_text(channel, 40),
        customer=_text(customer, 120),
        limit=limit,
    )
    logger.info(
        "orders_page_fetch_result company_id=%s status=%s channel=%s customer_filter_present=%s count=%s",
        company_id,
        _text(status, 40),
        _text(channel, 40),
        bool(_text(customer, 120)),
        len(items),
    )
    return {"items": items, "total": len(items)}


@router.get("/orders/{order_id}")
async def get_company_order(request: Request, order_id: str):
    cu = await get_current_user_flexible(request)
    company_id = get_company_id(cu)
    if not company_id:
        raise HTTPException(401, "Invalid tenant context")
    order = await get_order(_db(request), company_id=company_id, order_id=_text(order_id, 120))
    if not order:
        raise HTTPException(404, "Order not found")
    logger.info("order_display_ready company_id=%s order_id=%s status=%s", company_id, order.get("id", ""), order.get("status", ""))
    return order


@router.patch("/orders/{order_id}/status")
async def patch_order_status(
    request: Request,
    order_id: str,
    payload: dict | None = Body(default=None),
):
    cu = await require_roles(request, ["admin", "company_agent"])
    company_id = get_company_id(cu)
    if not company_id:
        raise HTTPException(401, "Invalid tenant context")
    status = _text((payload or {}).get("status"), 40)
    if not status:
        raise HTTPException(400, "Missing order status")
    try:
        order = await update_order_status(
            _db(request),
            company_id=company_id,
            order_id=_text(order_id, 120),
            status=status,
            actor_user_id=_text(cu.get("sub"), 120),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not order:
        raise HTTPException(404, "Order not found")
    return order


@router.get("/conversations/{conversation_id}/orders")
async def list_conversation_orders(request: Request, conversation_id: str):
    cu = await get_current_user_flexible(request)
    company_id = get_company_id(cu)
    if not company_id:
        raise HTTPException(401, "Invalid tenant context")
    db = _db(request)
    rows = await db.fetch(
        "SELECT * FROM orders WHERE company_id=$1 AND conversation_id=$2 ORDER BY created_at DESC LIMIT 50",
        company_id,
        _text(conversation_id, 120),
    )
    from services.order_service import serialize_order

    return {"items": [serialize_order(dict(row)) for row in rows or []]}
