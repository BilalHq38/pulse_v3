from __future__ import annotations

import json
import logging
import re
import datetime as _dt
from datetime import datetime
from typing import Any

from core.utils import make_id
from shared.config import frontend_url
from shared.product_ref_token import create_ref_token
from services.ai_service.routing_guards import route_product_order_intent
from services.db_helpers import create_notification

logger = logging.getLogger(__name__)

ORDER_DRAFT_STATUSES = {"collecting_details", "awaiting_confirmation"}
ORDER_ACTIVE_STATUSES = {"collecting_details", "awaiting_confirmation", "admin_review", "placed", "confirmed"}
ORDER_ALLOWED_STATUSES = {
    "collecting_details",
    "awaiting_confirmation",
    "placed",
    "admin_review",
    "pending",
    "confirmed",
    "shipped",
    "delivered",
    "cancelled",
    "completed",
}
ORDER_MANAGE_STATUSES = {
    "admin_review",
    "placed",
    "confirmed",
    "shipped",
    "delivered",
    "cancelled",
    "completed",
}

ORDER_REQUIRED_FIELDS = (
    "product",
    "quantity",
    "customer_name",
    "customer_email",
    "customer_phone",
    "delivery_address",
)
ORDER_SELECTION_STATE = "awaiting_product_selection"
ORDER_PRODUCT_BATCH_SIZE = 6

_COLORS = {
    "black",
    "white",
    "red",
    "blue",
    "green",
    "yellow",
    "pink",
    "purple",
    "grey",
    "gray",
    "brown",
    "orange",
    "silver",
    "gold",
    "navy",
    "maroon",
    "beige",
}
_SIZES = {
    "xs",
    "s",
    "m",
    "l",
    "xl",
    "xxl",
    "small",
    "medium",
    "large",
    "extra small",
    "extra large",
}


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


def _as_dict(row: Any) -> dict:
    if not row:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=True, default=str)


def _json_loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def normalize_order_status(status: str) -> str:
    cleaned = _text(status, 40).lower().replace("-", "_").replace(" ", "_")
    cleaned = {
        "order_complete": "completed",
        "order_completed": "completed",
        "delivery_complete": "delivered",
        "delivery_completed": "delivered",
    }.get(cleaned, cleaned)
    if cleaned not in ORDER_ALLOWED_STATUSES:
        raise ValueError("Invalid order status")
    return cleaned


def is_order_confirmation(text: str) -> bool:
    return route_product_order_intent(text, {"active_order": True}).get("intent") == "order_confirmation"


def is_order_cancel(text: str) -> bool:
    return route_product_order_intent(text, {"active_order": True}).get("intent") == "order_cancel"


def detect_order_intent(
    text: str,
    *,
    has_catalog_context: bool = False,
    has_active_order: bool = False,
) -> bool:
    route = route_product_order_intent(
        text,
        {
            "active_order": has_active_order,
            "has_catalog_context": has_catalog_context,
        },
    )
    return route.get("intent") in {"order_intent", "order_continuation", "order_confirmation", "order_cancel"}


def _normalize_phone(raw: str) -> str:
    value = _text(raw, 80)
    if not value:
        return ""
    leading_plus = value.strip().startswith("+")
    digits = re.sub(r"\D", "", value)
    if len(digits) < 7:
        return ""
    return ("+" if leading_plus else "") + digits[:20]


def _extract_phone(text: str) -> str:
    for match in re.finditer(r"(?:\+?\d[\d\s().-]{5,}\d)", text or ""):
        phone = _normalize_phone(match.group(0))
        if phone:
            return phone
    return ""


def _extract_email(text: str) -> str:
    match = re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", text or "", re.IGNORECASE)
    if not match:
        return ""
    return _text(match.group(0).lower(), 160)


def _extract_quantity(text: str) -> int | None:
    normalized = _text(text, 300).lower()
    patterns = (
        r"\b(?:qty|quantity)\s*[:#-]?\s*(\d{1,3})\b",
        r"\b(?:want|need|order|buy|take|book)\s+(\d{1,3})\b",
        r"\b(\d{1,3})\s*(?:pcs|pieces|items|units|x)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            quantity = int(match.group(1))
            if 0 < quantity <= 999:
                return quantity
    if re.fullmatch(r"\s*\d{1,3}\s*", normalized):
        quantity = int(normalized.strip())
        if 0 < quantity <= 999:
            return quantity
    word_map = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    for word, quantity in word_map.items():
        if re.search(rf"\b(?:want|need|order|buy|take|book)\s+{word}\b", normalized):
            return quantity
    return None


def _extract_name(text: str) -> str:
    cleaned = _text(text, 300)
    patterns = (
        r"\bmy\s+name\s+is\s+([A-Za-z][A-Za-z\s.'-]{1,80})",
        r"\bname\s*[:=-]\s*([A-Za-z][A-Za-z\s.'-]{1,80})",
        r"\bi\s+am\s+([A-Za-z][A-Za-z\s.'-]{1,80})",
    )
    for pattern in patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            value = re.split(
                r"\b(?:email|phone|address|deliver|delivery|quantity|qty)\b",
                match.group(1),
                flags=re.IGNORECASE,
            )[0]
            return _text(value, 100)
    return ""


def _extract_address(text: str) -> str:
    cleaned = _text(text, 800)
    patterns = (
        r"\b(?:deliver|delivery|ship|send)\s+(?:to|at)\s+(.+)",
        r"\b(?:address|delivery\s+address)\s*(?:is|:|-)?\s+(.+)",
    )
    for pattern in patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            address = match.group(1).strip()
            address = re.split(r"\b(?:email|phone|qty|quantity|name)\b", address, flags=re.IGNORECASE)[0].strip(" ,.-")
            if len(address) >= 8:
                return _text(address, 500)
    lower = cleaned.lower()
    address_terms = (
        "house",
        "street",
        "road",
        "block",
        "sector",
        "apartment",
        "flat",
        "near",
        "phase",
        "floor",
        "islamabad",
        "karachi",
        "lahore",
    )
    if len(cleaned) >= 12 and ("," in cleaned or any(term in lower for term in address_terms)):
        if not is_order_confirmation(cleaned) and not re.fullmatch(r"[\d\s+().-]+", cleaned):
            return _text(cleaned, 500)
    return ""


def _extract_variant(text: str) -> tuple[str, str, str]:
    lowered = re.sub(r"\s+", " ", _text(text, 400).lower()).strip()
    color = ""
    size = ""
    for candidate in sorted(_COLORS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(candidate)}\b", lowered):
            color = candidate
            break
    for candidate in sorted(_SIZES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(candidate)}\b", lowered):
            size = candidate.upper() if len(candidate) <= 3 else candidate
            break
    variant = " / ".join(part for part in (size, color) if part)
    return variant, size, color


def _extract_product_name_from_text(text: str) -> str:
    cleaned = _text(text, 300)
    patterns = (
        r"\bproduct\s*(?:is|:|-)\s*([A-Za-z0-9][A-Za-z0-9\s&.'/-]{1,100})",
        r"\bitem\s*(?:is|:|-)\s*([A-Za-z0-9][A-Za-z0-9\s&.'/-]{1,100})",
    )
    for pattern in patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            value = re.split(r"\b(?:qty|quantity|address|phone|name|size|color|colour)\b", match.group(1), flags=re.IGNORECASE)[0]
            return _text(value, 160)
    return ""


def extract_order_details(text: str, *, customer_info: dict | None = None, product_context: dict | None = None) -> dict:
    customer = dict(customer_info or {})
    product = dict(product_context or {})
    details: dict[str, Any] = {}

    quantity = _extract_quantity(text)
    if quantity is not None:
        details["quantity"] = quantity
    phone = _extract_phone(text)
    if phone:
        details["customer_phone"] = phone
    email = _extract_email(text)
    if email:
        details["customer_email"] = email
    name = _extract_name(text)
    if name:
        details["customer_name"] = name
    address = _extract_address(text)
    if address:
        details["delivery_address"] = address
    product_name = _extract_product_name_from_text(text)
    if not product_name and product.get("product_name"):
        product_name = _text(product.get("product_name"), 240)
    product_id = _text(product.get("product_id"), 120)
    if product_name:
        details["product_name"] = product_name
    if product_id:
        details["product_id"] = product_id

    if not details.get("customer_name"):
        existing_name = _text(customer.get("name") or customer.get("customer_name"), 120)
        if existing_name and existing_name.lower() not in {"customer", "unknown"}:
            details["customer_name"] = existing_name
    if not details.get("customer_phone"):
        existing_phone = _normalize_phone(_text(customer.get("phone") or customer.get("mobile_number"), 80))
        if existing_phone:
            details["customer_phone"] = existing_phone
    if not details.get("customer_email"):
        existing_email = _extract_email(_text(customer.get("email"), 160))
        if existing_email:
            details["customer_email"] = existing_email
    if not details.get("delivery_address"):
        existing_address = _text(customer.get("address"), 500)
        if existing_address:
            details["delivery_address"] = existing_address
    return details


def merge_order_details(existing: dict, details: dict) -> dict:
    merged = dict(existing or {})
    for key in (
        "product_id",
        "product_name",
        "quantity",
        "customer_name",
        "customer_email",
        "customer_phone",
        "delivery_address",
        "notes",
        "purchase_link",
    ):
        value = details.get(key)
        if value not in (None, "", []):
            merged[key] = value
    return merged


def missing_order_fields(order: dict) -> list[str]:
    missing: list[str] = []
    if not (_text(order.get("product_id")) or _text(order.get("product_name"))):
        missing.append("product")
    if not order.get("quantity"):
        missing.append("quantity")
    if not _text(order.get("customer_name")):
        missing.append("customer_name")
    if not _text(order.get("customer_email")):
        missing.append("customer_email")
    if not _text(order.get("customer_phone")):
        missing.append("customer_phone")
    if not _text(order.get("delivery_address")):
        missing.append("delivery_address")
    return missing


def format_missing_fields_reply(missing: list[str], order: dict | None = None) -> str:
    order = dict(order or {})
    labels = {
        "product": "product",
        "quantity": "quantity",
        "customer_name": "your name",
        "customer_email": "email address",
        "customer_phone": "phone number",
        "delivery_address": "delivery address",
    }
    visible = [labels.get(item, item.replace("_", " ")) for item in missing]
    if not visible:
        return ""
    product_name = _text(order.get("product_name"), 240)
    if product_name and len(missing) >= 3:
        response = (
            f"Sure, I can help you place the order for *{product_name}*. "
            "Please share the following details:\n\n"
            "Name:\n"
            "Email:\n"
            "Phone:\n"
            "Delivery Address:\n"
            "Quantity:"
        )
        return _append_purchase_link(response, order)
    if len(visible) == 1:
        needed = visible[0]
    elif len(visible) == 2:
        needed = f"{visible[0]} and {visible[1]}"
    else:
        needed = ", ".join(visible[:-1]) + f", and {visible[-1]}"
    return _append_purchase_link(f"Sure, I can help place the order. Please share {needed} to complete it.", order)


def format_confirmation_reply(order: dict) -> str:
    product = _text(order.get("product_name") or "Selected product", 240)
    quantity = order.get("quantity") or 1
    name = _text(order.get("customer_name") or "Not provided", 160)
    email = _text(order.get("customer_email") or "Not provided", 160)
    phone = _text(order.get("customer_phone") or "Not provided", 80)
    address = _text(order.get("delivery_address") or "Not provided", 500)
    lines = [
        "Please confirm your order:",
        f"Product: {product}",
        f"Name: {name}",
        f"Email: {email}",
        f"Phone: {phone}",
        f"Delivery Address: {address}",
        f"Quantity: {quantity}",
    ]
    lines.append("Reply 'Confirm' to place the order, or tell me what you want to change.")
    return _append_purchase_link("\n".join(lines), order)


def format_order_placed_reply(order: dict) -> str:
    order_id = _text(order.get("id"), 120) or "pending"
    return f"Your order has been placed successfully. Your order ID is {order_id}. Our team will contact you soon."


def _order_response_payload(
    response: str,
    *,
    order: dict | None = None,
    next_action: str = "send_response",
    route: str = "order_flow",
    routing: dict | None = None,
) -> dict:
    order = dict(order or {})
    routing = dict(routing or {})
    purchase_link = _purchase_link_from_order(order)
    return {
        "response": response,
        "confidence": 0.98,
        "attachments": [],
        "product_images": [],
        "product_ids": [order.get("product_id")] if order.get("product_id") else [],
        "product_links": [
            {
                "product_id": order.get("product_id", ""),
                "url": purchase_link,
                "name": order.get("product_name", ""),
                "image_url": "",
            }
        ] if purchase_link else [],
        "llm_id": "",
        "provider": "rule",
        "model_name": "deterministic-order-flow",
        "intent_name": "order_intent",
        "route": route,
        "neutral_route": route,
        "routing": routing,
        "conversation_stage": "order_flow",
        "next_action": next_action,
        "next_step": "",
        "rag_called": False,
        "api_error": False,
        "provider_error": {},
        "degraded": False,
        "error_type": "",
        "error_reason": "",
        "fallback_used": False,
        "order_id": order.get("id", ""),
        "order_status": order.get("status", ""),
        "deliver_response": True,
        "escalate": False,
    }


def _metadata_from_attachment(item: dict) -> dict:
    raw = item.get("raw_metadata")
    if raw is None:
        raw = item.get("metadata")
    return _json_loads(raw, {}) if raw is not None else {}


def _dedupe_products(products: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for product in products:
        product_id = _text(product.get("product_id"), 120)
        product_name = _text(product.get("product_name"), 240)
        key = product_id or product_name.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "product_id": product_id,
                "product_name": product_name,
                "price": _text(product.get("price"), 120),
                "price_currency": _text(product.get("price_currency"), 20),
                "status": _text(product.get("status"), 40),
            }
        )
    return result


