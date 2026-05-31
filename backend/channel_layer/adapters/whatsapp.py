"""
channel_layer/adapters/whatsapp.py — WhatsApp channel adapter.

Refactored from services/meta_service.py and services/messaging_service.py.
Delegates to existing Meta API infrastructure but presents a clean adapter interface.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import Request

from channel_layer.base import BaseChannelAdapter
from channel_layer.channel_identity import company_default_phone_region, normalize_whatsapp_phone
from channel_layer.schemas import (
    AdapterHealthStatus,
    ChannelType,
    MessageDirection,
    SendResult,
    UnifiedAttachment,
    UnifiedMessage,
)
from core.utils import make_id

logger = logging.getLogger(__name__)


def _first_text(*values) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _is_lid_identifier(value: str) -> bool:
    """Return True if *value* is a WhatsApp @lid JID (Linked Device ID).

    @lid JIDs (e.g. "182974364528890@lid") are internal multi-device identifiers,
    NOT phone numbers.  They must never be used as a canonical sender phone.
    """
    return "@lid" in str(value or "").lower()


def _is_ai_auto_response_idempotency_key(value: str) -> bool:
    return str(value or "").strip().startswith("ai:auto_response:")


def _whatsapp_web_bridge_sender_identity(
    msg: dict,
    contact: dict,
    *,
    default_region: str | None = None,
) -> tuple:
    web_bridge = msg.get("web_bridge") if isinstance(msg.get("web_bridge"), dict) else {}
    contact_bridge = contact.get("web_bridge") if isinstance(contact.get("web_bridge"), dict) else {}
    is_group_message = bool(web_bridge.get("is_group_message"))
    if not is_group_message:
        raw_from = str(msg.get("from") or web_bridge.get("raw_from") or "").strip().lower()
        is_group_message = raw_from.endswith("@g.us")
    group_candidates = [
        web_bridge.get("group_sender_phone"),
        web_bridge.get("group_sender_phone_digits"),
    ]
    candidates = [
        *(group_candidates if is_group_message else []),
        web_bridge.get("sender_phone"),
        web_bridge.get("sender_phone_digits"),
        contact_bridge.get("sender_phone"),
        contact.get("wa_id"),
        *([] if is_group_message else [msg.get("from")]),
        web_bridge.get("contact_number"),
        web_bridge.get("msg_from"),
        web_bridge.get("raw_from"),
    ]
    for candidate in candidates:
        # Skip @lid JIDs — they are device identifiers, not phone numbers.
        if _is_lid_identifier(candidate):
            continue
        identity = normalize_whatsapp_phone(str(candidate or ""), default_region=default_region)
        if identity.is_valid:
            return identity, web_bridge
    return normalize_whatsapp_phone("", default_region=default_region), web_bridge


class WhatsAppAdapter(BaseChannelAdapter):
    """
    Adapter for WhatsApp via Meta Cloud API.

    Inbound: Normalizes Meta webhook payloads into UnifiedMessage.
    Outbound: Routes through existing tenant_meta_config → Meta Graph API.
    """

    channel_type = ChannelType.WHATSAPP

    async def receive_message(
        self,
        payload: dict,
        db,
        tenant_id: str,
    ) -> UnifiedMessage:
        """Parse a Meta WhatsApp webhook payload into UnifiedMessage."""
        default_region = await company_default_phone_region(db, tenant_id)
        normalized = self.normalize_message(payload, default_region=default_region)
        identity = normalized.get("sender_identity") or {}
        return UnifiedMessage(
            message_id=normalized.get("message_id") or make_id(),
            tenant_id=tenant_id,
            user_id=normalized.get("sender_phone", ""),
            external_user_id=normalized.get("sender_phone", ""),
            channel_type=ChannelType.WHATSAPP,
            direction=MessageDirection.INBOUND,
            content=normalized.get("content", ""),
            timestamp=normalized.get("timestamp", datetime.now(timezone.utc)),
            metadata={
                "phone_number_id": normalized.get("phone_number_id", ""),
                "business_account_id": normalized.get("business_account_id", ""),
                "profile_name": normalized.get("profile_name", ""),
                "profile_picture_url": normalized.get("profile_picture_url", ""),
                "message_type": normalized.get("message_type", "text"),
                "raw_message": normalized.get("raw_message", {}),
                "raw_provider_payload": normalized.get("raw_payload", {}),
                "raw_sender_id": normalized.get("raw_sender_id", ""),
                "raw_wa_id": normalized.get("raw_wa_id", ""),
                "normalized_sender_id": identity.get("canonical_value", ""),
                "provider_sender_id": identity.get("provider_sender_id", ""),
                "identity_source": identity.get("source", ""),
                "identity_reason": identity.get("reason", ""),
                "identity_confidence": identity.get("confidence", 0.0),
                "is_group_message": normalized.get("is_group_message", False),
                "group_id": normalized.get("group_id", ""),
                "group_name": normalized.get("group_name", ""),
                "group_sender_phone": normalized.get("group_sender_phone", ""),
                "group_sender_name": normalized.get("group_sender_name", ""),
            },
            attachments=normalized.get("attachments", []),
            reply_to_message_id=normalized.get("reply_to_message_id", ""),
        )

    async def send_message(
        self,
        message: UnifiedMessage,
        db,
    ) -> SendResult:
        """Send a message via WhatsApp using existing infrastructure."""
        from services.messaging_service import send_whatsapp_message

        to_phone = message.external_user_id or message.metadata.get("phone", "")
        db_message_id = str(message.metadata.get("db_message_id") or "").strip()
        conversation_id = str(message.metadata.get("conversation_id") or "").strip() or str(
            message.metadata.get("conversationId") or ""
        ).strip()
        customer_id = str(message.metadata.get("customer_id") or "").strip()
        idempotency_key = str(message.metadata.get("idempotency_key") or "").strip()
        actor_user_id = str(
            message.metadata.get("actor_user_id")
            or message.metadata.get("owner_user_id")
            or message.metadata.get("user_id")
            or ""
        ).strip()
        attachments = [
            {
                "type": att.type,
                "url": att.url,
                "data_url": att.data_url,
                "mime_type": att.mime_type,
                "name": att.name,
                "raw_metadata": dict(att.raw_metadata or {}),
                "caption": str((att.raw_metadata or {}).get("caption") or ""),
                "product_id": str((att.raw_metadata or {}).get("product_id") or ""),
                "product_name": str((att.raw_metadata or {}).get("product_name") or ""),
                "product_title": str((att.raw_metadata or {}).get("product_title") or ""),
                "product_category": str((att.raw_metadata or {}).get("product_category") or ""),
            }
            for att in (message.attachments or [])
            if att.url or att.data_url
        ]

        send_result = await send_whatsapp_message(
            to_phone,
            message.content,
            attachments=attachments or None,
            db=db,
            company_id=message.tenant_id,
            db_message_id=db_message_id,
            user_id=actor_user_id,
            conversation_id=conversation_id or str(message.metadata.get("conversation_id") or ""),
            customer_id=customer_id,
            idempotency_key=idempotency_key,
            single_dispatch=_is_ai_auto_response_idempotency_key(idempotency_key),
            return_details=True,
        )
        sent = bool(send_result[0]) if isinstance(send_result, tuple) and len(send_result) >= 1 else False
        error = str(send_result[1] or "") if isinstance(send_result, tuple) and len(send_result) >= 2 else ""
        details = send_result[2] if isinstance(send_result, tuple) and len(send_result) >= 3 else {}
        details = dict(details or {}) if isinstance(details, dict) else {}

        return SendResult(
            success=sent,
            error=error,
            external_message_id=str(details.get("external_message_id") or ""),
            channel_type=ChannelType.WHATSAPP,
            metadata={
                "delivery_provider": str(details.get("delivery_provider") or "meta_api").strip() or "meta_api",
                "bridge_state": str(details.get("bridge_state") or ""),
                "bridge_scope": str(details.get("bridge_scope") or ""),
                "meta_attempted": bool(details.get("meta_attempted")),
            },
        )

    async def validate_webhook(
        self,
        request: Request,
        db,
        raw_body: bytes,
    ) -> bool:
        """
        Validate Meta WhatsApp webhook signature.
        Delegates to existing signature verification in webhooks.py.
        """
        import hashlib
        import hmac as hmac_module

        from services.meta_service import decrypt_meta_secret

        signature_header = request.headers.get("X-Hub-Signature-256", "")
        provided_signature = (
            signature_header.split("=", 1)[-1].strip().lower()
            if "=" in signature_header
            else signature_header.strip().lower()
        )

        if not provided_signature:
            logger.warning("WhatsApp webhook missing signature")
            return False

        # Try environment secret first
        import os

        env_secret = (os.environ.get("WHATSAPP_WEBHOOK_SECRET") or os.environ.get("META_WEBHOOK_SECRET") or "").strip()
        if env_secret:
            expected = hmac_module.new(env_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
            if hmac_module.compare_digest(expected, provided_signature):
                return True

        # Try tenant-specific secrets
        try:
            rows = await db.fetch(
                "SELECT webhook_secret_enc FROM tenant_meta_config "
                "WHERE channel='whatsapp' AND is_active=TRUE AND webhook_secret_enc != ''",
            )
            for row in rows or []:
                encrypted = str(dict(row).get("webhook_secret_enc", "")).strip()
                if not encrypted:
                    continue
                try:
                    secret = decrypt_meta_secret(encrypted)
                    expected = hmac_module.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
                    if hmac_module.compare_digest(expected, provided_signature):
                        return True
                except Exception:
                    continue
        except Exception as exc:
            logger.warning("Tenant secret lookup failed: %s", exc)

        return False

    def normalize_message(self, raw_payload: dict, default_region: str | None = None) -> dict:
        """
        Extract core message fields from Meta WhatsApp webhook format.

        Meta webhook structure:
        {
          "entry": [{
            "id": "<business_account_id>",
            "changes": [{
              "value": {
                "metadata": {"phone_number_id": "..."},
                "messages": [{
                  "id": "wamid.xxx",
                  "from": "15551234567",
                  "type": "text",
                  "text": {"body": "Hello"},
                  "timestamp": "1234567890"
                }],
                "contacts": [{"profile": {"name": "John"}}]
              }
            }]
          }]
        }
        """
        result: dict = {
            "message_id": "",
            "sender_phone": "",
            "content": "",
            "message_type": "text",
            "phone_number_id": "",
            "business_account_id": "",
            "profile_name": "",
            "profile_picture_url": "",
            "attachments": [],
            "reply_to_message_id": "",
            "timestamp": datetime.now(timezone.utc),
            "raw_message": {},
            "raw_payload": raw_payload,
            "raw_sender_id": "",
            "raw_wa_id": "",
            "sender_identity": {},
            "is_group_message": False,
            "group_id": "",
            "group_name": "",
            "group_sender_phone": "",
            "group_sender_name": "",
        }

        for entry in raw_payload.get("entry", []) or []:
            result["business_account_id"] = str((entry or {}).get("id") or "").strip()

            for change in (entry or {}).get("changes", []) or []:
                value = (change or {}).get("value", {}) or {}
                metadata = value.get("metadata", {}) or {}
                result["phone_number_id"] = str(metadata.get("phone_number_id") or "").strip()
                if metadata.get("is_group_message"):
                    result["is_group_message"] = True
                    result["group_id"] = str(metadata.get("group_id") or "").strip()
                    result["group_name"] = str(metadata.get("group_name") or "").strip()

                contacts = value.get("contacts", []) or []
                if contacts:
                    profile = ((contacts[0] or {}).get("profile") or {})
                    result["profile_name"] = str(profile.get("name") or "").strip()
                    result["profile_picture_url"] = str(
                        profile.get("profile_picture")
                        or profile.get("picture")
                        or (contacts[0] or {}).get("profile_picture_url")
                        or ""
                    ).strip()
                    result["raw_wa_id"] = str((contacts[0] or {}).get("wa_id") or "").strip()

                messages = value.get("messages", []) or []
                if not messages:
                    continue

                msg = messages[0] or {}
                result["raw_message"] = msg
                result["message_id"] = str(msg.get("id") or "").strip()
                metadata_source = str(metadata.get("source") or "").strip()
                is_web_bridge = metadata_source == "whatsapp_web_bridge" or isinstance(msg.get("web_bridge"), dict)
                web_bridge = {}
                if is_web_bridge:
                    sender_identity, web_bridge = _whatsapp_web_bridge_sender_identity(
                        msg,
                        contacts[0] if contacts else {},
                        default_region=default_region,
                    )
                    result["is_group_message"] = bool(
                        result.get("is_group_message") or web_bridge.get("is_group_message")
                    )
                    result["group_id"] = _first_text(result.get("group_id"), web_bridge.get("group_id"))
                    result["group_name"] = _first_text(result.get("group_name"), web_bridge.get("group_name"))
                    result["group_sender_phone"] = _first_text(
                        web_bridge.get("group_sender_phone"),
                        web_bridge.get("group_sender_phone_digits"),
                    )
                    result["group_sender_name"] = _first_text(web_bridge.get("group_sender_name"))
                    result["raw_sender_id"] = _first_text(
                        web_bridge.get("selected_identity_source") and web_bridge.get("raw_sender_id"),
                        web_bridge.get("group_sender_phone"),
                        web_bridge.get("group_sender_phone_digits"),
                        web_bridge.get("sender_phone"),
                        web_bridge.get("sender_phone_digits"),
                        msg.get("from"),
                    )
                    result["raw_wa_id"] = _first_text(
                        (contacts[0] or {}).get("wa_id") if contacts else "",
                        web_bridge.get("sender_phone_digits"),
                    )
                    result["profile_picture_url"] = _first_text(
                        result.get("profile_picture_url"),
                        web_bridge.get("profile_picture_url"),
                        metadata.get("profile_picture_url"),
                    )
                else:
                    result["raw_sender_id"] = str(msg.get("from") or "").strip()
                    sender_identity = normalize_whatsapp_phone(
                        result["raw_sender_id"],
                        default_region=default_region,
                    )
                    if not sender_identity.is_valid and result["raw_wa_id"]:
                        wa_identity = normalize_whatsapp_phone(
                            result["raw_wa_id"],
                            default_region=default_region,
                        )
                        if wa_identity.is_valid:
                            sender_identity = wa_identity
                fallback_unresolved_identity = ""
                bridge_direction = str(web_bridge.get("direction") or "").strip().lower()
                if is_web_bridge and not sender_identity.is_valid and (
                    bridge_direction == "outbound"
                    or bool(web_bridge.get("from_me"))
                    or bool(web_bridge.get("pending_identity"))
                ):
                    # Build an ordered list of fallback candidates.
                    # IMPORTANT: @lid JIDs must be excluded — they are WhatsApp Linked
                    # Device identifiers, not phone numbers.  Using them as sender_phone
                    # would corrupt customer lookups with a non-E.164 string.
                    _raw_target = web_bridge.get("target_raw_id") or ""
                    _lid_jid = web_bridge.get("target_lid_jid") or ""
                    fallback_unresolved_identity = _first_text(
                        web_bridge.get("target_phone"),
                        web_bridge.get("target_phone_digits"),
                        # Only use target_raw_id if it is NOT a @lid identifier
                        "" if _is_lid_identifier(_raw_target) else _raw_target,
                        # target_lid_jid is by definition a @lid — never a phone number
                        web_bridge.get("sender_phone"),
                        web_bridge.get("sender_phone_digits"),
                        web_bridge.get("raw_sender_id"),
                        (contacts[0] or {}).get("wa_id") if contacts else "",
                        msg.get("from"),
                    )
                    # Final guard: if the fallback itself resolved to a @lid, discard it.
                    if _is_lid_identifier(fallback_unresolved_identity):
                        logger.info(
                            "WhatsApp outbound @lid identity skipped as sender_phone "
                            "company_id=%s lid=%s",
                            tenant_id if "tenant_id" in dir() else "",
                            _lid_jid or _raw_target,
                        )
                        fallback_unresolved_identity = ""
                result["sender_phone"] = sender_identity.canonical_value or fallback_unresolved_identity
                result["sender_identity"] = {
                    "raw_value": sender_identity.raw_value,
                    "canonical_value": sender_identity.canonical_value,
                    "provider_id": sender_identity.provider_id,
                    "is_valid": sender_identity.is_valid,
                    "reason": sender_identity.reason,
                    "channel": sender_identity.channel,
                    "confidence": sender_identity.confidence,
                }
                if is_web_bridge:
                    result["sender_identity"]["provider_sender_id"] = _first_text(
                        web_bridge.get("provider_sender_id"),
                        web_bridge.get("target_raw_id"),
                        web_bridge.get("target_lid_jid"),
                        web_bridge.get("raw_from"),
                        web_bridge.get("msg_id_remote"),
                    )
                    result["sender_identity"]["source"] = "whatsapp_web_bridge"
                if not sender_identity.is_valid:
                    logger.warning(
                        "Suspicious WhatsApp sender identity source=%s raw_sender_id=%s raw_wa_id=%s provider_sender_id=%s reason=%s",
                        "whatsapp_web_bridge" if is_web_bridge else "meta_cloud",
                        result["raw_sender_id"],
                        result["raw_wa_id"],
                        (web_bridge or {}).get("provider_sender_id", ""),
                        sender_identity.reason,
                    )
                result["message_type"] = str(msg.get("type") or "text").strip()

                # Parse timestamp (Meta may deliver seconds or milliseconds)
                ts_raw = str(msg.get("timestamp") or "").strip()
                if ts_raw and ts_raw.isdigit():
                    try:
                        ts_val = int(ts_raw)
                        if ts_val > 10_000_000_000:
                            ts_val = ts_val // 1000
                        result["timestamp"] = datetime.fromtimestamp(ts_val, tz=timezone.utc)
                    except Exception:
                        pass

                # Extract content based on type
                msg_type = result["message_type"]
                if msg_type == "text":
                    result["content"] = str((msg.get("text") or {}).get("body") or "").strip()
                elif msg_type in ("image", "video", "audio", "document", "sticker"):
                    media_data = msg.get(msg_type) or {}
                    result["content"] = str(media_data.get("caption") or "").strip()
                    data_url = str(media_data.get("data_url") or "").strip()
                    media_id = str(media_data.get("id") or "").strip()
                    if data_url or media_id:
                        result["attachments"].append(
                            UnifiedAttachment(
                                type=msg_type,
                                url="",
                                data_url=data_url,
                                mime_type=str(media_data.get("mime_type") or "").strip(),
                                name=str(media_data.get("filename") or "").strip(),
                                size=int(media_data.get("size") or 0),
                                provider_media_id=media_id,
                                raw_metadata={
                                    "provider_media_id": media_id,
                                    "message_type": msg_type,
                                    "has_data_url": bool(data_url),
                                },
                            )
                        )
                elif msg_type == "location":
                    location = msg.get("location") or {}
                    result["content"] = f"📍 Location: {location.get('latitude', '')}, {location.get('longitude', '')}"
                elif msg_type == "contacts":
                    result["content"] = "[Contact shared]"
                elif msg_type == "interactive":
                    interactive = msg.get("interactive") or {}
                    reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
                    result["content"] = str(reply.get("title") or reply.get("id") or "").strip()

                # Check for reply context
                context = msg.get("context") or {}
                result["reply_to_message_id"] = str(context.get("message_id") or "").strip()

                break  # Process first message only

        return result

    async def health_check(self) -> AdapterHealthStatus:
        """Check WhatsApp bridge / Meta API connectivity."""
        from services.messaging_service import _bridge_health, _use_bridge

        if _use_bridge():
            status = await _bridge_health()
            return AdapterHealthStatus(
                adapter="WhatsAppAdapter",
                channel_type=ChannelType.WHATSAPP,
                healthy=status.get("status") != "offline",
                detail=f"bridge: {status.get('status', 'unknown')}",
            )
        return AdapterHealthStatus(
            adapter="WhatsAppAdapter",
            channel_type=ChannelType.WHATSAPP,
            healthy=True,
            detail="meta_api",
        )
