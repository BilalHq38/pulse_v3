"""
channel_layer/adapters/facebook.py — Facebook Messenger channel adapter.

Normalizes Facebook webhook payloads into UnifiedMessage and routes outbound
messages through the existing Meta messaging service.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_module
import os
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


class FacebookAdapter(BaseChannelAdapter):
    """Adapter for Facebook Messenger webhook and outbound traffic."""

    channel_type = ChannelType.FACEBOOK

    async def receive_message(
        self,
        payload: dict,
        db,
        tenant_id: str,
    ) -> UnifiedMessage:
        normalized = self.normalize_message(payload)
        return UnifiedMessage(
            message_id=normalized.get("message_id") or make_id(),
            tenant_id=tenant_id,
            user_id=normalized.get("sender_id", ""),
            external_user_id=normalized.get("sender_id", ""),
            channel_type=ChannelType.FACEBOOK,
            direction=MessageDirection.INBOUND,
            content=normalized.get("content", ""),
            timestamp=normalized.get("timestamp", datetime.now(timezone.utc)),
            metadata={
                "page_id": normalized.get("page_id", ""),
                "message_type": normalized.get("message_type", "text"),
            },
            attachments=normalized.get("attachments", []),
            reply_to_message_id=normalized.get("reply_to_message_id", ""),
        )

    async def send_message(
        self,
        message: UnifiedMessage,
        db,
    ) -> SendResult:
        from services.messaging_service import send_meta_channel_message

        current_user = {"company_id": message.tenant_id}
        sent, error = await send_meta_channel_message(
            db,
            "facebook",
            message.external_user_id,
            message.content,
            current_user,
            db_message_id=str(message.metadata.get("db_message_id") or "").strip(),
        )
        return SendResult(
            success=sent,
            error=error,
            channel_type=ChannelType.FACEBOOK,
        )

    async def validate_webhook(
        self,
        request: Request,
        db,
        raw_body: bytes,
    ) -> bool:
        signature_header = request.headers.get("X-Hub-Signature-256", "")
        provided_signature = (
            signature_header.split("=", 1)[-1].strip().lower()
            if "=" in signature_header
            else signature_header.strip().lower()
        )
        if not provided_signature:
            return False

        secret = (os.environ.get("FACEBOOK_WEBHOOK_SECRET") or os.environ.get("META_WEBHOOK_SECRET") or "").strip()
        if not secret:
            return False

        expected = hmac_module.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        return hmac_module.compare_digest(expected, provided_signature)

    def normalize_comment_event(self, change_value: dict, page_id: str) -> dict:
        """Normalize a ``changes[].value`` entry from a Page ``feed`` webhook.

        Facebook sends comment activity via ``entry[].changes[]`` with
        ``field = "feed"`` and ``value.item = "comment"``. We convert that into
        the same shape as ``normalize_message`` so the webhook handler can
        feed it through the existing UnifiedMessage pipeline.
        """
        value = change_value or {}
        result: dict = {
            "message_id": str(value.get("comment_id") or value.get("id") or "").strip(),
            "sender_id": str((value.get("from") or {}).get("id") or "").strip(),
            "sender_name": str((value.get("from") or {}).get("name") or "").strip(),
            "content": str(value.get("message") or "").strip(),
            "message_type": "comment",
            "page_id": str(page_id or "").strip(),
            "post_id": str(value.get("post_id") or value.get("parent_id") or "").strip(),
            "attachments": [],
            "reply_to_message_id": str(value.get("parent_id") or "").strip(),
            "timestamp": datetime.now(timezone.utc),
        }
        ts_raw = value.get("created_time")
        if ts_raw:
            try:
                ts_val = int(ts_raw)
                if ts_val > 10_000_000_000:
                    ts_val = ts_val // 1000
                result["timestamp"] = datetime.fromtimestamp(ts_val, tz=timezone.utc)
            except Exception:
                pass
        item = str(value.get("item") or "").lower()
        verb = str(value.get("verb") or "").lower()
        if item == "like":
            result["message_type"] = "like"
        elif verb == "remove":
            result["message_type"] = "comment_deleted"
        return result

    def normalize_message(self, raw_payload: dict) -> dict:
        result: dict = {
            "message_id": "",
            "sender_id": "",
            "content": "",
            "message_type": "text",
            "page_id": "",
            "attachments": [],
            "reply_to_message_id": "",
            "timestamp": datetime.now(timezone.utc),
        }

        for entry in raw_payload.get("entry", []) or []:
            result["page_id"] = str((entry or {}).get("id") or "").strip()
            for event in (entry or {}).get("messaging", []) or []:
                sender = (event or {}).get("sender", {}) or {}
                result["sender_id"] = str(sender.get("id") or "").strip()

                ts_raw = event.get("timestamp")
                if ts_raw:
                    try:
                        ts_val = int(ts_raw)
                        if ts_val > 10_000_000_000:
                            ts_val = ts_val // 1000
                        result["timestamp"] = datetime.fromtimestamp(ts_val, tz=timezone.utc)
                    except Exception:
                        pass

                message = (event or {}).get("message", {}) or {}
                postback = (event or {}).get("postback", {}) or {}
                result["message_id"] = str(message.get("mid") or postback.get("mid") or "").strip()

                quick_reply = message.get("quick_reply") or {}
                result["content"] = str(
                    message.get("text")
                    or quick_reply.get("payload")
                    or postback.get("title")
                    or postback.get("payload")
                    or ""
                ).strip()
                if postback and not message:
                    result["message_type"] = "postback"

                reply_to_info = message.get("reply_to") or {}
                if reply_to_info.get("mid"):
                    result["reply_to_message_id"] = str(reply_to_info.get("mid") or "").strip()

                for att in message.get("attachments", []) or []:
                    attachment = att or {}
                    payload_data = attachment.get("payload", {}) or {}
                    url = str(payload_data.get("url") or "").strip()
                    att_type = str(attachment.get("type") or "").strip()
                    if url:
                        result["attachments"].append(
                            UnifiedAttachment(
                                type=att_type or "file",
                                url=url,
                                provider_media_id=str(payload_data.get("id") or attachment.get("id") or "").strip(),
                                raw_metadata={"provider": "facebook", "attachment": attachment},
                            )
                        )
                        result["message_type"] = att_type or "attachment"

                break

        return result

    async def health_check(self) -> AdapterHealthStatus:
        from shared.config import is_production

        secret_ok = bool(
            os.environ.get("FACEBOOK_WEBHOOK_SECRET", "").strip() or os.environ.get("META_WEBHOOK_SECRET", "").strip()
        )
        return AdapterHealthStatus(
            adapter="FacebookAdapter",
            channel_type=ChannelType.FACEBOOK,
            healthy=secret_ok or not is_production(),
            detail="configured" if secret_ok else "webhook_secret_missing",
        )
