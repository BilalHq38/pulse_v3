"""
WhatsApp and Meta channel message sending helpers.
"""

import logging
import time
from typing import Any

import httpx
from fastapi import HTTPException

from core.config import WHATSAPP_PHONE_ID, WHATSAPP_TOKEN
from channel_layer.channel_identity import normalize_whatsapp_phone
from shared.config import (
    messaging_http_connect_timeout_seconds,
    messaging_http_keepalive_expiry_seconds,
    messaging_http_max_connections,
    messaging_http_max_keepalive_connections,
    messaging_http_pool_timeout_seconds,
    messaging_http_timeout_seconds,
    meta_graph_api_version,
    meta_message_send_timeout_seconds,
    whatsapp_bridge_health_timeout_seconds,
    whatsapp_bridge_secret,
    whatsapp_bridge_send_timeout_seconds,
    whatsapp_bridge_session_timeout_seconds,
    whatsapp_bridge_url,
    whatsapp_mode,
)
from services.ai_service.common import _extract_data_url_payload
from services.meta_service import get_meta_config, meta_api_request
from services.provider_health import (
    provider_is_throttled,
    record_provider_failure,
    record_provider_success,
)

logger = logging.getLogger(__name__)

_BRIDGE_URL = whatsapp_bridge_url()
_BRIDGE_SECRET = whatsapp_bridge_secret()
_MODE = whatsapp_mode("")
_HTTP_CLIENT = httpx.AsyncClient(
    timeout=httpx.Timeout(
        messaging_http_timeout_seconds(),
        connect=messaging_http_connect_timeout_seconds(),
        read=messaging_http_timeout_seconds(),
        write=messaging_http_timeout_seconds(),
        pool=messaging_http_pool_timeout_seconds(),
    ),
    limits=httpx.Limits(
        max_keepalive_connections=messaging_http_max_keepalive_connections(),
        max_connections=messaging_http_max_connections(),
        keepalive_expiry=messaging_http_keepalive_expiry_seconds(),
    ),
)


def _use_bridge() -> bool:
    if _MODE == "bridge":
        return True
    if _MODE == "meta":
        return False
    return not (WHATSAPP_PHONE_ID and WHATSAPP_TOKEN)


def _bridge_headers(*, company_id: str = "", user_id: str = "") -> dict[str, str]:
    headers = {"X-Bridge-Secret": _BRIDGE_SECRET}
    scoped_company_id = (company_id or "").strip()
    scoped_user_id = (user_id or "").strip()
    if scoped_company_id:
        headers["X-Bridge-Company-Id"] = scoped_company_id
    if scoped_user_id:
        headers["X-Bridge-User-Id"] = scoped_user_id
    return headers


def _attachment_metadata(attachment: dict | None) -> dict[str, Any]:
    item = dict(attachment or {})
    raw_metadata = item.get("raw_metadata") or item.get("metadata") or {}
    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    for key in ("product_id", "product_name", "product_title", "product_category", "caption"):
        if item.get(key) not in (None, "") and key not in metadata:
            metadata[key] = item.get(key)
    return metadata


def _caption_context_source(
    *,
    message_source: str = "",
    idempotency_key: str = "",
    attachment: dict | None = None,
) -> str:
    source = str(message_source or "").strip().lower()
    key = str(idempotency_key or "").strip().lower()
    metadata = _attachment_metadata(attachment)
    has_product_identity = bool(
        str(metadata.get("product_id") or "").strip()
        or str(metadata.get("product_name") or "").strip()
        or str(metadata.get("product_title") or "").strip()
        or str(metadata.get("product_category") or "").strip()
    )
    if key.startswith("ai:auto_response:") or source in {"ai", "send_message_ai"} or source.startswith("webhook_"):
        return "ai"
    if (
        source in {"manual", "send_message", "manual_ai_respond"}
        or key.startswith("api:")
        or ":manual-ai:" in key
        or "manual_ai" in key
    ):
        return "manual"
    if source in {"product", "catalog", "product_media"} or has_product_identity:
        return "product"
    if source in {"system", "static_fallback"}:
        return "system"
    return "manual"


def _outbound_media_caption_selection(
    message_text: str,
    attachment: dict | None,
    *,
    message_source: str = "",
    idempotency_key: str = "",
) -> tuple[str, str]:
    metadata = _attachment_metadata(attachment)
    item = dict(attachment or {})
    context_source = _caption_context_source(
        message_source=message_source,
        idempotency_key=idempotency_key,
        attachment=attachment,
    )
    manual_text = str(message_text or "").strip()
    if manual_text and context_source == "manual":
        return manual_text[:900].strip(), "manual"
    # For AI-generated responses the assistant's own reply is more informative than
    # a bare product-catalog label, so prefer it as the WhatsApp caption.
    if manual_text and context_source == "ai":
        return manual_text[:900].strip(), "ai"
    explicit_caption = str(metadata.get("caption") or "").strip()
    if explicit_caption:
        selected_source = "ai" if context_source == "ai" else "product" if context_source == "product" else context_source
        return explicit_caption[:900].strip(), selected_source
    product_name = str(metadata.get("product_name") or "").strip()
    product_title = str(metadata.get("product_title") or "").strip()
    product_category = str(metadata.get("product_category") or "").strip()
    filename = str(item.get("name") or item.get("file_name") or "").strip()
    if not (product_name or product_title) and context_source in {"ai", "product", "system"}:
        product_name = filename
    if not (product_name or product_title):
        if manual_text:
            return manual_text[:900].strip(), "manual"
        return filename[:900].strip(), "filename_fallback" if filename else "none"
    label = (
        f"{product_name} ({product_title})"
        if product_name and product_title and product_title.lower() != product_name.lower()
        else (product_name or product_title)
    )
    lines = [f"Product: {label}"]
    if product_category:
        lines.append(f"Category: {product_category}")
    selected = "filename_fallback" if label == filename and not str(metadata.get("product_name") or metadata.get("product_title") or "").strip() else "product"
    return "\n".join(lines)[:900].strip(), selected