def _products_from_context_messages(conversation_context: list[dict]) -> list[dict]:
    products: list[dict] = []
    for message in reversed(list(conversation_context or [])):
        sender = _text((message or {}).get("sender_type"), 40).lower()
        if sender not in {"ai", "agent"}:
            continue
        attachments = list((message or {}).get("attachments") or [])
        for attachment in reversed(attachments):
            if not isinstance(attachment, dict):
                continue
            metadata = _metadata_from_attachment(attachment)
            raw_metadata = attachment.get("raw_metadata") if isinstance(attachment.get("raw_metadata"), dict) else {}
            product_id = _text(
                metadata.get("product_id") or attachment.get("product_id") or raw_metadata.get("product_id"),
                120,
            )
            product_name = _text(
                metadata.get("product_name")
                or metadata.get("name")
                or attachment.get("product_name")
                or attachment.get("caption"),
                240,
            )
            if product_id or product_name:
                products.append({
                    "product_id": product_id,
                    "product_name": product_name,
                    "purchase_link": _text(metadata.get("public_url") or metadata.get("purchase_link"), 2000),
                    "links": _text(metadata.get("links"), 2000),
                    "slug": _text(metadata.get("slug"), 160),
                })
    return _dedupe_products(products)


def _product_from_context_messages(conversation_context: list[dict]) -> dict:
    products = _products_from_context_messages(conversation_context)
    return products[0] if products else {}


async def _product_from_recent_attachments(db, company_id: str, conversation_id: str) -> dict:
    products = await _products_from_recent_attachments(db, company_id, conversation_id)
    return products[0] if products else {}


async def _products_from_recent_attachments(db, company_id: str, conversation_id: str) -> list[dict]:
    if not (db and company_id and conversation_id):
        return []
    try:
        rows = await db.fetch(
            "SELECT ma.raw_metadata "
            "FROM message_attachments ma "
            "JOIN messages m ON m.id=ma.message_id AND m.company_id=ma.company_id "
            "WHERE ma.company_id=$1 AND ma.conversation_id=$2 AND m.sender_type IN ('ai','agent') "
            "ORDER BY ma.created_at DESC LIMIT 10",
            company_id,
            conversation_id,
        )
    except Exception as exc:
        logger.debug("order_product_attachment_lookup_failed company_id=%s conversation_id=%s error=%s", company_id, conversation_id, exc)
        return []
    products: list[dict] = []
    for row in rows or []:
        metadata = _json_loads(_as_dict(row).get("raw_metadata"), {})
        product_id = _text(metadata.get("product_id"), 120)
        product_name = _text(metadata.get("product_name") or metadata.get("name"), 240)
        if product_id or product_name:
            products.append({
                "product_id": product_id,
                "product_name": product_name,
                "purchase_link": _text(metadata.get("public_url") or metadata.get("purchase_link"), 2000),
                "links": _text(metadata.get("links"), 2000),
                "slug": _text(metadata.get("slug"), 160),
            })
    return _dedupe_products(products)


async def _product_from_ids(db, company_id: str, product_ids: list[str]) -> dict:
    products = await _products_from_ids(db, company_id, product_ids)
    return products[0] if products else {}


async def _products_from_ids(db, company_id: str, product_ids: list[str]) -> list[dict]:
    ids = [str(item).strip() for item in product_ids or [] if str(item).strip()]
    if not (db and company_id and ids):
        return []
    try:
        rows = await db.fetch(
            "SELECT id,name,product_title,price,price_currency,status,slug,links FROM company_products "
            "WHERE company_id=$1 AND id=ANY($2::text[]) "
            "ORDER BY array_position($2::text[], id)",
            company_id,
            ids,
        )
    except Exception as exc:
        logger.debug("order_product_lookup_failed company_id=%s product_ids=%s error=%s", company_id, ids[:3], exc)
        return []
    products = []
    for row in rows or []:
        product = _as_dict(row)
        if product:
            products.append({
                "product_id": _text(product.get("id"), 120),
                "product_name": _text(product.get("name") or product.get("product_title"), 240),
                "price": _text(product.get("price"), 120),
                "price_currency": _text(product.get("price_currency"), 20),
                "status": _text(product.get("status"), 40),
                "slug": _text(product.get("slug"), 160),
                "links": _text(product.get("links"), 2000),
            })
    return _dedupe_products(products)


def _normalize_product_match_text(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9\s]", " ", _text(value, 300).lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    stop_words = {
        "this", "that", "the", "a", "an", "product", "products", "item", "items",
        "one", "ye", "wala", "wali",
        # Generic intent words that are never product names
        "suggest", "suggested", "suggestion", "suggestions",
        "something", "anything", "everything",
        "recommend", "recommended", "recommendation", "recommendations",
        "details", "detail", "info", "information",
        "help", "back", "now", "here", "there", "please",
        "want", "need", "get", "have", "know", "tell", "show",
        "do", "does", "did", "can", "could", "would", "should",
        "i", "you", "we", "us", "me", "my", "your",
        "what", "which", "how", "where", "when",
    }
    return " ".join(token for token in normalized.split() if token not in stop_words)


