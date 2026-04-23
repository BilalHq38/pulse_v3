"""
WhatsApp and Meta channel message sending helpers.
"""

import logging
import os
from typing import Any

import httpx
from fastapi import HTTPException

from core.config import WHATSAPP_PHONE_ID, WHATSAPP_TOKEN
from services.ai_service.common import _extract_data_url_payload
from services.meta_service import get_meta_config, meta_api_request

logger = logging.getLogger(__name__)

_BRIDGE_URL = os.environ.get("WHATSAPP_BRIDGE_URL", "http://localhost:3001").rstrip("/")
_BRIDGE_SECRET = (os.environ.get("WHATSAPP_BRIDGE_SECRET", "") or "").strip()
_MODE = os.environ.get("WHATSAPP_MODE", "").strip().lower()
_HTTP_CLIENT = httpx.AsyncClient(
    timeout=httpx.Timeout(20.0, connect=5.0, read=20.0, write=20.0, pool=5.0),
    limits=httpx.Limits(
        max_keepalive_connections=10,
        max_connections=20,
        keepalive_expiry=60.0,
    ),
)


def _use_bridge() -> bool:
    if _MODE == "bridge":
        return True
    if _MODE == "meta":
        return False
    return not (WHATSAPP_PHONE_ID and WHATSAPP_TOKEN)


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
        if status == "sent":
            await db.execute(
                "UPDATE messages SET external_message_id=COALESCE(NULLIF($1,''), external_message_id), "
                "delivery_status='sent', sent_at=COALESCE(sent_at, NOW()), updated_at=NOW() "
                "WHERE id=$2 AND company_id=$3",
                (external_message_id or "").strip(),
                local_message_id,
                scoped_company_id,
            )
            return
        if status == "failed":
            await db.execute(
                "UPDATE messages SET delivery_status='failed', failed_at=COALESCE(failed_at, NOW()), updated_at=NOW() "
                "WHERE id=$1 AND company_id=$2",
                local_message_id,
                scoped_company_id,
            )
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
) -> tuple[bool, str, str]:
    phone = to_phone.strip().replace("+", "").replace(" ", "").replace("-", "")
    if not phone:
        logger.warning("send_whatsapp_message: empty phone number")
        return False, "Phone number is required", ""
    if not _BRIDGE_SECRET:
        return False, "WHATSAPP_BRIDGE_SECRET is not configured", ""
    try:
        resp = await _HTTP_CLIENT.post(
            f"{_BRIDGE_URL}/send",
            json={"to": phone, "message": message_text, "attachments": attachments or []},
            headers={"X-Bridge-Secret": _BRIDGE_SECRET},
            timeout=20.0,
        )
        data = resp.json() if resp.content else {}
        if resp.status_code == 200 and isinstance(data, dict) and data.get("success"):
            logger.info("[Bridge] WhatsApp sent to %s", phone)
            return True, "", _extract_meta_message_id(data)
        logger.error("[Bridge] Send failed [%s]: %s", resp.status_code, resp.text[:200])
        return False, resp.text[:200], ""
    except httpx.ConnectError:
        logger.error("[Bridge] Cannot connect to WhatsApp bridge on %s", _BRIDGE_URL)
        return False, "WhatsApp bridge is not reachable", ""
    except Exception as exc:
        logger.error("[Bridge] Send error: %s", exc)
        return False, str(exc), ""


async def _bridge_health() -> dict:
    try:
        resp = await _HTTP_CLIENT.get(f"{_BRIDGE_URL}/health", timeout=5.0)
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
    phone = to_phone.strip().replace("+", "").replace(" ", "").replace("-", "")
    if not phone:
        logger.warning("send_whatsapp_message: empty phone number")
        return False, "Phone number is required", ""
    url = f"https://graph.facebook.com/v21.0/{WHATSAPP_PHONE_ID}/messages"
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
        resp = await _HTTP_CLIENT.post(url, json=payload, headers=headers, timeout=15.0)
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
    phone = to_phone.strip().replace("+", "").replace(" ", "").replace("-", "")
    if not phone:
        return False, "Phone number is required", ""
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
) -> tuple[bool, str]:
    scoped_company_id = (company_id or "").strip()
    local_message_id = (db_message_id or "").strip()
    sent = False
    error = ""
    external_message_id = ""
    if db and scoped_company_id:
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
            if not _use_bridge():
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
    if _use_bridge():
        sent, error, external_message_id = await _send_via_bridge(
            to_phone,
            message_text,
            attachments=attachments,
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

    url = f"https://graph.facebook.com/v21.0/{page_id}/messages"
    payload = {"recipient": {"id": recipient_id}, "message": {"text": message_text}}
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    try:
        resp = await _HTTP_CLIENT.post(url, json=payload, headers=headers, timeout=15.0)
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