def _log_outbound_media_caption_selection(
    *,
    conversation_id: str = "",
    message_id: str = "",
    channel_provider: str = "whatsapp",
    source: str = "",
    has_manual_caption: bool = False,
    selected_caption_source: str = "",
    filename: str = "",
) -> None:
    logger.info(
        "outbound_media_caption_selected conversation_id=%s message_id=%s channel_provider=%s source=%s has_manual_caption=%s selected_caption_source=%s filename=%s",
        str(conversation_id or ""),
        str(message_id or ""),
        str(channel_provider or "whatsapp"),
        str(source or ""),
        bool(has_manual_caption),
        str(selected_caption_source or "none"),
        str(filename or "")[:120],
    )


def _product_media_caption(
    message_text: str,
    attachment: dict | None,
    *,
    message_source: str = "",
    idempotency_key: str = "",
    conversation_id: str = "",
    db_message_id: str = "",
    channel_provider: str = "whatsapp",
    log_selection: bool = False,
) -> str:
    caption, selected_source = _outbound_media_caption_selection(
        message_text,
        attachment,
        message_source=message_source,
        idempotency_key=idempotency_key,
    )
    if log_selection and attachment:
        context_source = _caption_context_source(
            message_source=message_source,
            idempotency_key=idempotency_key,
            attachment=attachment,
        )
        _log_outbound_media_caption_selection(
            conversation_id=conversation_id,
            message_id=db_message_id,
            channel_provider=channel_provider,
            source=context_source,
            has_manual_caption=bool(str(message_text or "").strip() and context_source == "manual"),
            selected_caption_source=selected_source,
            filename=str((attachment or {}).get("name") or (attachment or {}).get("file_name") or ""),
        )
    return caption


def _whatsapp_send_text_for_attachments(
    message_text: str,
    attachments: list | None,
    *,
    message_source: str = "",
    idempotency_key: str = "",
    conversation_id: str = "",
    db_message_id: str = "",
    channel_provider: str = "whatsapp",
    log_selection: bool = False,
) -> str:
    if not attachments:
        return str(message_text or "").strip()
    first = attachments[0] if isinstance(attachments[0], dict) else {}
    return _product_media_caption(
        message_text,
        first,
        message_source=message_source,
        idempotency_key=idempotency_key,
        conversation_id=conversation_id,
        db_message_id=db_message_id,
        channel_provider=channel_provider,
        log_selection=log_selection,
    )


def _bridge_scope_for(*, company_id: str = "", user_id: str = "") -> str:
    scoped_company_id = (company_id or "").strip()
    scoped_user_id = (user_id or "").strip()
    if scoped_user_id:
        return f"user-{scoped_company_id or 'global'}-{scoped_user_id}"
    if scoped_company_id:
        return f"company-{scoped_company_id}"
    return "default"


async def _bridge_session_snapshot(*, company_id: str = "", user_id: str = "") -> dict[str, Any]:
    if not _BRIDGE_SECRET:
        return {
            "status": "not_configured",
            "state": "not_configured",
            "scope": _bridge_scope_for(company_id=company_id, user_id=user_id),
            "company_id": (company_id or "").strip(),
            "user_id": (user_id or "").strip(),
            "phone": "",
        }
    try:
        resp = await _HTTP_CLIENT.get(
            f"{_BRIDGE_URL}/session",
            headers=_bridge_headers(company_id=company_id, user_id=user_id),
            timeout=whatsapp_bridge_session_timeout_seconds(),
        )
        if resp.status_code != 200 or not resp.content:
            return {
                "status": "error",
                "state": "error",
                "scope": _bridge_scope_for(company_id=company_id, user_id=user_id),
                "company_id": (company_id or "").strip(),
                "user_id": (user_id or "").strip(),
                "phone": "",
            }
        payload = resp.json()
        if not isinstance(payload, dict):
            return {
                "status": "error",
                "state": "error",
                "scope": _bridge_scope_for(company_id=company_id, user_id=user_id),
                "company_id": (company_id or "").strip(),
                "user_id": (user_id or "").strip(),
                "phone": "",
            }
        state = str(payload.get("state") or payload.get("status") or "").strip().lower() or "unknown"
        return {
            **payload,
            "status": state,
            "state": state,
            "scope": str(payload.get("scope") or _bridge_scope_for(company_id=company_id, user_id=user_id)),
            "company_id": str(payload.get("company_id") or (company_id or "").strip()),
            "user_id": str(payload.get("user_id") or (user_id or "").strip()),
            "phone": str(payload.get("phone") or ""),
        }
    except httpx.ConnectError:
        return {
            "status": "offline",
            "state": "offline",
            "scope": _bridge_scope_for(company_id=company_id, user_id=user_id),
            "company_id": (company_id or "").strip(),
            "user_id": (user_id or "").strip(),
            "phone": "",
        }
    except Exception as exc:
        logger.debug("[Bridge] Session status lookup failed: %s", exc)
        return {
            "status": "error",
            "state": "error",
            "scope": _bridge_scope_for(company_id=company_id, user_id=user_id),
            "company_id": (company_id or "").strip(),
            "user_id": (user_id or "").strip(),
            "phone": "",
        }


async def _bridge_session_status(*, company_id: str = "", user_id: str = "") -> str:
    snapshot = await _bridge_session_snapshot(company_id=company_id, user_id=user_id)
    return str(snapshot.get("state") or snapshot.get("status") or "").strip().lower() or "unknown"


def _bridge_not_ready_error(snapshot: dict[str, Any]) -> str:
    state = str(snapshot.get("state") or snapshot.get("status") or "").strip().lower()
    if state in {"", "ready"}:
        return ""
    if state == "initializing":
        return "WHATSAPP_SESSION_NOT_READY: WhatsApp session is still starting."
    if state == "qr_required":
        return "WHATSAPP_SESSION_NOT_READY: WhatsApp session is not connected for this account. Please scan the QR code first."
    if state == "not_configured":
        return "WhatsApp bridge is not configured."
    if state == "offline":
        return "WhatsApp bridge is not reachable."
    return "WHATSAPP_SESSION_NOT_READY: WhatsApp session is not ready."