def _score_product_name_match(query: str, product: dict) -> int:
    query_norm = _normalize_product_match_text(query)
    if not query_norm:
        return 0
    names = [
        _normalize_product_match_text(product.get("product_name") or ""),
        _normalize_product_match_text(product.get("name") or ""),
        _normalize_product_match_text(product.get("product_title") or ""),
    ]
    best = 0
    query_tokens = set(query_norm.split())
    for name_norm in names:
        if not name_norm:
            continue
        if query_norm == name_norm:
            best = max(best, 100)
        elif query_norm in name_norm or name_norm in query_norm:
            best = max(best, 88)
        else:
            name_tokens = set(name_norm.split())
            overlap = len(query_tokens & name_tokens)
            if overlap and query_tokens:
                best = max(best, int((overlap / len(query_tokens)) * 72))
                if len(query_tokens) <= 3 and any(token.isdigit() for token in query_tokens):
                    non_numeric = {token for token in query_tokens if not token.isdigit()}
                    if non_numeric and non_numeric <= name_tokens:
                        best = max(best, 82)
    return best


async def resolve_order_product_matches(db, company_id: str, product_text: str) -> list[dict]:
    product_text = _normalize_product_match_text(product_text)
    if not (db and company_id and product_text):
        return []
    try:
        rows = await db.fetch(
            "SELECT id,name,product_title,price,price_currency,status,slug,links "
            "FROM company_products "
            "WHERE company_id=$1 AND (status='active' OR status IS NULL OR status='') "
            "ORDER BY updated_at DESC NULLS LAST, created_at DESC LIMIT 250",
            company_id,
        )
    except Exception as exc:
        logger.debug("order_product_name_lookup_failed company_id=%s product_text=%s error=%s", company_id, product_text[:80], exc)
        return []

    scored: list[tuple[int, dict]] = []
    for row in rows or []:
        product = _as_dict(row)
        product_name = _text(product.get("name") or product.get("product_title"), 240)
        if not product_name:
            continue
        score = _score_product_name_match(product_text, {"product_name": product_name})
        if score >= 62:
            scored.append(
                (
                    score,
                    {
                        "product_id": _text(product.get("id"), 120),
                        "product_name": product_name,
                        "price": _text(product.get("price"), 120),
                        "price_currency": _text(product.get("price_currency"), 20),
                        "status": _text(product.get("status"), 40),
                        "slug": _text(product.get("slug"), 160),
                        "links": _text(product.get("links"), 2000),
                    },
                )
            )
    if not scored:
        return []
    scored.sort(key=lambda item: item[0], reverse=True)
    top_score = scored[0][0]
    return _dedupe_products([product for score, product in scored if score == top_score])


def _product_detail_prefix(routing: dict, product: dict) -> str:
    if not product:
        return ""
    name = _text(product.get("product_name") or "This product", 240)
    parts: list[str] = []
    if routing.get("price_request") and _text(product.get("price")):
        currency = _text(product.get("price_currency") or "USD", 20)
        parts.append(f"{name} is priced at {_text(product.get('price'), 80)} {currency}.")
    if "available" in str(routing.get("matched_terms") or "").lower() or routing.get("reason") == "mixed_product_and_order_terms":
        status = _text(product.get("status"), 40).lower()
        if status in {"active", ""}:
            parts.append(f"{name} is available in the catalog.")
    return " ".join(dict.fromkeys(parts)).strip()


def _price_display(product: dict) -> str:
    price = _text(product.get("price"), 80)
    currency = _text(product.get("price_currency") or "USD", 20)
    return f"{price} {currency}".strip() if price else "Price not listed"


def _append_ref_to_purchase_url(url: str, *, company_id: str, customer_id: str = "", conversation_id: str = "") -> str:
    cleaned = _text(url, 2000)
    if not cleaned or not customer_id or not company_id or "ref=" in cleaned:
        return cleaned
    ref = create_ref_token(
        customer_id=customer_id,
        session_id=conversation_id,
        company_id=company_id,
    )
    sep = "&" if "?" in cleaned else "?"
    return f"{cleaned}{sep}ref={ref}"


def _build_product_purchase_url(
    product: dict,
    *,
    company_slug: str = "",
    company_id: str = "",
    customer_id: str = "",
    conversation_id: str = "",
) -> str:
    manual = _text(
        product.get("purchase_link") or product.get("public_url") or product.get("links") or product.get("url"),
        2000,
    )
    if manual.startswith(("http://", "https://")):
        return _append_ref_to_purchase_url(
            manual,
            company_id=company_id,
            customer_id=customer_id,
            conversation_id=conversation_id,
        )
    slug = _text(product.get("slug"), 160)
    if not (company_slug and slug):
        return ""
    url = f"{frontend_url().rstrip('/')}/c/{company_slug}/product/{slug}"
    return _append_ref_to_purchase_url(
        url,
        company_id=company_id,
        customer_id=customer_id,
        conversation_id=conversation_id,
    )


def _purchase_link_from_order(order: dict | None) -> str:
    order = dict(order or {})
    raw = _json_loads(order.get("raw_details"), {}) if "raw_details" in order else {}
    return _text(order.get("purchase_link") or raw.get("purchase_link"), 2000)


def _append_purchase_link(response: str, order: dict | None) -> str:
    link = _purchase_link_from_order(order)
    if not link or link in response:
        return response
    return f"{response}\n\nYou can complete the purchase here:\n{link}"


def _serialize_product_row(row: Any) -> dict:
    product = _as_dict(row)
    if not product:
        return {}
    return {
        "product_id": _text(product.get("id") or product.get("product_id"), 120),
        "product_name": _text(product.get("name") or product.get("product_name") or product.get("product_title"), 240),
        "price": _text(product.get("price"), 120),
        "price_currency": _text(product.get("price_currency"), 20),
        "status": _text(product.get("status"), 40),
        "category": _text(product.get("category"), 120),
        "description": _text(product.get("description"), 500),
        "slug": _text(product.get("slug"), 160),
        "links": _text(product.get("links"), 2000),
        "purchase_link": _text(product.get("purchase_link") or product.get("public_url"), 2000),
    }


async def fetch_product_batch(
    db,
    company_id: str,
    *,
    limit: int = ORDER_PRODUCT_BATCH_SIZE,
    exclude_ids: list[str] | None = None,
) -> list[dict]:
    excluded = [str(item).strip() for item in (exclude_ids or []) if str(item).strip()]
    if not (db and company_id):
        return []
    try:
        rows = await db.fetch(
            "SELECT id,name,product_title,description,price,price_currency,category,status,slug,links "
            "FROM company_products "
            "WHERE company_id=$1 AND (status='active' OR status IS NULL OR status='') "
            "  AND NOT (id = ANY($2::text[])) "
            "ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST, name ASC LIMIT $3",
            company_id,
            excluded,
            max(1, min(int(limit or ORDER_PRODUCT_BATCH_SIZE), 10)),
        )
    except Exception as exc:
        logger.warning("top_products_lookup_failed company_id=%s error=%s", company_id, exc)
        return []
    products = [_serialize_product_row(row) for row in rows or []]
    return _dedupe_products([product for product in products if product.get("product_id") or product.get("product_name")])


def _shown_products_from_order(order: dict | None) -> list[dict]:
    raw = _json_loads((order or {}).get("raw_details"), {})
    products = raw.get("shown_products") or raw.get("product_options") or []
    if isinstance(products, list):
        return _dedupe_products([dict(item) for item in products if isinstance(item, dict)])
    return []


def _shown_product_ids_from_order(order: dict | None) -> list[str]:
    ids = [product.get("product_id") for product in _shown_products_from_order(order)]
    raw = _json_loads((order or {}).get("raw_details"), {})
    ids.extend(raw.get("shown_product_ids") or [])
    return list(dict.fromkeys([_text(item, 120) for item in ids if _text(item, 120)]))


