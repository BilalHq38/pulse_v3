"""
WhatsApp and Meta channel message sending helpers.
"""

import logging
from typing import Any

import httpx
from fastapi import HTTPException

from core.config import WHATSAPP_PHONE_ID, WHATSAPP_TOKEN
from core.phone_normalization import normalize_to_e164_digits, strict_normalize_to_e164_digits
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


async def _bridge_session_status(*, company_id: str = "", user_id: str = "") -> str:
    if not _BRIDGE_SECRET:
        return "not_configured"
    try:
        resp = await _HTTP_CLIENT.get(
            f"{_BRIDGE_URL}/session",
            headers=_bridge_headers(company_id=company_id, user_id=user_id),
            timeout=whatsapp_bridge_session_timeout_seconds(),
        )
        if resp.status_code != 200 or not resp.content:
            return "error"
        payload = resp.json()
        if not isinstance(payload, dict):
            return "error"
        return str(payload.get("status") or "").strip().lower() or "unknown"
    except httpx.ConnectError:
        return "offline"
    except Exception as exc:
        logger.debug("[Bridge] Session status lookup failed: %s", exc)
        return "error"


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
    normalized = strict_normalize_to_e164_digits(raw_phone, fallback_region=fallback_region)
    if normalized:
        return normalized, ""

    fallback_digits = normalize_to_e164_digits(raw_phone, fallback_region=fallback_region)
    if fallback_digits and fallback_region:
        logger.warning(
            "Strict WhatsApp phone normalization failed, keeping digit fallback company_id=%s region=%s raw=%s",
            (company_id or "").strip(),
            fallback_region,
            raw_phone,
        )
        return fallback_digits, ""

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
) -> tuple[bool, str, str]:
    phone, phone_error = await _normalize_outbound_whatsapp_phone(
        to_phone,
        db=db,
        company_id=company_id,
    )
    if not phone:
        logger.warning("send_whatsapp_message: invalid bridge phone company_id=%s error=%s", company_id, phone_error)
        return False, phone_error or "Phone number is required", ""
    if not _BRIDGE_SECRET:
        return False, "WHATSAPP_BRIDGE_SECRET is not configured", ""
    try:
        resp = await _HTTP_CLIENT.post(
            f"{_BRIDGE_URL}/send",
            json={"to": phone, "message": message_text, "attachments": attachments or []},
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
            logger.info("[Bridge] WhatsApp sent to %s", phone)
            return True, "", _extract_meta_message_id(data)
        error_text = _extract_bridge_error(data, resp.text[:300])
        logger.error("[Bridge] Send failed [%s]: %s", resp.status_code, error_text or resp.text[:300])
        return False, error_text or "WhatsApp bridge send failed", ""
    except httpx.ConnectError:
        logger.error("[Bridge] Cannot connect to WhatsApp bridge on %s", _BRIDGE_URL)
        return False, "WhatsApp bridge is not reachable", ""
    except Exception as exc:
        logger.error("[Bridge] Send error: %s", exc)
        return False, str(exc), ""


async def _bridge_health() -> dict:
    try:
        resp = await _HTTP_CLIENT.get(f"{_BRIDGE_URL}/health", timeout=whatsapp_bridge_health_timeout_seconds())
        return resp.json() if resp.status_code == 200 else {"status": "error"}
    except Exception:
        return {"status": "offline"}


async def _send_via_meta(
    to_phone: str,
    message_text: str,
    attachments: list | None = None,
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
        mime_type = str(attachment.get("mime_type") or "").strip().lower()
        if link and mime_type.startswith("image/"):
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "image",
                "image": {"link": link, **({"caption": message_text} if message_text else {})},
            }
    try:
        resp = await _HTTP_CLIENT.post(url, json=payload, headers=headers, timeout=meta_message_send_timeout_seconds())
        if resp.status_code == 200:
            logger.info("[Meta] WhatsApp sent to %s", phone)
            data = resp.json() if resp.content else {}
            return True, "", _extract_meta_message_id(data)
        logger.error("[Meta] Send failed [%s]: %s", resp.status_code, resp.text[:300])
        return False, resp.text[:300], ""
    except Exception as exc:
        logger.error("[Meta] Send error: %s", exc)
        return False, str(exc), ""


def _infer_media_type(attachment: dict) -> str:
    mime = str(attachment.get("mime_type") or "").strip().lower()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return "document"


async def _send_via_tenant_meta(
    db,
    company_id: str,
    to_phone: str,
    message_text: str,
    attachments: list | None = None,
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
                media_ref = {"link": str(attachment["url"]).strip()}
            else:
                media_ref = {}
            if media_ref:
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
                                {"caption": message_text}
                                if message_text and media_type in {"image", "video", "document"}
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


async def send_whatsapp_message(
    to_phone: str,
    message_text: str,
    attachments: list | None = None,
    *,
    db=None,
    company_id: str = "",
    db_message_id: str = "",
    user_id: str = "",
) -> tuple[bool, str]:
    scoped_company_id = (company_id or "").strip()
    local_message_id = (db_message_id or "").strip()
    scoped_user_id = (user_id or "").strip()
    sent = False
    error = ""
    external_message_id = ""
    bridge_status = ""
    bridge_preferred = False
    if db and scoped_company_id:
        bridge_status = await _bridge_session_status(
            company_id=scoped_company_id,
            user_id=scoped_user_id,
        )
        bridge_preferred = bridge_status in {"ready", "initializing"}
        try:
            sent, error, external_message_id = await _send_via_tenant_meta(
                db,
                scoped_company_id,
                to_phone,
                message_text,
                attachments=attachments,
            )
            if sent:
                if local_message_id:
                    await _persist_outbound_message_state(
                        db,
                        company_id=scoped_company_id,
                        db_message_id=local_message_id,
                        delivery_status="sent",
                        external_message_id=external_message_id,
                    )
                return sent, error
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
        except Exception as exc:
            logger.warning("[MetaTenant] fallback triggered: %s", exc)
    if bridge_preferred or _use_bridge():
        sent, error, external_message_id = await _send_via_bridge(
            to_phone,
            message_text,
            attachments=attachments,
            db=db,
            company_id=scoped_company_id,
            user_id=scoped_user_id,
        )
    else:
        sent, error, external_message_id = await _send_via_meta(
            to_phone,
            message_text,
            attachments=attachments,
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

    if db and company_id and local_message_id:
        await _persist_outbound_message_state(
            db,
            company_id=company_id,
            db_message_id=local_message_id,
            delivery_status="sent" if sent else "failed",
            external_message_id=external_message_id,
        )
    return sent, error