def _extract_meta_message_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    direct = str(payload.get("message_id") or payload.get("id") or "").strip()
    if direct:
        return direct
    messages = payload.get("messages")
    if isinstance(messages, list) and messages:
        first = messages[0] if isinstance(messages[0], dict) else {}
        return str(first.get("id") or "").strip()
    return ""


def _extract_bridge_error(payload: Any, fallback_text: str = "") -> str:
    if isinstance(payload, dict):
        primary = str(payload.get("error") or payload.get("detail") or "").strip()
        details = str(payload.get("details") or "").strip()
        if details and (not primary or len(primary) <= 2 or details == primary):
            return details
        if primary and details and details != primary:
            return f"{primary} ({details})"
        if primary:
            return primary
        if details:
            return details
    return str(fallback_text or "").strip()


def _normalize_region_code(value: str | None) -> str:
    candidate = str(value or "").strip().upper()
    if len(candidate) == 2 and candidate.isalpha():
        return candidate
    return ""


async def _company_default_phone_region(db, company_id: str) -> str:
    scoped_company_id = (company_id or "").strip()
    if not db or not scoped_company_id:
        return ""
    try:
        row = await db.fetchrow(
            "SELECT default_phone_region FROM company_settings WHERE company_id=$1 LIMIT 1",
            scoped_company_id,
        )
    except Exception as exc:
        logger.debug("Failed to load company phone region company_id=%s: %s", scoped_company_id, exc)
        return ""
    if not row:
        return ""
    return _normalize_region_code(dict(row).get("default_phone_region"))


async def _normalize_outbound_whatsapp_phone(
    to_phone: str,
    *,
    db=None,
    company_id: str = "",
) -> tuple[str, str]:
    raw_phone = str(to_phone or "").strip()
    if not raw_phone:
        return "", "Phone number is required"

    fallback_region = await _company_default_phone_region(db, company_id)
    identity = normalize_whatsapp_phone(raw_phone, default_region=fallback_region)
    if identity.is_valid:
        return identity.canonical_value.lstrip("+"), ""

    return (
        "",
        "Invalid WhatsApp phone number. Save the contact number in full international format "
        "or set the tenant default phone region in Company Settings.",
    )


async def _persist_outbound_message_state(
    db,
    *,
    company_id: str,
    db_message_id: str,
    delivery_status: str,
    external_message_id: str = "",
) -> None:
    scoped_company_id = (company_id or "").strip()
    local_message_id = (db_message_id or "").strip()
    status = (delivery_status or "").strip().lower()
    if not db or not scoped_company_id or not local_message_id or not status:
        return

    try:
        conversation_id = ""
        if status == "sent":
            conversation_id = await db.fetchval(
                "UPDATE messages SET external_message_id=COALESCE(NULLIF($1,''), external_message_id), "
                "delivery_status='sent', sent_at=COALESCE(sent_at, NOW()), updated_at=NOW() "
                "WHERE id=$2 AND company_id=$3 RETURNING conversation_id",
                (external_message_id or "").strip(),
                local_message_id,
                scoped_company_id,
            )
        elif status == "delivered":
            conversation_id = await db.fetchval(
                "UPDATE messages SET external_message_id=COALESCE(NULLIF($1,''), external_message_id), "
                "delivery_status='delivered', sent_at=COALESCE(sent_at, NOW()), "
                "delivered_at=COALESCE(delivered_at, NOW()), updated_at=NOW() "
                "WHERE id=$2 AND company_id=$3 RETURNING conversation_id",
                (external_message_id or "").strip(),
                local_message_id,
                scoped_company_id,
            )
        elif status == "failed":
            conversation_id = await db.fetchval(
                "UPDATE messages SET delivery_status='failed', failed_at=COALESCE(failed_at, NOW()), updated_at=NOW() "
                "WHERE id=$1 AND company_id=$2 RETURNING conversation_id",
                local_message_id,
                scoped_company_id,
            )
        if conversation_id:
            message_row = await db.fetchrow(
                "SELECT * FROM messages WHERE id=$1 AND company_id=$2 LIMIT 1",
                local_message_id,
                scoped_company_id,
            )
            if message_row:
                attachments = await db.fetch(
                    "SELECT * FROM message_attachments WHERE message_id=$1 ORDER BY created_at ASC",
                    local_message_id,
                )
                payload = dict(message_row)
                payload["attachments"] = [
                    {
                        "id": row["id"],
                        "type": row["file_type"],
                        "url": row["file_url"],
                        "name": row["file_name"],
                        "size": row["file_size"],
                    }
                    for row in attachments
                ]
                from core.socket import emit_message_updated

                await emit_message_updated(str(conversation_id), payload)
    except Exception as exc:
        logger.warning(
            "Failed to persist outbound message state company_id=%s message_id=%s status=%s: %s",
            scoped_company_id,
            local_message_id,
            status,
            exc,
        )