def _is_more_products_request(text: str) -> bool:
    normalized = re.sub(r"[^a-z0-9\s]", " ", _text(text, 200).lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return any(
        phrase in normalized
        for phrase in (
            "show more",
            "more products",
            "more options",
            "next products",
            "next",
            "aur dikhao",
            "different products",
            "other products",
        )
    )


def _parse_product_selection_index(text: str) -> int | None:
    normalized = re.sub(r"[^a-z0-9\s]", " ", _text(text, 120).lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if re.fullmatch(r"\d{1,2}", normalized):
        return int(normalized)
    match = re.search(r"\b(?:number|option|item|product)\s+(\d{1,2})\b", normalized)
    if match:
        return int(match.group(1))
    words = {
        "first": 1,
        "second": 2,
        "third": 3,
        "fourth": 4,
        "fifth": 5,
        "one": 1,
        "two": 2,
        "three": 3,
        "pehla": 1,
        "dosra": 2,
        "doosra": 2,
        "teesra": 3,
    }
    for word, index in words.items():
        if re.search(rf"\b{re.escape(word)}\b", normalized):
            return index
    return None


def _is_product_selection_only(text: str) -> bool:
    normalized = re.sub(r"[^a-z0-9\s]", " ", _text(text, 120).lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return bool(
        re.fullmatch(r"\d{1,2}", normalized)
        or re.fullmatch(r"(?:number|option|item|product)\s+\d{1,2}", normalized)
        or normalized in {"first", "second", "third", "fourth", "fifth", "one", "two", "three", "pehla", "dosra", "doosra", "teesra"}
    )


def _is_product_change_request(text: str) -> bool:
    normalized = re.sub(r"[^a-z0-9\s]", " ", _text(text, 240).lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return any(
        phrase in normalized
        for phrase in (
            "change product",
            "different product",
            "another product",
            "other product",
            "switch product",
            "wrong product",
            "doosra product",
            "dosra product",
            "dusra product",
            "aur product",
        )
    )


def resolve_product_selection_from_list(text: str, products: list[dict]) -> dict:
    products = _dedupe_products(products or [])
    if not products:
        return {}
    index = _parse_product_selection_index(text)
    if index and 1 <= index <= len(products):
        return dict(products[index - 1])
    normalized = _normalize_product_match_text(text)
    if not normalized:
        return {}
    scored = [(_score_product_name_match(normalized, product), product) for product in products]
    scored = [(score, product) for score, product in scored if score >= 62]
    if not scored:
        return {}
    scored.sort(key=lambda item: item[0], reverse=True)
    top_score = scored[0][0]
    top = [product for score, product in scored if score == top_score]
    return dict(top[0]) if len(top) == 1 else {}


def format_product_selection_list(products: list[dict], *, intro: str = "") -> str:
    products = _dedupe_products(products or [])[:max(ORDER_PRODUCT_BATCH_SIZE, 6)]
    if not products:
        return "I do not see available products in the catalog right now. Our team can still help if you share what you need."
    header = intro.strip() or "Here are the available products:"
    lines = [header, ""]
    for index, product in enumerate(products, start=1):
        name = _text(product.get("product_name") or "Product", 240)
        price = _price_display(product)
        lines.append(f"{index}. {name} — {price}")
    lines.append("")
    lines.append("Reply with the product number or name to continue.")
    return "\n".join(lines)


def _product_not_found_reply(product_text: str) -> str:
    product_text = _text(product_text, 160)
    if product_text:
        return f"I could not find {product_text} in the current catalog. Please reply with another product name or ask to see available products."
    return "Which product would you like to order? Please reply with the product name or ask to see available products."


async def resolve_order_product_context(
    db,
    company_id: str,
    conversation_id: str,
    *,
    conversation_context: list[dict] | None = None,
    shown_product_ids: list[str] | None = None,
    last_response_context: dict | None = None,
) -> dict:
    products = await resolve_order_product_candidates(
        db,
        company_id,
        conversation_id,
        conversation_context=conversation_context,
        shown_product_ids=shown_product_ids,
        last_response_context=last_response_context,
    )
    return products[0] if products else {}


async def resolve_order_product_candidates(
    db,
    company_id: str,
    conversation_id: str,
    *,
    conversation_context: list[dict] | None = None,
    shown_product_ids: list[str] | None = None,
    last_response_context: dict | None = None,
) -> list[dict]:
    products = _products_from_context_messages(conversation_context or [])
    if products:
        return products
    ids = [
        *[str(item).strip() for item in (shown_product_ids or []) if str(item).strip()],
        *[str(item).strip() for item in ((last_response_context or {}).get("product_ids") or []) if str(item).strip()],
    ]
    products = await _products_from_ids(db, company_id, ids)
    if products:
        return products
    return await _products_from_recent_attachments(db, company_id, conversation_id)


async def _hydrate_product_purchase_fields(db, company_id: str, product: dict) -> dict:
    hydrated = dict(product or {})
    if not (db and company_id and hydrated.get("product_id")):
        return hydrated
    if hydrated.get("purchase_link") or hydrated.get("public_url") or hydrated.get("links") or hydrated.get("slug"):
        return hydrated
    try:
        row = await db.fetchrow(
            "SELECT id,name,product_title,slug,links FROM company_products "
            "WHERE company_id=$1 AND id=$2 LIMIT 1",
            company_id,
            _text(hydrated.get("product_id"), 120),
        )
    except Exception as exc:
        logger.debug("order_product_purchase_lookup_failed company_id=%s product_id=%s error=%s", company_id, hydrated.get("product_id"), exc)
        return hydrated
    product = _as_dict(row)
    if not product:
        return hydrated
    hydrated.setdefault("product_name", _text(product.get("name") or product.get("product_title"), 240))
    hydrated["slug"] = _text(product.get("slug"), 160)
    hydrated["links"] = _text(product.get("links"), 2000)
    return hydrated


async def _resolve_company_slug(db, company_id: str) -> str:
    if not (db and company_id):
        return ""
    try:
        row = await db.fetchrow("SELECT slug FROM companies WHERE id=$1 LIMIT 1", company_id)
    except Exception as exc:
        logger.debug("order_company_slug_lookup_failed company_id=%s error=%s", company_id, exc)
        return ""
    return _text(_as_dict(row).get("slug"), 160)


async def fetch_active_order(db, company_id: str, conversation_id: str) -> dict:
    if not (db and company_id and conversation_id):
        return {}
    row = await db.fetchrow(
        "SELECT * FROM orders "
        "WHERE company_id=$1 AND conversation_id=$2 AND status=ANY($3::text[]) "
        "ORDER BY updated_at DESC NULLS LAST, created_at DESC LIMIT 1",
        company_id,
        conversation_id,
        sorted(ORDER_DRAFT_STATUSES),
    )
    return _as_dict(row)


async def fetch_latest_active_order(db, company_id: str, conversation_id: str) -> dict:
    if not (db and company_id and conversation_id):
        return {}
    row = await db.fetchrow(
        "SELECT * FROM orders "
        "WHERE company_id=$1 AND conversation_id=$2 AND status=ANY($3::text[]) "
        "ORDER BY updated_at DESC NULLS LAST, created_at DESC LIMIT 1",
        company_id,
        conversation_id,
        sorted(ORDER_ACTIVE_STATUSES),
    )
    return _as_dict(row)


async def _fetch_conversation_context(db, company_id: str, conversation_id: str) -> dict:
    if not (db and company_id and conversation_id):
        return {}
    try:
        row = await db.fetchrow(
            "SELECT c.id,c.customer_id,c.customer_name,c.channel,c.assigned_to,c.assigned_name, "
            "       cu.name AS customer_record_name,cu.email AS customer_record_email,cu.phone AS customer_record_phone,cu.address AS customer_record_address,cu.lead_id "
            "FROM conversations c "
            "LEFT JOIN customers cu ON cu.company_id=c.company_id AND cu.id=c.customer_id "
            "WHERE c.company_id=$1 AND c.id=$2 LIMIT 1",
            company_id,
            conversation_id,
        )
    except Exception as exc:
        logger.debug("order_conversation_context_lookup_failed company_id=%s conversation_id=%s error=%s", company_id, conversation_id, exc)
        return {}
    return _as_dict(row)


def _order_values_from_record(order: dict) -> dict:
    raw_details = _json_loads(order.get("raw_details"), {})
    return {
        "id": _text(order.get("id"), 120),
        "company_id": _text(order.get("company_id"), 120),
        "conversation_id": _text(order.get("conversation_id"), 120),
        "lead_id": _text(order.get("lead_id"), 120),
        "customer_id": _text(order.get("customer_id"), 120),
        "product_id": _text(order.get("product_id"), 120),
        "product_name": _text(order.get("product_name"), 240),
        "quantity": int(order.get("quantity") or 0) or None,
        "variant": _text(order.get("variant"), 160),
        "size": _text(order.get("size"), 80),
        "color": _text(order.get("color"), 80),
        "customer_name": _text(order.get("customer_name"), 160),
        "customer_email": _text(order.get("customer_email"), 160),
        "customer_phone": _text(order.get("customer_phone"), 80),
        "delivery_address": _text(order.get("delivery_address"), 500),
        "notes": _text(order.get("notes"), 1000),
        "status": _text(order.get("status"), 40) or "collecting_details",
        "source_channel": _text(order.get("source_channel"), 40) or "web_chat",
        "created_by": _text(order.get("created_by"), 40) or "ai",
        "purchase_link": _text(raw_details.get("purchase_link"), 2000),
        "raw_details": raw_details,
        "missing_fields": _json_loads(order.get("missing_fields"), []),
    }


async def create_or_update_order_draft(
    db,
    *,
    company_id: str,
    conversation_id: str,
    customer_id: str = "",
    lead_id: str = "",
    source_channel: str = "web_chat",
    details: dict,
) -> tuple[dict, bool]:
    active = await fetch_active_order(db, company_id, conversation_id)
    if active:
        order = merge_order_details(_order_values_from_record(active), details)
        order["raw_details"] = {**_json_loads(active.get("raw_details"), {}), **dict(details or {})}
        if "quantity" not in details and "quantity" in set(_json_loads(active.get("missing_fields"), [])):
            order["quantity"] = None
        order["status"] = "awaiting_confirmation" if not missing_order_fields(order) else "collecting_details"
        order["missing_fields"] = missing_order_fields(order)
        await db.execute(
            "UPDATE orders SET product_id=$1,product_name=$2,quantity=$3,variant=$4,size=$5,color=$6, "
            "customer_name=$7,customer_email=$8,customer_phone=$9,delivery_address=$10,notes=$11,status=$12, "
            "raw_details=COALESCE(raw_details,'{}'::jsonb) || $13::jsonb, missing_fields=$14::jsonb, updated_at=NOW() "
            "WHERE company_id=$15 AND id=$16",
            order.get("product_id") or "",
            order.get("product_name") or "",
            order.get("quantity"),
            order.get("variant") or "",
            order.get("size") or "",
            order.get("color") or "",
            order.get("customer_name") or "",
            order.get("customer_email") or "",
            order.get("customer_phone") or "",
            order.get("delivery_address") or "",
            order.get("notes") or "",
            order.get("status"),
            _json_dumps(details),
            _json_dumps(order["missing_fields"]),
            company_id,
            order["id"],
        )
        logger.info(
            "order_draft_updated company_id=%s conversation_id=%s order_id=%s status=%s missing_fields=%s",
            company_id,
            conversation_id,
            order["id"],
            order["status"],
            ",".join(order["missing_fields"]),
        )
        return order, False

    order_id = make_id()
    order = merge_order_details(
        {
            "id": order_id,
            "company_id": company_id,
            "conversation_id": conversation_id,
            "customer_id": customer_id,
            "lead_id": lead_id,
            "source_channel": source_channel or "web_chat",
            "created_by": "ai",
        },
        details,
    )
    order["status"] = "awaiting_confirmation" if not missing_order_fields(order) else "collecting_details"
    order["missing_fields"] = missing_order_fields(order)
    order["raw_details"] = dict(details or {})
    await db.execute(
        "INSERT INTO orders(id,company_id,conversation_id,lead_id,customer_id,product_id,product_name,quantity,variant,size,color, "
        "customer_name,customer_email,customer_phone,delivery_address,notes,status,source_channel,created_by,raw_details,missing_fields,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20::jsonb,$21::jsonb,NOW(),NOW())",
        order_id,
        company_id,
        conversation_id,
        lead_id or "",
        customer_id or "",
        order.get("product_id") or "",
        order.get("product_name") or "",
        order.get("quantity"),
        order.get("variant") or "",
        order.get("size") or "",
        order.get("color") or "",
        order.get("customer_name") or "",
        order.get("customer_email") or "",
        order.get("customer_phone") or "",
        order.get("delivery_address") or "",
        order.get("notes") or "",
        order["status"],
        source_channel or "web_chat",
        "ai",
        _json_dumps(details),
        _json_dumps(order["missing_fields"]),
    )
    logger.info(
        "order_draft_created company_id=%s conversation_id=%s order_id=%s status=%s missing_fields=%s",
        company_id,
        conversation_id,
        order_id,
        order["status"],
        ",".join(order["missing_fields"]),
    )
    return order, True


async def place_order(db, company_id: str, order_id: str) -> dict:
    row = await db.fetchrow(
        "UPDATE orders SET status='admin_review', missing_fields='[]'::jsonb, updated_at=NOW() "
        "WHERE company_id=$1 AND id=$2 AND status=ANY($3::text[]) "
        "RETURNING *",
        company_id,
        order_id,
        sorted(ORDER_DRAFT_STATUSES),
    )
    order = _as_dict(row)
    if not order:
        fallback = await db.fetchrow("SELECT o.* FROM orders o WHERE o.company_id=$1 AND o.id=$2 LIMIT 1", company_id, order_id)
        order = _as_dict(fallback)
    logger.info("order_placed company_id=%s order_id=%s status=%s", company_id, order_id, order.get("status", ""))
    return order


async def notify_order_admins(db, order: dict, *, conversation_context: dict | None = None) -> int:
    company_id = _text(order.get("company_id"), 120)
    if not (db and company_id and order.get("id")):
        return 0
    conversation = dict(conversation_context or {})
    assigned_to = _text(conversation.get("assigned_to"), 120)
    try:
        rows = await db.fetch(
            "SELECT id FROM users "
            "WHERE company_id=$1 AND status='active' AND (role=ANY($2::text[]) OR id=$3) "
            "LIMIT 50",
            company_id,
            ["admin", "company_agent"],
            assigned_to,
        )
    except Exception as exc:
        logger.warning("order_admin_notification_user_lookup_failed company_id=%s order_id=%s error=%s", company_id, order.get("id"), exc)
        return 0
    customer_label = _text(order.get("customer_name") or order.get("customer_phone") or conversation.get("customer_name") or "Customer", 160)
    product_name = _text(order.get("product_name") or "a product", 240)
    title = "New order placed"
    body = f"{customer_label} placed an order for {product_name}."
    count = 0
    seen_users: set[str] = set()
    for row in rows or []:
        user_id = _text(_as_dict(row).get("id"), 120)
        if not user_id or user_id in seen_users:
            continue
        seen_users.add(user_id)
        note = await create_notification(
            db,
            None,
            {},
            {"sub": "system", "company_id": company_id},
            title,
            body,
            "order",
            target_user_id=user_id,
            reminder_key=f"order:{order.get('id')}",
            action_url=f"/orders?order={order.get('id')}",
        )
        if note:
            count += 1
    logger.info(
        "order_admin_notified company_id=%s order_id=%s notification_count=%s",
        company_id,
        order.get("id", ""),
        count,
    )
    return count


async def sync_order_contact_records(db, order: dict, *, conversation_context: dict | None = None) -> dict:
    company_id = _text(order.get("company_id"), 120)
    order_id = _text(order.get("id"), 120)
    if not (db and company_id and order_id):
        return dict(order or {})
    synced = dict(order or {})
    conversation = dict(conversation_context or {})
    lead_id = _text(synced.get("lead_id") or conversation.get("lead_id"), 120)
    customer_id = _text(synced.get("customer_id") or conversation.get("customer_id"), 120)
    name = _text(synced.get("customer_name") or conversation.get("customer_name") or "Customer", 160)
    email = _text(synced.get("customer_email"), 160)
    phone = _text(synced.get("customer_phone"), 80)
    address = _text(synced.get("delivery_address"), 500)

    if lead_id:
        try:
            await db.execute(
                "UPDATE leads SET name=COALESCE(NULLIF($3,''), name), email=COALESCE(NULLIF($4,''), email), "
                "phone=COALESCE(NULLIF($5,''), phone), notes=TRIM(BOTH E'\\n' FROM CONCAT(COALESCE(notes,''), E'\\nOrder placed: ', $6)), "
                "updated_at=NOW() WHERE company_id=$1 AND id=$2",
                company_id,
                lead_id,
                name,
                email,
                phone,
                order_id,
            )
            logger.info("lead_updated_from_order company_id=%s lead_id=%s order_id=%s", company_id, lead_id, order_id)
            if email:
                logger.info("lead_email_updated_from_order company_id=%s lead_id=%s order_id=%s", company_id, lead_id, order_id)
            if phone:
                logger.info("lead_phone_updated_from_order company_id=%s lead_id=%s order_id=%s", company_id, lead_id, order_id)
            try:
                await db.execute(
                    "INSERT INTO lead_activities(id,company_id,lead_id,type,content,stage,created_at) "
                    "VALUES($1,$2,$3,'order',$4,'order_placed',NOW())",
                    make_id(),
                    company_id,
                    lead_id,
                    f"Order {order_id} placed for {_text(synced.get('product_name') or 'product', 240)}.",
                )
            except Exception as exc:
                logger.debug("lead_order_activity_skipped company_id=%s lead_id=%s order_id=%s error=%s", company_id, lead_id, order_id, exc)
        except Exception as exc:
            logger.warning("lead_update_from_order_failed company_id=%s lead_id=%s order_id=%s error=%s", company_id, lead_id, order_id, exc)

    if not customer_id and (email or phone):
        phone_digits = re.sub(r"\D", "", phone)
        try:
            row = await db.fetchrow(
                "SELECT id FROM customers WHERE company_id=$1 AND ("
                "($2<>'' AND LOWER(email)=LOWER($2)) OR ($3<>'' AND regexp_replace(phone, '\\D', '', 'g')=$3)"
                ") ORDER BY updated_at DESC NULLS LAST, created_at DESC LIMIT 1",
                company_id,
                email,
                phone_digits,
            )
            customer_id = _text(_as_dict(row).get("id"), 120)
        except Exception as exc:
            logger.debug("customer_lookup_for_order_failed company_id=%s order_id=%s error=%s", company_id, order_id, exc)

    if customer_id:
        try:
            await db.execute(
                "UPDATE customers SET lead_id=COALESCE(NULLIF($3,''), lead_id), name=COALESCE(NULLIF($4,''), name), "
                "email=COALESCE(NULLIF($5,''), email), phone=COALESCE(NULLIF($6,''), phone), "
                "address=COALESCE(NULLIF($7,''), address), lifecycle_stage='customer', updated_at=NOW() "
                "WHERE company_id=$1 AND id=$2",
                company_id,
                customer_id,
                lead_id,
                name,
                email,
                phone,
                address,
            )
            logger.info("customer_linked_to_order company_id=%s customer_id=%s order_id=%s", company_id, customer_id, order_id)
        except Exception as exc:
            logger.warning("customer_update_from_order_failed company_id=%s customer_id=%s order_id=%s error=%s", company_id, customer_id, order_id, exc)
    elif name or email or phone:
        customer_id = make_id()
        try:
            await db.execute(
                "INSERT INTO customers(id,company_id,lead_id,name,email,phone,address,lifecycle_stage,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,$5,$6,$7,'customer',NOW(),NOW())",
                customer_id,
                company_id,
                lead_id,
                name or phone or email or "Customer",
                email,
                phone,
                address,
            )
            logger.info("lead_converted_to_customer company_id=%s lead_id=%s customer_id=%s order_id=%s", company_id, lead_id, customer_id, order_id)
        except Exception as exc:
            logger.warning("customer_create_from_order_failed company_id=%s order_id=%s error=%s", company_id, order_id, exc)
            customer_id = ""

    # Lead/customer integrity: once the customer record exists, mark the
    # originating lead row as converted so the same person doesn't keep
    # showing in the default leads list. Idempotent — re-running on a
    # converted lead is a no-op.
    if customer_id and lead_id:
        try:
            await db.execute(
                "UPDATE leads SET status='converted', updated_at=NOW() "
                "WHERE id=$1 AND company_id=$2 AND status <> 'converted'",
                lead_id,
                company_id,
            )
        except Exception as exc:
            logger.warning(
                "lead_status_convert_failed company_id=%s lead_id=%s error=%s",
                company_id, lead_id, exc,
            )

    if customer_id:
        synced["customer_id"] = customer_id
        try:
            await db.execute(
                "UPDATE orders SET customer_id=$1, updated_at=NOW() WHERE company_id=$2 AND id=$3",
                customer_id,
                company_id,
                order_id,
            )
            if _text(synced.get("conversation_id"), 120):
                await db.execute(
                    "UPDATE conversations SET customer_id=$1, customer_name=COALESCE(NULLIF($2,''), customer_name), updated_at=NOW() "
                    "WHERE company_id=$3 AND id=$4",
                    customer_id,
                    name,
                    company_id,
                    _text(synced.get("conversation_id"), 120),
                )
        except Exception as exc:
            logger.debug("order_customer_link_update_skipped company_id=%s order_id=%s customer_id=%s error=%s", company_id, order_id, customer_id, exc)

    return synced


def _is_manual_source(source: str, message_id: str, metadata: dict | None = None) -> bool:
    metadata = dict(metadata or {})
    values = {
        _text(source, 120),
        _text(message_id, 200),
        _text(metadata.get("source"), 120),
        _text(metadata.get("message_id"), 200),
        _text(metadata.get("idempotency_key"), 200),
    }
    return any("manual_ai_respond" in value or "manual_ai_draft" in value for value in values if value)


async def handle_order_flow(
    *,
    db,
    company_id: str,
    conversation_id: str,
    message_text: str,
    customer_info: dict | None = None,
    lead: dict | None = None,
    conversation_context: list[dict] | None = None,
    shown_product_ids: list[str] | None = None,
    last_response_context: dict | None = None,
    previous_state: dict | None = None,
    source_channel: str = "web_chat",
    source: str = "",
    actor_user_id: str = "",
    message_id: str = "",
    metadata: dict | None = None,
) -> dict | None:
    del actor_user_id
    company_id = _text(company_id, 120)
    conversation_id = _text(conversation_id, 120)
    if not (db and company_id and conversation_id):
        return None
    if _is_manual_source(source, message_id, metadata):
        logger.info(
            "order_flow_skipped_manual_source company_id=%s conversation_id=%s message_id=%s source=%s",
            company_id,
            conversation_id,
            _text(message_id, 120),
            _text(source, 120),
        )
        return None

    customer = dict(customer_info or {})
    lead_info = dict(lead or {})
    active_order = await fetch_active_order(db, company_id, conversation_id)
    latest_order = active_order or await fetch_latest_active_order(db, company_id, conversation_id)
    logger.info(
        "intent_routing_started company_id=%s conversation_id=%s message_id=%s active_order_id=%s",
        company_id,
        conversation_id,
        _text(message_id, 120),
        _text(active_order.get("id"), 120),
    )
    product_candidates = await resolve_order_product_candidates(
        db,
        company_id,
        conversation_id,
        conversation_context=conversation_context or [],
        shown_product_ids=shown_product_ids or [],
        last_response_context=last_response_context or {},
    )
    active_shown_products = _shown_products_from_order(active_order)
    if active_shown_products and not product_candidates:
        product_candidates = active_shown_products
    active_product = {}
    if latest_order and (_text(latest_order.get("product_id"), 120) or _text(latest_order.get("product_name"), 240)):
        active_product = {
            "product_id": _text(latest_order.get("product_id"), 120),
            "product_name": _text(latest_order.get("product_name"), 240),
            "price": _text(latest_order.get("price"), 120),
            "price_currency": _text(latest_order.get("price_currency"), 20),
            "status": _text(latest_order.get("status"), 40),
        }
    candidate_product_context = product_candidates[0] if product_candidates else {}
    previous_stage = _text((previous_state or {}).get("stage") or (previous_state or {}).get("order_state"), 80)
    product_count = len(product_candidates) or len([item for item in shown_product_ids or [] if str(item).strip()])
    has_catalog_context = bool(candidate_product_context or shown_product_ids or (last_response_context or {}).get("product_ids") or previous_stage in {"catalog_shown", "awaiting_order_intent", "recommendation"})
    has_active_draft = bool(active_order)
    route = route_product_order_intent(
        message_text,
        {
            "active_order": has_active_draft,
            "active_order_id": active_order.get("id", ""),
            "has_catalog_context": has_catalog_context,
            "product_context_present": bool(candidate_product_context),
            "shown_product_count": product_count,
            "product_images_recent": bool(product_candidates),
        },
    )
    route_intent = str(route.get("intent") or "")
    logger.info(
        "intent_routing_result company_id=%s conversation_id=%s message_id=%s route_selected=%s intent=%s confidence=%s reason=%s matched_terms=%s active_order_id=%s product_id=%s product_name=%s",
        company_id,
        conversation_id,
        _text(message_id, 120),
        route_intent,
        route_intent,
        str(route.get("confidence") or ""),
        str(route.get("reason") or ""),
        ",".join(str(item) for item in route.get("matched_terms", [])[:6]),
        _text(active_order.get("id"), 120),
        _text(candidate_product_context.get("product_id"), 120),
        _text(candidate_product_context.get("product_name"), 120),
    )

    if route_intent == "product_query":
        logger.info(
            "product_intent_detected company_id=%s conversation_id=%s message_id=%s reason=%s matched_terms=%s",
            company_id,
            conversation_id,
            _text(message_id, 120),
            str(route.get("reason") or ""),
            ",".join(str(item) for item in route.get("matched_terms", [])[:6]),
        )
        return None
    product_context: dict = dict(active_product)
    explicit_product_text = _text(route.get("extracted_product_text"), 240)
    # If extracted product text is actually a generic catalog query, discard it
    # so the system shows the product list instead of returning product not found
    _generic_intent_words = {
        "know", "about", "offering", "offer", "options", "available", "catalog",
        "products", "product", "services", "service", "items", "item", "collection",
        "list", "see", "get", "tell", "show", "all", "what", "your", "you",
        "want", "need", "order", "buy", "purchase",
        # Extended: common non-product words that the extractor may incorrectly pull
        "suggest", "suggested", "suggestion", "suggestions",
        "recommend", "recommended", "recommendation", "recommendations",
        "something", "anything", "everything", "nothing",
        "help", "assist", "assistance",
        "back", "later", "now", "here", "there",
        "details", "detail", "info", "information",
        "more", "some", "any", "another", "other", "please",
        "do", "does", "did", "can", "could", "would", "should",
        "i", "me", "my", "we", "us", "our",
        "have", "has", "had", "is", "are", "was", "were",
    }
    if explicit_product_text:
        _ept_tokens = set(re.sub(r"[^a-z0-9\s]", " ", explicit_product_text.lower()).split())
        _meaningful = _ept_tokens - _generic_intent_words
        if not _meaningful:
            logger.info(
                "extracted_product_text_discarded_as_generic company_id=%s conversation_id=%s text=%s",
                company_id,
                conversation_id,
                explicit_product_text,
            )
            explicit_product_text = ""
    selected_from_list = resolve_product_selection_from_list(message_text, active_shown_products or product_candidates)
    product_change_requested = bool(active_order and _is_product_change_request(message_text) and not selected_from_list)
    if selected_from_list:
        product_context = selected_from_list
        logger.info(
            "product_selection_resolved company_id=%s conversation_id=%s message_id=%s product_id=%s product_name=%s source=recent_list",
            company_id,
            conversation_id,
            _text(message_id, 120),
            _text(product_context.get("product_id"), 120),
            _text(product_context.get("product_name"), 120),
        )
        logger.info(
            "order_product_selected company_id=%s conversation_id=%s message_id=%s product_id=%s product_name=%s",
            company_id,
            conversation_id,
            _text(message_id, 120),
            _text(product_context.get("product_id"), 120),
            _text(product_context.get("product_name"), 120),
        )
    elif product_change_requested and not explicit_product_text:
        product_context = {}
        logger.info(
            "order_product_change_requested company_id=%s conversation_id=%s message_id=%s active_order_id=%s",
            company_id,
            conversation_id,
            _text(message_id, 120),
            _text(active_order.get("id"), 120),
        )
    elif explicit_product_text:
        logger.info(
            "product_name_extracted company_id=%s conversation_id=%s message_id=%s extracted_product_text=%s",
            company_id,
            conversation_id,
            _text(message_id, 120),
            explicit_product_text,
        )
        matches = await resolve_order_product_matches(db, company_id, explicit_product_text)
        if len(matches) == 1:
            product_context = matches[0]
            logger.info(
                "product_match_found company_id=%s conversation_id=%s message_id=%s product_id=%s product_name=%s",
                company_id,
                conversation_id,
                _text(message_id, 120),
                _text(product_context.get("product_id"), 120),
                _text(product_context.get("product_name"), 120),
            )
        elif len(matches) > 1 and route_intent in {"order_intent", "order_continuation", "clarification_needed"}:
            logger.info(
                "product_match_ambiguous company_id=%s conversation_id=%s message_id=%s extracted_product_text=%s match_count=%s",
                company_id,
                conversation_id,
                _text(message_id, 120),
                explicit_product_text,
                len(matches),
            )
            options = format_product_selection_list(matches, intro="I found multiple matching products. Which one would you like to order?")
            return _order_response_payload(options, order=active_order or {}, next_action="clarify_selected_product", route="clarification_needed", routing=route)
        elif route_intent in {"order_intent", "order_continuation", "clarification_needed"}:
            logger.info(
                "product_match_missing company_id=%s conversation_id=%s message_id=%s extracted_product_text=%s",
                company_id,
                conversation_id,
                _text(message_id, 120),
                explicit_product_text,
            )
            return _order_response_payload(_product_not_found_reply(explicit_product_text), order=active_order or {}, next_action="clarify_selected_product", route="clarification_needed", routing=route)
    elif route.get("selected_item_reference") and product_candidates:
        if len(product_candidates) == 1:
            product_context = candidate_product_context
            logger.info(
                "product_selection_resolved company_id=%s conversation_id=%s message_id=%s product_id=%s product_name=%s source=single_recent_context",
                company_id,
                conversation_id,
                _text(message_id, 120),
                _text(product_context.get("product_id"), 120),
                _text(product_context.get("product_name"), 120),
            )
    elif route_intent in {"order_intent", "order_continuation"} and product_candidates and len(product_candidates) == 1:
        if re.search(r"\b(?:this|it|ye|yahi|isko)\b", _text(message_text, 500).lower()):
            product_context = candidate_product_context
            logger.info(
                "product_selection_resolved company_id=%s conversation_id=%s message_id=%s product_id=%s product_name=%s source=single_recent_reference",
                company_id,
                conversation_id,
                _text(message_id, 120),
                _text(product_context.get("product_id"), 120),
                _text(product_context.get("product_name"), 120),
            )
    elif active_order and not explicit_product_text:
        normalized_tokens = _normalize_product_match_text(message_text).split()
        # Only attempt product name lookup if the remaining tokens after stop word removal
        # look like an actual product name, not generic intent/query words.
        _match_skip_words = {
            "suggest", "suggested", "something", "anything", "everything", "recommend",
            "recommended", "details", "info", "information", "help", "back", "now",
            "more", "please", "tell", "show", "see", "know", "about",
        }
        _candidate_tokens = [token for token in normalized_tokens if token not in _match_skip_words]
        if (
            1 <= len(normalized_tokens) <= 5
            and _candidate_tokens
            and not (set(normalized_tokens) & {"confirm", "cancel", "address", "phone", "email", "quantity"})
        ):
            matches = await resolve_order_product_matches(db, company_id, " ".join(_candidate_tokens))
            if len(matches) == 1:
                product_context = matches[0]
                logger.info(
                    "product_selection_resolved company_id=%s conversation_id=%s message_id=%s product_id=%s product_name=%s source=active_order_name_match",
                    company_id,
                    conversation_id,
                    _text(message_id, 120),
                    _text(product_context.get("product_id"), 120),
                    _text(product_context.get("product_name"), 120),
                )

    if route_intent in {"order_intent", "order_continuation", "order_confirmation", "order_cancel", "clarification_needed"}:
        if route.get("needs_clarification") or str(route.get("reason") or "").startswith(("selected_", "order_reference", "confirmation_without")):
            logger.info(
                "ambiguous_intent_resolved company_id=%s conversation_id=%s message_id=%s route_selected=%s reason=%s needs_clarification=%s",
                company_id,
                conversation_id,
                _text(message_id, 120),
                route_intent,
                str(route.get("reason") or ""),
                bool(route.get("needs_clarification")),
            )
        logger.info(
            "order_route_priority_selected company_id=%s conversation_id=%s message_id=%s route_selected=%s reason=%s",
            company_id,
            conversation_id,
            _text(message_id, 120),
            route_intent,
            str(route.get("reason") or ""),
        )
        if product_candidates:
            logger.info(
                "selected_product_resolution_started company_id=%s conversation_id=%s message_id=%s candidate_count=%s",
                company_id,
                conversation_id,
                _text(message_id, 120),
                len(product_candidates),
            )
            logger.info(
                "selected_product_resolved company_id=%s conversation_id=%s message_id=%s product_id=%s product_name=%s candidate_count=%s",
                company_id,
                conversation_id,
                _text(message_id, 120),
                _text(product_context.get("product_id"), 120),
                _text(product_context.get("product_name"), 120),
                len(product_candidates),
            )
        if route_intent == "clarification_needed" and not has_active_draft and not latest_order:
            logger.info(
                "selected_product_clarification_requested company_id=%s conversation_id=%s message_id=%s reason=%s candidate_count=%s",
                company_id,
                conversation_id,
                _text(message_id, 120),
                str(route.get("reason") or ""),
                len(product_candidates),
            )
            return _order_response_payload(
                "Which product would you like to order? Please share the product name or item number.",
                order={},
                next_action="clarify_selected_product",
            )
        if route_intent != "product_query":
            logger.info(
                "catalog_resend_skipped_due_to_order_intent company_id=%s conversation_id=%s message_id=%s intent=%s reason=%s",
                company_id,
                conversation_id,
                _text(message_id, 120),
                route_intent,
                str(route.get("reason") or ""),
            )

    if route_intent not in {"order_intent", "order_continuation", "order_confirmation", "order_cancel"} and not (
        latest_order and is_order_confirmation(message_text)
    ):
        return None

    logger.info(
        "order_intent_detected company_id=%s conversation_id=%s active_order=%s catalog_context=%s source_channel=%s",
        company_id,
        conversation_id,
        bool(active_order),
        has_catalog_context,
        source_channel or "web_chat",
    )
    logger.info(
        "order_flow_message_consumed company_id=%s conversation_id=%s message_id=%s route=%s active_order_id=%s",
        company_id,
        conversation_id,
        _text(message_id, 120),
        route_intent,
        _text(active_order.get("id"), 120),
    )

    if latest_order and latest_order.get("status") in {"admin_review", "placed", "confirmed"} and not active_order:
        # Only block duplicate order if the previous order was placed very recently (within 2 hours).
        # If the order is older than 2 hours, the user is allowed to place a new order.
        _recent_block = False
        _order_updated = latest_order.get("updated_at") or latest_order.get("created_at")
        if _order_updated:
            try:
                if isinstance(_order_updated, str):
                    _order_updated = _dt.datetime.fromisoformat(_order_updated.replace("Z", "+00:00"))
                _now = _dt.datetime.now(_dt.timezone.utc)
                if _order_updated.tzinfo is None:
                    _order_updated = _order_updated.replace(tzinfo=_dt.timezone.utc)
                _age_hours = (_now - _order_updated).total_seconds() / 3600
                _recent_block = _age_hours < 2.0
            except Exception:
                _recent_block = False
        if _recent_block:
            logger.info(
                "order_duplicate_prevented company_id=%s conversation_id=%s order_id=%s status=%s age_check=recent",
                company_id,
                conversation_id,
                latest_order.get("id", ""),
                latest_order.get("status", ""),
            )
            return _order_response_payload(
                "Your order request is already with our team. We will contact you shortly.",
                order=latest_order,
                next_action="order_already_placed",
            )
        else:
            logger.info(
                "order_new_allowed_after_completed company_id=%s conversation_id=%s previous_order_id=%s previous_status=%s",
                company_id,
                conversation_id,
                latest_order.get("id", ""),
                latest_order.get("status", ""),
            )

    if active_order and route_intent == "order_cancel":
        await db.execute(
            "UPDATE orders SET status='cancelled', updated_at=NOW() WHERE company_id=$1 AND id=$2",
            company_id,
            active_order["id"],
        )
        active_order["status"] = "cancelled"
        logger.info("order_status_updated company_id=%s order_id=%s status=cancelled source=customer", company_id, active_order["id"])
        return _order_response_payload("No problem, I have cancelled this order request.", order=active_order, next_action="order_cancelled")

    conversation = await _fetch_conversation_context(db, company_id, conversation_id)
    if not customer.get("name"):
        customer["name"] = conversation.get("customer_record_name") or conversation.get("customer_name") or ""
    if not customer.get("phone"):
        customer["phone"] = conversation.get("customer_record_phone") or ""
    if not customer.get("email"):
        customer["email"] = conversation.get("customer_record_email") or ""
    if not customer.get("address"):
        customer["address"] = conversation.get("customer_record_address") or ""
    customer_id = _text(customer.get("id") or conversation.get("customer_id"), 120)
    lead_id = _text(lead_info.get("id") or customer.get("lead_id") or conversation.get("lead_id"), 120)
    channel = _text(source_channel or conversation.get("channel") or "web_chat", 40)

    if product_context:
        product_context = await _hydrate_product_purchase_fields(db, company_id, product_context)
        company_slug = ""
        if not (
            product_context.get("purchase_link")
            or product_context.get("public_url")
            or product_context.get("links")
        ):
            company_slug = await _resolve_company_slug(db, company_id)
        purchase_link = _build_product_purchase_url(
            product_context,
            company_slug=company_slug,
            company_id=company_id,
            customer_id=customer_id,
            conversation_id=conversation_id,
        )
        if purchase_link:
            product_context["purchase_link"] = purchase_link

    details = extract_order_details(message_text, customer_info=customer, product_context=product_context)
    if selected_from_list and _is_product_selection_only(message_text):
        details.pop("quantity", None)
    if product_context:
        details["product_id"] = _text(product_context.get("product_id"), 120)
        details["product_name"] = _text(product_context.get("product_name"), 240)
        if product_context.get("purchase_link"):
            details["purchase_link"] = _text(product_context.get("purchase_link"), 2000)

    order_before_update = active_order or latest_order or {}
    needs_product_selection = bool(product_change_requested) or not (
        _text(details.get("product_id"), 120)
        or _text(details.get("product_name"), 240)
        or _text(order_before_update.get("product_id"), 120)
        or _text(order_before_update.get("product_name"), 240)
    )
    if needs_product_selection and route_intent in {"order_intent", "order_continuation", "order_confirmation"}:
        exclude_ids = _shown_product_ids_from_order(active_order) if _is_more_products_request(message_text) else []
        products = await fetch_product_batch(db, company_id, limit=ORDER_PRODUCT_BATCH_SIZE, exclude_ids=exclude_ids)
        details.update(
            {
                "order_state": ORDER_SELECTION_STATE,
                "shown_products": products,
                "shown_product_ids": [product.get("product_id") for product in products if product.get("product_id")],
            }
        )
        order, created = await create_or_update_order_draft(
            db,
            company_id=company_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            lead_id=lead_id,
            source_channel=channel,
            details=details,
        )
        if product_change_requested:
            await db.execute(
                "UPDATE orders SET product_id='', product_name='', status='collecting_details', updated_at=NOW() "
                "WHERE company_id=$1 AND id=$2",
                company_id,
                order["id"],
            )
            order["product_id"] = ""
            order["product_name"] = ""
            order["status"] = "collecting_details"
        logger.info(
            "%s company_id=%s conversation_id=%s order_id=%s product_count=%s excluded_count=%s",
            "next_product_batch_selected" if exclude_ids else "top_products_selected",
            company_id,
            conversation_id,
            order.get("id", ""),
            len(products),
            len(exclude_ids),
        )
        logger.info(
            "order_flow_%s company_id=%s conversation_id=%s order_id=%s state=%s",
            "continued" if not created else "started",
            company_id,
            conversation_id,
            order.get("id", ""),
            ORDER_SELECTION_STATE,
        )
        return _order_response_payload(
            format_product_selection_list(
                products,
                intro=(
                    "Here are the next available products. Please choose one:"
                    if exclude_ids and products
                    else "Sure, I can help you place an order. Please choose a product from the list:"
                ),
            ),
            order=order,
            next_action=ORDER_SELECTION_STATE,
            routing=route,
        )

    order, created = await create_or_update_order_draft(
        db,
        company_id=company_id,
        conversation_id=conversation_id,
        customer_id=customer_id,
        lead_id=lead_id,
        source_channel=channel,
        details=details,
    )
    missing = missing_order_fields(order)
    if missing:
        logger.info(
            "order_missing_fields_requested company_id=%s conversation_id=%s order_id=%s missing_fields=%s created=%s",
            company_id,
            conversation_id,
            order.get("id", ""),
            ",".join(missing),
            created,
        )
        logger.info(
            "order_flow_%s company_id=%s conversation_id=%s order_id=%s",
            "continued" if not created else "started",
            company_id,
            conversation_id,
            order.get("id", ""),
        )
        return _order_response_payload(format_missing_fields_reply(missing, order=order), order=order, next_action="collect_order_details", routing=route)

    if route_intent == "order_confirmation" or is_order_confirmation(message_text):
        placed = await place_order(db, company_id, order["id"])
        if not placed:
            placed = {**order, "status": "admin_review"}
        placed = await sync_order_contact_records(db, placed, conversation_context=conversation)
        await notify_order_admins(db, placed, conversation_context=conversation)
        return _order_response_payload(format_order_placed_reply(placed), order=placed, next_action="order_placed", routing=route)

    order["status"] = "awaiting_confirmation"
    await db.execute(
        "UPDATE orders SET status='awaiting_confirmation', missing_fields='[]'::jsonb, updated_at=NOW() "
        "WHERE company_id=$1 AND id=$2",
        company_id,
        order["id"],
    )
    logger.info(
        "order_confirmation_requested company_id=%s conversation_id=%s order_id=%s",
        company_id,
        conversation_id,
        order["id"],
    )
    logger.info(
        "order_flow_%s company_id=%s conversation_id=%s order_id=%s",
        "continued" if not created else "started",
        company_id,
        conversation_id,
        order.get("id", ""),
    )
    return _order_response_payload(format_confirmation_reply(order), order=order, next_action="request_order_confirmation", routing=route)


async def list_orders(
    db,
    *,
    company_id: str,
    status: str = "",
    channel: str = "",
    customer: str = "",
    limit: int = 100,
) -> list[dict]:
    company_id = _text(company_id, 120)
    clauses = ["o.company_id=$1"]
    args: list[Any] = [company_id]
    if status:
        args.append(normalize_order_status(status))
        clauses.append(f"o.status=${len(args)}")
    if channel:
        args.append(_text(channel, 40))
        clauses.append(f"o.source_channel=${len(args)}")
    if customer:
        args.append(f"%{_text(customer, 120).lower()}%")
        clauses.append(
            f"(LOWER(o.customer_name) LIKE ${len(args)} OR LOWER(o.customer_email) LIKE ${len(args)} OR LOWER(o.customer_phone) LIKE ${len(args)} OR LOWER(o.product_name) LIKE ${len(args)})"
        )
    args.append(max(1, min(int(limit or 100), 300)))
    query = (
        "SELECT o.*, c.customer_name AS conversation_customer_name, c.assigned_to, c.assigned_name "
        "FROM orders o "
        "LEFT JOIN conversations c ON c.company_id=o.company_id AND c.id=o.conversation_id "
        f"WHERE {' AND '.join(clauses)} "
        f"ORDER BY o.created_at DESC LIMIT ${len(args)}"
    )
    rows = await db.fetch(query, *args)
    return [serialize_order(_as_dict(row)) for row in rows or []]


async def get_order(db, *, company_id: str, order_id: str) -> dict:
    row = await db.fetchrow(
        "SELECT o.*, c.customer_name AS conversation_customer_name, c.assigned_to, c.assigned_name "
        "FROM orders o "
        "LEFT JOIN conversations c ON c.company_id=o.company_id AND c.id=o.conversation_id "
        "WHERE o.company_id=$1 AND o.id=$2 LIMIT 1",
        company_id,
        order_id,
    )
    return serialize_order(_as_dict(row))


async def update_order_status(db, *, company_id: str, order_id: str, status: str, actor_user_id: str = "") -> dict:
    normalized = normalize_order_status(status)
    if normalized not in ORDER_MANAGE_STATUSES:
        raise ValueError("Invalid order status transition")

    # Snapshot the prior status so the lifecycle event row records the
    # transition rather than just the destination.
    prior_row = await db.fetchrow(
        "SELECT status FROM orders WHERE company_id=$1 AND id=$2",
        company_id,
        order_id,
    )
    prior_status = str((dict(prior_row) if prior_row else {}).get("status") or "")

    row = await db.fetchrow(
        "UPDATE orders SET status=$1, updated_at=NOW() WHERE company_id=$2 AND id=$3 RETURNING *",
        normalized,
        company_id,
        order_id,
    )
    order = _as_dict(row)
    lifecycle_event_inserted = False
    if order:
        logger.info(
            "order_status_updated company_id=%s order_id=%s status=%s actor_user_id=%s",
            company_id,
            order_id,
            normalized,
            actor_user_id or "",
        )
        if prior_status != normalized:
            # Record the transition in order_lifecycle_events. The
            # (order_id, to_status, actor_id) unique index makes replays a
            # no-op; if RETURNING id yields no row we know the event was
            # already recorded and we shouldn't double-enqueue the follow-up.
            try:
                inserted = await db.fetchrow(
                    "INSERT INTO order_lifecycle_events "
                    "(id, company_id, order_id, from_status, to_status, actor_type, actor_id) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7) "
                    "ON CONFLICT (order_id, to_status, actor_id) DO NOTHING RETURNING id",
                    make_id(),
                    company_id,
                    order_id,
                    prior_status,
                    normalized,
                    "admin" if actor_user_id else "system",
                    actor_user_id or "",
                )
                lifecycle_event_inserted = bool(inserted)
            except Exception as exc:
                # Lifecycle audit is best-effort. The status update has already
                # committed, so we swallow the error and log it rather than
                # raising — losing the audit row is far less bad than dropping
                # the customer-visible status change.
                logger.warning(
                    "order_lifecycle_insert_failed order_id=%s to_status=%s error=%s",
                    order_id, normalized, exc,
                )

    if lifecycle_event_inserted and order:
        await _maybe_enqueue_followup_evaluation(
            db,
            company_id=company_id,
            order_id=order_id,
            customer_id=str(order.get("customer_id") or ""),
            session_id=str(order.get("conversation_id") or ""),
            to_status=normalized,
        )

    return serialize_order(order)


async def _maybe_enqueue_followup_evaluation(
    db,
    *,
    company_id: str,
    order_id: str,
    customer_id: str,
    session_id: str,
    to_status: str,
) -> None:
    """Fire-and-forget call into the follow-up scheduler.

    The scheduler decides whether the lifecycle transition warrants a
    proactive follow-up. We isolate the import
    here to keep services/order_service.py free of follow-up dependencies at
    module-import time (avoids circular imports during service startup).
    """
    try:
        from services.followup_scheduler import evaluate_order_event
        from shared.background_queue import get_background_queue

        queue = get_background_queue()
        coro = evaluate_order_event(
            db,
            company_id=company_id,
            order_id=order_id,
            customer_id=customer_id,
            session_id=session_id,
            to_status=to_status,
        )
        if queue is not None and queue.enabled:
            await queue.enqueue_coroutine(
                coro,
                name=f"followup-evaluate-{order_id}",
                idempotency_key=f"followup_evaluate:{order_id}:{to_status}",
            )
            try:
                coro.close()
            except Exception:
                pass
        else:
            # No queue available — run inline. Caller still completed the
            # status update before this; an exception here only loses the
            # follow-up, not the customer-visible state change.
            await coro
    except Exception as exc:
        logger.warning(
            "followup_evaluation_dispatch_failed order_id=%s to_status=%s error=%s",
            order_id, to_status, exc,
        )


def serialize_order(order: dict) -> dict:
    if not order:
        return {}
    serialized = dict(order)
    for key in ("raw_details", "missing_fields"):
        default = [] if key == "missing_fields" else {}
        serialized[key] = _json_loads(serialized.get(key), default)
    for key, value in list(serialized.items()):
        if isinstance(value, datetime):
            serialized[key] = value.isoformat()
    return serialized
