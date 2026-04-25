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
        normalized = self.normalize_message(payload)
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
                "message_type": normalized.get("message_type", "text"),
                "raw_message": normalized.get("raw_message", {}),
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
        actor_user_id = str(
            message.metadata.get("actor_user_id")
            or message.metadata.get("owner_user_id")
            or message.metadata.get("user_id")
            or ""
        ).strip()
        attachments = [
            {
                "url": att.url,
                "data_url": att.data_url,
                "mime_type": att.mime_type,
                "name": att.name,
            }
            for att in (message.attachments or [])
            if att.url or att.data_url
        ]

        sent, error = await send_whatsapp_message(
            to_phone,
            message.content,
            attachments=attachments or None,
            db=db,
            company_id=message.tenant_id,
            db_message_id=db_message_id,
            user_id=actor_user_id,
        )

        return SendResult(
            success=sent,
            error=error,
            channel_type=ChannelType.WHATSAPP,
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

    def normalize_message(self, raw_payload: dict) -> dict:
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
            "attachments": [],
            "reply_to_message_id": "",
            "timestamp": datetime.now(timezone.utc),
            "raw_message": {},
        }

        for entry in raw_payload.get("entry", []) or []:
            result["business_account_id"] = str((entry or {}).get("id") or "").strip()

            for change in (entry or {}).get("changes", []) or []:
                value = (change or {}).get("value", {}) or {}
                metadata = value.get("metadata", {}) or {}
                result["phone_number_id"] = str(metadata.get("phone_number_id") or "").strip()

                contacts = value.get("contacts", []) or []
                if contacts:
                    result["profile_name"] = str(((contacts[0] or {}).get("profile") or {}).get("name") or "").strip()

                messages = value.get("messages", []) or []
                if not messages:
                    continue

                msg = messages[0] or {}
                result["raw_message"] = msg
                result["message_id"] = str(msg.get("id") or "").strip()
                result["sender_phone"] = str(msg.get("from") or "").strip()
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