async def _send_via_bridge(
    to_phone: str,
    message_text: str,
    attachments: list | None = None,
    *,
    db=None,
    company_id: str = "",
    user_id: str = "",
    conversation_id: str = "",
    customer_id: str = "",
    db_message_id: str = "",
    idempotency_key: str = "",
    send_attempt: int = 1,
) -> tuple[bool, str, str]:
    phone, phone_error = await _normalize_outbound_whatsapp_phone(
        to_phone,
        db=db,
        company_id=company_id,
    )
    if not phone:
        logger.warning(
            "whatsapp_outbound_bridge_invalid_phone company_id=%s user_id=%s conversation_id=%s customer_id=%s message_id=%s selected_whatsapp_scope=%s recipient_id=%s send_attempt=%s idempotency_key=%s failure_code=%s error=%s",
            company_id,
            user_id,
            conversation_id,
            customer_id,
            db_message_id,
            _bridge_scope_for(company_id=company_id, user_id=user_id),
            to_phone,
            send_attempt,
            idempotency_key,
            "INVALID_WHATSAPP_PHONE",
            phone_error,
        )
        return False, phone_error or "Phone number is required", ""
    if not _BRIDGE_SECRET:
        return False, "WHATSAPP_BRIDGE_SECRET is not configured", ""
    bridge_scope = _bridge_scope_for(company_id=company_id, user_id=user_id)
    throttled, health_state = provider_is_throttled(
        "qr",
        channel="whatsapp",
        company_id=company_id,
        scope=bridge_scope,
    )
    if throttled:
        retry_after = max(float((health_state.throttle_until if health_state else 0.0) - time.time()), 0.0)
        error = f"WhatsApp QR provider is temporarily degraded; retry after {retry_after:.0f}s"
        logger.warning(
            "whatsapp_outbound_bridge_throttled company_id=%s user_id=%s conversation_id=%s message_id=%s scope=%s retry_after_seconds=%.1f",
            company_id,
            user_id,
            conversation_id,
            db_message_id,
            bridge_scope,
            retry_after,
        )
        return False, error, ""
    try:
        first_attachment = attachments[0] if attachments and isinstance(attachments[0], dict) else {}
        is_ai_response = str(idempotency_key or "").startswith("ai:")
        if attachments and is_ai_response and str(message_text or "").strip():
            # For AI-generated responses with images, send the AI text as the WhatsApp
            # caption so the customer reads the full AI reply, not just the product name.
            outbound_text = str(message_text).strip()
            outbound_caption_source = "manual"
        else:
            outbound_text = _whatsapp_send_text_for_attachments(
                message_text,
                attachments,
                idempotency_key=idempotency_key,
                conversation_id=conversation_id,
                db_message_id=db_message_id,
                channel_provider="qr",
                log_selection=True,
            )
            _, outbound_caption_source = _outbound_media_caption_selection(
                message_text,
                first_attachment,
                idempotency_key=idempotency_key,
            )
        resp = await _HTTP_CLIENT.post(
            f"{_BRIDGE_URL}/send",
            json={
                "to": phone,
                "message": outbound_text,
                "attachments": attachments or [],
                "caption_source": outbound_caption_source,
                "conversation_id": conversation_id,
                "message_id": db_message_id,
            },
            headers=_bridge_headers(company_id=company_id, user_id=user_id),
            timeout=whatsapp_bridge_send_timeout_seconds(),
        )
        # If the bridge rejected the attachment (e.g. could not fetch/convert the
        # image), fall back to sending the text alone so the customer at least
        # receives the AI's reply.
        if resp.status_code == 400 and attachments:
            logger.warning(
                "whatsapp_bridge_attachment_rejected_text_fallback company_id=%s conversation_id=%s message_id=%s",
                company_id,
                conversation_id,
                db_message_id,
            )
            fallback_text = str(message_text or outbound_text or "").strip()
            resp = await _HTTP_CLIENT.post(
                f"{_BRIDGE_URL}/send",
                json={
                    "to": phone,
                    "message": fallback_text,
                    "attachments": [],
                    "conversation_id": conversation_id,
                    "message_id": db_message_id,
                },
                headers=_bridge_headers(company_id=company_id, user_id=user_id),
                timeout=whatsapp_bridge_send_timeout_seconds(),
            )
        data = {}
        if resp.content:
            try:
                data = resp.json()
            except ValueError:
                data = {}
        if resp.status_code == 200 and isinstance(data, dict) and data.get("success"):
            record_provider_success(
                "qr",
                channel="whatsapp",
                company_id=company_id,
                scope=bridge_scope,
                operation="outbound_send",
            )
            logger.info(
                "whatsapp_outbound_bridge_sent company_id=%s user_id=%s conversation_id=%s customer_id=%s message_id=%s bridge_scope=%s selected_whatsapp_scope=%s bridge_state=ready bridge_connected_phone=%s recipient_id=%s send_attempt=%s idempotency_key=%s delivery_status=sent attachment_count=%s",
                company_id,
                user_id,
                conversation_id,
                customer_id,
                db_message_id,
                str(data.get("scope") or _bridge_scope_for(company_id=company_id, user_id=user_id)),
                _bridge_scope_for(company_id=company_id, user_id=user_id),
                str(data.get("phone") or ""),
                phone,
                send_attempt,
                idempotency_key,
                len(attachments or []),
            )
            return True, "", _extract_meta_message_id(data)
        error_text = _extract_bridge_error(data, resp.text[:300])
        failure_code = (
            str(data.get("code") or "WHATSAPP_BRIDGE_SEND_FAILED")
            if isinstance(data, dict)
            else "WHATSAPP_BRIDGE_SEND_FAILED"
        )
        bridge_state = str(data.get("state") or data.get("status") or "") if isinstance(data, dict) else ""
        log_func = logger.warning if failure_code == "WHATSAPP_SESSION_NOT_READY" else logger.error
        log_func(
            "whatsapp_outbound_bridge_failed company_id=%s user_id=%s conversation_id=%s customer_id=%s message_id=%s bridge_scope=%s selected_whatsapp_scope=%s bridge_state=%s bridge_connected_phone=%s recipient_id=%s send_attempt=%s idempotency_key=%s delivery_status=failed failure_code=%s status_code=%s error=%s",
            company_id,
            user_id,
            conversation_id,
            customer_id,
            db_message_id,
            str(data.get("scope") or _bridge_scope_for(company_id=company_id, user_id=user_id)) if isinstance(data, dict) else _bridge_scope_for(company_id=company_id, user_id=user_id),
            _bridge_scope_for(company_id=company_id, user_id=user_id),
            bridge_state,
            str(data.get("phone") or "") if isinstance(data, dict) else "",
            phone,
            send_attempt,
            idempotency_key,
            failure_code,
            resp.status_code,
            error_text or resp.text[:300],
        )
        record_provider_failure(
            "qr",
            channel="whatsapp",
            company_id=company_id,
            scope=bridge_scope,
            operation="outbound_send",
            error=error_text or resp.text[:300],
            rate_limited=failure_code in {"RATE_LIMITED", "TOO_MANY_REQUESTS"},
        )
        return False, error_text or "WhatsApp bridge send failed", ""
    except httpx.ConnectError:
        logger.error("[Bridge] Cannot connect to WhatsApp bridge on %s", _BRIDGE_URL)
        record_provider_failure(
            "qr",
            channel="whatsapp",
            company_id=company_id,
            scope=bridge_scope,
            operation="outbound_send",
            error="WhatsApp bridge is not reachable",
        )
        return False, "WhatsApp bridge is not reachable", ""
    except Exception as exc:
        logger.error("[Bridge] Send error: %s", exc)
        record_provider_failure(
            "qr",
            channel="whatsapp",
            company_id=company_id,
            scope=bridge_scope,
            operation="outbound_send",
            error=str(exc),
        )
        return False, str(exc), ""


async def _bridge_health() -> dict:
    try:
        resp = await _HTTP_CLIENT.get(f"{_BRIDGE_URL}/health", timeout=whatsapp_bridge_health_timeout_seconds())
        return resp.json() if resp.status_code == 200 else {"status": "error"}
    except Exception:
        return {"status": "offline"}


async def get_channel_connection_status(
    db,
    *,
    company_id: str,
    channel: str,
    user_id: str = "",
) -> dict[str, Any]:
    scoped_company_id = (company_id or "").strip()
    channel_name = (channel or "").strip().lower()
    scoped_user_id = (user_id or "").strip()
    if channel_name in {"", "web_chat", "website"}:
        return {"connected": True, "status": "ready", "error": ""}
    if not scoped_company_id:
        return {"connected": False, "status": "missing_company", "error": "Company context is required."}

    if channel_name == "whatsapp":
        meta_error = ""
        try:
            config = await get_meta_config(db, scoped_company_id, channel="whatsapp", include_secrets=False)
            if (
                config.get("is_active", True)
                and config.get("access_token_configured")
                and str(config.get("phone_number_id") or "").strip()
            ):
                return {"connected": True, "status": "ready", "provider": "meta", "error": ""}
            meta_error = "WhatsApp Meta configuration is incomplete."
        except HTTPException as exc:
            meta_error = str(exc.detail or "WhatsApp Meta configuration is missing.")
        except Exception as exc:
            meta_error = str(exc or "WhatsApp Meta configuration could not be checked.")

        snapshot = await _bridge_session_snapshot(company_id=scoped_company_id, user_id=scoped_user_id)
        state = str(snapshot.get("state") or snapshot.get("status") or "").strip().lower()
        if state == "ready":
            return {
                "connected": True,
                "status": "ready",
                "provider": "bridge",
                "scope": str(snapshot.get("scope") or ""),
                "phone": str(snapshot.get("phone") or ""),
                "error": "",
            }
        return {
            "connected": False,
            "status": state or "not_configured",
            "provider": "bridge",
            "scope": str(snapshot.get("scope") or ""),
            "phone": str(snapshot.get("phone") or ""),
            "error": _bridge_not_ready_error(snapshot) or meta_error or "WhatsApp is not connected.",
        }

    if channel_name in {"facebook", "instagram"}:
        try:
            config = await get_meta_config(db, scoped_company_id, channel=channel_name, include_secrets=False)
            if (
                config.get("is_active", True)
                and config.get("access_token_configured")
                and str(config.get("page_id") or "").strip()
            ):
                return {"connected": True, "status": "ready", "provider": "meta", "error": ""}
        except Exception:
            pass
        row = await db.fetchrow(
            "SELECT enabled, access_token, page_id FROM channel_settings WHERE company_id=$1 AND channel=$2 LIMIT 1",
            scoped_company_id,
            channel_name,
        )
        data = dict(row) if row else {}
        if data.get("enabled") and str(data.get("access_token") or "").strip() and str(data.get("page_id") or "").strip():
            return {"connected": True, "status": "ready", "provider": "legacy", "error": ""}
        return {
            "connected": False,
            "status": "not_configured",
            "error": f"{channel_name.capitalize()} is not connected.",
        }

    if channel_name == "email":
        row = await db.fetchrow(
            "SELECT * FROM channel_settings WHERE company_id=$1 AND channel='email' AND enabled=TRUE LIMIT 1",
            scoped_company_id,
        )
        data = dict(row) if row else {}
        provider = str(data.get("email_provider") or "smtp_imap").strip().lower()
        if provider == "brevo":
            connected = bool(str(data.get("api_key") or "").strip() and str(data.get("email_address") or "").strip())
        else:
            connected = bool(
                str(data.get("smtp_host") or "").strip()
                and str(data.get("email_address") or data.get("smtp_user") or "").strip()
            )
        return {
            "connected": connected,
            "status": "ready" if connected else "not_configured",
            "provider": provider,
            "error": "" if connected else "Email is not connected.",
        }

    return {"connected": True, "status": "ready", "error": ""}


async def _send_via_meta(
    to_phone: str,
    message_text: str,
    attachments: list | None = None,
    *,
    message_source: str = "",
    idempotency_key: str = "",
    conversation_id: str = "",
    db_message_id: str = "",
    channel_provider: str = "meta",
) -> tuple[bool, str, str]:
    if not WHATSAPP_PHONE_ID or not WHATSAPP_TOKEN:
        logger.warning("Meta WhatsApp not configured")
        return False, "Meta WhatsApp is not configured", ""
    phone, phone_error = await _normalize_outbound_whatsapp_phone(to_phone)
    if not phone:
        logger.warning("send_whatsapp_message: invalid meta phone error=%s", phone_error)
        return False, phone_error or "Phone number is required", ""
    url = f"https://graph.facebook.com/{meta_graph_api_version()}/{WHATSAPP_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": message_text},
    }
    if attachments:
        attachment = attachments[0] or {}
        link = str(attachment.get("url") or attachment.get("data_url") or "").strip()
        media_type = _infer_media_type(attachment)
        # Meta Cloud API requires a publicly accessible URL; skip image sending for
        # relative/local paths which Meta's servers cannot reach.
        is_public_url = link.startswith(("http://", "https://"))
        if link and media_type == "image" and is_public_url:
            caption = _product_media_caption(
                message_text,
                attachment,
                message_source=message_source,
                idempotency_key=idempotency_key,
                conversation_id=conversation_id,
                db_message_id=db_message_id,
                channel_provider=channel_provider,
                log_selection=True,
            )
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "image",
                "image": {"link": link, **({"caption": caption} if caption else {})},
            }
    try:
        resp = await _HTTP_CLIENT.post(url, json=payload, headers=headers, timeout=meta_message_send_timeout_seconds())
        if resp.status_code == 200:
            logger.info("[Meta] WhatsApp sent to %s", phone)
            data = resp.json() if resp.content else {}
            record_provider_success("meta", channel="whatsapp", operation="outbound_send")
            return True, "", _extract_meta_message_id(data)
        logger.error("[Meta] Send failed [%s]: %s", resp.status_code, resp.text[:300])
        record_provider_failure(
            "meta",
            channel="whatsapp",
            operation="outbound_send",
            error=resp.text[:300],
            rate_limited=resp.status_code in {429, 503},
        )
        return False, resp.text[:300], ""
    except Exception as exc:
        logger.error("[Meta] Send error: %s", exc)
        record_provider_failure("meta", channel="whatsapp", operation="outbound_send", error=str(exc))
        return False, str(exc), ""


def _infer_media_type(attachment: dict) -> str:
    atype = str(attachment.get("type") or attachment.get("file_type") or "").strip().lower()
    if atype in {"image", "video", "audio", "document"}:
        return atype
    mime = str(attachment.get("mime_type") or "").strip().lower()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    url = str(attachment.get("url") or attachment.get("data_url") or "").strip().lower()
    if url.startswith("data:image/") or url.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
        return "image"
    return "document"


async def _send_via_tenant_meta(
    db,
    company_id: str,
    to_phone: str,
    message_text: str,
    attachments: list | None = None,
    *,
    message_source: str = "",
    idempotency_key: str = "",
    conversation_id: str = "",
    db_message_id: str = "",
) -> tuple[bool, str, str]:
    config = await get_meta_config(
        db,
        company_id,
        channel="whatsapp",
        include_secrets=True,
    )
    phone_number_id = (config.get("phone_number_id") or "").strip()
    if not phone_number_id:
        return False, "phone_number_id is not configured for this tenant", ""
    phone, phone_error = await _normalize_outbound_whatsapp_phone(
        to_phone,
        db=db,
        company_id=company_id,
    )
    if not phone:
        return False, phone_error or "Phone number is required", ""
    try:
        if attachments:
            attachment = attachments[0] or {}
            media_type = _infer_media_type(attachment)
            media_ref: dict[str, str]
            if attachment.get("data_url"):
                parsed = _extract_data_url_payload(str(attachment.get("data_url") or ""))
                if not parsed:
                    return False, "Unsupported media attachment", ""
                mime_type, raw_bytes, _ = parsed
                upload = await meta_api_request(
                    db,
                    company_id,
                    config,
                    method="POST",
                    path=f"{phone_number_id}/media",
                    channel="whatsapp",
                    metric_name="runtime_media_upload",
                    files={
                        "file": (
                            attachment.get("name") or "attachment",
                            raw_bytes,
                            attachment.get("mime_type") or mime_type or "application/octet-stream",
                        ),
                        "messaging_product": (None, "whatsapp"),
                    },
                )
                media_id = str(upload.get("id") or "").strip()
                if not media_id:
                    return False, "Meta media upload failed", ""
                media_ref = {"id": media_id}
            elif attachment.get("url"):
                raw_url = str(attachment["url"]).strip()
                # Meta's servers must be able to download the URL; skip local/relative paths.
                if raw_url.startswith(("http://", "https://")):
                    media_ref = {"link": raw_url}
                else:
                    media_ref = {}
            else:
                media_ref = {}
            if media_ref:
                caption = _product_media_caption(
                    message_text,
                    attachment,
                    message_source=message_source,
                    idempotency_key=idempotency_key,
                    conversation_id=conversation_id,
                    db_message_id=db_message_id,
                    channel_provider="meta",
                    log_selection=True,
                )
                send_payload = await meta_api_request(
                    db,
                    company_id,
                    config,
                    method="POST",
                    path=f"{phone_number_id}/messages",
                    channel="whatsapp",
                    metric_name="runtime_media_message",
                    billable_units=1,
                    json_body={
                        "messaging_product": "whatsapp",
                        "to": phone,
                        "type": media_type,
                        media_type: {
                            **media_ref,
                            **(
                                {"caption": caption}
                                if caption and media_type in {"image", "video", "document"}
                                else {}
                            ),
                            **(
                                {"filename": attachment.get("name", "")}
                                if media_type == "document" and attachment.get("name")
                                else {}
                            ),
                        },
                    },
                )
                return True, "", _extract_meta_message_id(send_payload)
        send_payload = await meta_api_request(
            db,
            company_id,
            config,
            method="POST",
            path=f"{phone_number_id}/messages",
            channel="whatsapp",
            metric_name="runtime_text_message",
            billable_units=1,
            json_body={
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {"body": message_text},
            },
        )
        return True, "", _extract_meta_message_id(send_payload)
    except HTTPException as exc:
        return False, str(exc.detail), ""
    except Exception as exc:
        logger.error("[MetaTenant] Send error: %s", exc)
        return False, str(exc), ""


def _tenant_meta_error_allows_bridge_fallback(error: str) -> bool:
    lowered = str(error or "").strip().lower()
    return bool(
        "phone_number_id is not configured" in lowered
        or "meta whatsapp is not configured" in lowered
        or "not configured for this tenant" in lowered
    )


async def send_whatsapp_message(
    to_phone: str,
    message_text: str,
    attachments: list | None = None,
    *,
    db=None,
    company_id: str = "",
    db_message_id: str = "",
    user_id: str = "",
    conversation_id: str = "",
    customer_id: str = "",
    idempotency_key: str = "",
    single_dispatch: bool = False,
) -> tuple[bool, str]:
    scoped_company_id = (company_id or "").strip()
    local_message_id = (db_message_id or "").strip()
    scoped_user_id = (user_id or "").strip()
    scoped_conversation_id = (conversation_id or "").strip()
    scoped_customer_id = (customer_id or "").strip()
    scoped_idempotency_key = (idempotency_key or "").strip()
    sent = False
    error = ""
    external_message_id = ""
    bridge_status = ""
    bridge_snapshot: dict[str, Any] = {}
    bridge_preferred = False
    tenant_meta_attempted = False
    if db and scoped_company_id:
        logger.info(
            "whatsapp_outbound_meta_first company_id=%s user_id=%s conversation_id=%s customer_id=%s message_id=%s selected_whatsapp_scope=%s recipient_id=%s send_attempt=%s idempotency_key=%s delivery_status=%s",
            scoped_company_id,
            scoped_user_id,
            scoped_conversation_id,
            scoped_customer_id,
            local_message_id,
            _bridge_scope_for(company_id=scoped_company_id, user_id=scoped_user_id),
            to_phone,
            0,
            scoped_idempotency_key,
            "pending",
        )
        try:
            tenant_meta_attempted = True
            sent, error, external_message_id = await _send_via_tenant_meta(
                db,
                scoped_company_id,
                to_phone,
                message_text,
                attachments=attachments,
                idempotency_key=scoped_idempotency_key,
                conversation_id=scoped_conversation_id,
                db_message_id=local_message_id,
            )
            if sent:
                record_provider_success(
                    "meta",
                    channel="whatsapp",
                    company_id=scoped_company_id,
                    operation="tenant_outbound_send",
                )
                if local_message_id:
                    await _persist_outbound_message_state(
                        db,
                        company_id=scoped_company_id,
                        db_message_id=local_message_id,
                        delivery_status="sent",
                        external_message_id=external_message_id,
                    )
                return sent, error
            if not _tenant_meta_error_allows_bridge_fallback(error):
                record_provider_failure(
                    "meta",
                    channel="whatsapp",
                    company_id=scoped_company_id,
                    operation="tenant_outbound_send",
                    error=error or "Meta tenant send failed",
                    rate_limited="rate" in str(error or "").lower() or "429" in str(error or ""),
                )
            if single_dispatch and not _tenant_meta_error_allows_bridge_fallback(error):
                logger.warning(
                    "whatsapp_outbound_bridge_fallback_suppressed company_id=%s user_id=%s conversation_id=%s customer_id=%s message_id=%s idempotency_key=%s error=%s",
                    scoped_company_id,
                    scoped_user_id,
                    scoped_conversation_id,
                    scoped_customer_id,
                    local_message_id,
                    scoped_idempotency_key,
                    error or "unknown",
                )
                if local_message_id:
                    await _persist_outbound_message_state(
                        db,
                        company_id=scoped_company_id,
                        db_message_id=local_message_id,
                        delivery_status="failed",
                        external_message_id=external_message_id,
                )
                return sent, error
            bridge_snapshot = await _bridge_session_snapshot(
                company_id=scoped_company_id,
                user_id=scoped_user_id,
            )
            bridge_status = str(bridge_snapshot.get("state") or bridge_snapshot.get("status") or "").strip().lower()
            bridge_preferred = bridge_status in {"ready", "initializing"}
            if not (bridge_preferred or _use_bridge()):
                if local_message_id:
                    await _persist_outbound_message_state(
                        db,
                        company_id=scoped_company_id,
                        db_message_id=local_message_id,
                        delivery_status="failed",
                        external_message_id=external_message_id,
                    )
                return sent, error
            logger.warning("[MetaTenant] bridge fallback after tenant send failure: %s", error or "unknown")
        except HTTPException as exc:
            logger.warning("[MetaTenant] falling back after config error: %s", exc.detail)
            error = str(exc.detail)
        except Exception as exc:
            logger.warning("[MetaTenant] fallback triggered: %s", exc)
            error = str(exc)
        if not bridge_snapshot:
            bridge_snapshot = await _bridge_session_snapshot(
                company_id=scoped_company_id,
                user_id=scoped_user_id,
            )
            bridge_status = str(bridge_snapshot.get("state") or bridge_snapshot.get("status") or "").strip().lower()
            bridge_preferred = bridge_status in {"ready", "initializing"}
        logger.info(
            "whatsapp_outbound_bridge_scope_check company_id=%s user_id=%s conversation_id=%s customer_id=%s message_id=%s bridge_scope=%s selected_whatsapp_scope=%s bridge_state=%s bridge_connected_phone=%s recipient_id=%s send_attempt=%s idempotency_key=%s delivery_status=%s failure_code=%s meta_attempted=%s",
            scoped_company_id,
            scoped_user_id,
            scoped_conversation_id,
            scoped_customer_id,
            local_message_id,
            str(bridge_snapshot.get("scope") or _bridge_scope_for(company_id=scoped_company_id, user_id=scoped_user_id)),
            _bridge_scope_for(company_id=scoped_company_id, user_id=scoped_user_id),
            bridge_status,
            str(bridge_snapshot.get("phone") or ""),
            to_phone,
            0,
            scoped_idempotency_key,
            "pending",
            "",
            tenant_meta_attempted,
        )
    if bridge_preferred or _use_bridge():
        bridge_error = _bridge_not_ready_error(bridge_snapshot) if bridge_snapshot else ""
        if bridge_error:
            record_provider_failure(
                "qr",
                channel="whatsapp",
                company_id=scoped_company_id,
                scope=_bridge_scope_for(company_id=scoped_company_id, user_id=scoped_user_id),
                operation="session_ready_check",
                error=bridge_error,
            )
            logger.warning(
                "whatsapp_outbound_bridge_not_ready company_id=%s user_id=%s conversation_id=%s customer_id=%s message_id=%s bridge_scope=%s selected_whatsapp_scope=%s bridge_state=%s recipient_id=%s idempotency_key=%s delivery_status=failed failure_code=%s error=%s",
                scoped_company_id,
                scoped_user_id,
                scoped_conversation_id,
                scoped_customer_id,
                local_message_id,
                str(bridge_snapshot.get("scope") or _bridge_scope_for(company_id=scoped_company_id, user_id=scoped_user_id)),
                _bridge_scope_for(company_id=scoped_company_id, user_id=scoped_user_id),
                bridge_status,
                to_phone,
                scoped_idempotency_key,
                "WHATSAPP_SESSION_NOT_READY",
                bridge_error,
            )
            if db and scoped_company_id and local_message_id:
                await _persist_outbound_message_state(
                    db,
                    company_id=scoped_company_id,
                    db_message_id=local_message_id,
                    delivery_status="failed",
                )
            return False, bridge_error
        sent, error, external_message_id = await _send_via_bridge(
            to_phone,
            message_text,
            attachments=attachments,
            db=db,
            company_id=scoped_company_id,
            user_id=scoped_user_id,
            conversation_id=scoped_conversation_id,
            customer_id=scoped_customer_id,
            db_message_id=local_message_id,
            idempotency_key=scoped_idempotency_key,
            send_attempt=1,
        )
    else:
        sent, error, external_message_id = await _send_via_meta(
            to_phone,
            message_text,
            attachments=attachments,
            idempotency_key=scoped_idempotency_key,
            conversation_id=scoped_conversation_id,
            db_message_id=local_message_id,
        )
    if db and scoped_company_id and local_message_id:
        await _persist_outbound_message_state(
            db,
            company_id=scoped_company_id,
            db_message_id=local_message_id,
            delivery_status="sent" if sent else "failed",
            external_message_id=external_message_id,
        )
    return sent, error


async def _send_meta_channel_via_legacy_settings(
    db,
    *,
    channel: str,
    company_id: str,
    recipient_id: str,
    message_text: str,
) -> tuple[bool, str, str]:
    if company_id:
        row = await db.fetchrow(
            "SELECT access_token, page_id FROM channel_settings WHERE channel=$1 AND company_id=$2 LIMIT 1",
            channel,
            company_id,
        )
    else:
        row = await db.fetchrow(
            "SELECT access_token, page_id FROM channel_settings WHERE channel=$1 LIMIT 1",
            channel,
        )

    settings = dict(row) if row else {}
    access_token = settings.get("access_token", "").strip()
    page_id = settings.get("page_id", "").strip()
    if not access_token or not page_id:
        return False, f"{channel.capitalize()} channel is not configured (missing access_token or page_id)", ""

    url = f"https://graph.facebook.com/{meta_graph_api_version()}/{page_id}/messages"
    payload = {"recipient": {"id": recipient_id}, "message": {"text": message_text}}
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    try:
        resp = await _HTTP_CLIENT.post(url, json=payload, headers=headers, timeout=meta_message_send_timeout_seconds())
        if resp.status_code < 300:
            data = resp.json() if resp.content else {}
            return True, "", _extract_meta_message_id(data)
        return False, resp.text[:300], ""
    except Exception as exc:
        return False, str(exc), ""


async def send_meta_channel_message(
    db,
    channel: str,
    recipient_id: str,
    message_text: str,
    current_user: dict,
    attachments: list | None = None,
    *,
    db_message_id: str = "",
) -> tuple[bool, str]:
    if channel not in ("facebook", "instagram"):
        return False, f"Unsupported channel: {channel}"
    if attachments:
        return False, f"Image sending is not supported for {channel} in this version"

    company_id = (current_user.get("company_id", "") or "").strip()
    local_message_id = (db_message_id or "").strip()

    sent = False
    error = ""
    external_message_id = ""
    try:
        config = await get_meta_config(
            db,
            company_id,
            channel=channel,
            include_secrets=True,
        )
        page_id = (config.get("business_account_id") or "").strip() or (config.get("catalog_id") or "").strip()
        if not page_id:
            raise HTTPException(
                400,
                f"{channel.capitalize()} channel is not configured (missing page_id/business_account_id)",
            )
        payload = {"recipient": {"id": recipient_id}, "message": {"text": message_text}}
        meta_response = await meta_api_request(
            db,
            company_id,
            config,
            method="POST",
            path=f"{page_id}/messages",
            channel=channel,
            metric_name="runtime_text_message",
            billable_units=1,
            json_body=payload,
        )
        sent = True
        external_message_id = _extract_meta_message_id(meta_response)
    except HTTPException as exc:
        logger.warning(
            "Meta tenant send fallback channel=%s company_id=%s detail=%s",
            channel,
            company_id,
            exc.detail,
        )
        sent, error, external_message_id = await _send_meta_channel_via_legacy_settings(
            db,
            channel=channel,
            company_id=company_id,
            recipient_id=recipient_id,
            message_text=message_text,
        )
        if not sent and not error:
            error = str(exc.detail)
    except Exception as exc:
        sent, error, external_message_id = await _send_meta_channel_via_legacy_settings(
            db,
            channel=channel,
            company_id=company_id,
            recipient_id=recipient_id,
            message_text=message_text,
        )
        if not sent and not error:
            error = str(exc)
    if sent:
        record_provider_success("meta", channel=channel, company_id=company_id, operation="messenger_outbound_send")
    else:
        record_provider_failure(
            "meta",
            channel=channel,
            company_id=company_id,
            operation="messenger_outbound_send",
            error=error or "Meta channel send failed",
            rate_limited="rate" in str(error or "").lower() or "429" in str(error or ""),
        )

    if db and company_id and local_message_id:
        await _persist_outbound_message_state(
            db,
            company_id=company_id,
            db_message_id=local_message_id,
            delivery_status="sent" if sent else "failed",
            external_message_id=external_message_id,
        )
    return sent, error
