"""
channel_layer/adapters/instagram.py — Instagram channel adapter.

Normalizes Instagram webhook payloads (messaging, comments)
into UnifiedMessage format.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_module
import logging
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

logger = logging.getLogger(__name__)


class InstagramAdapter(BaseChannelAdapter):
    """
    Instagram channel adapter.

    Handles:
        - Instagram Direct Messages via Meta webhook
        - Outbound via Meta Send API (page-scoped)
    """

    channel_type = ChannelType.INSTAGRAM

    async def receive_message(
        self,
        payload: dict,
        db,
        tenant_id: str,
    ) -> UnifiedMessage:
        """Normalize Instagram webhook payload into UnifiedMessage."""
        normalized = self.normalize_message(payload)
        return UnifiedMessage(
            message_id=normalized.get("message_id") or make_id(),
            tenant_id=tenant_id,
            user_id=normalized.get("sender_id", ""),
            external_user_id=normalized.get("sender_id", ""),
            channel_type=ChannelType.INSTAGRAM,
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
        """Send a message via Instagram using existing Meta channel infrastructure."""
        from services.messaging_service import send_meta_channel_message

        # Build a minimal current_user dict for the existing function
        current_user = {"company_id": message.tenant_id}
        sent, error = await send_meta_channel_message(
            db,
            "instagram",
            message.external_user_id,
            message.content,
            current_user,
            db_message_id=str(message.metadata.get("db_message_id") or "").strip(),
        )
        return SendResult(
            success=sent,
            error=error,
            channel_type=ChannelType.INSTAGRAM,
        )

    async def validate_webhook(
        self,
        request: Request,
        db,
        raw_body: bytes,
    ) -> bool:
        """Validate Instagram webhook signature (same as Meta/Facebook)."""
        signature_header = request.headers.get("X-Hub-Signature-256", "")
        provided_signature = (
            signature_header.split("=", 1)[-1].strip().lower()
            if "=" in signature_header
            else signature_header.strip().lower()
        )

        if not provided_signature:
            return False

        secret = (os.environ.get("INSTAGRAM_WEBHOOK_SECRET") or os.environ.get("META_WEBHOOK_SECRET") or "").strip()

        if not secret:
            return False

        expected = hmac_module.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        return hmac_module.compare_digest(expected, provided_signature)

    def normalize_comment_event(self, change_value: dict, change_field: str, page_id: str) -> dict:
        """Normalize an Instagram ``changes[]`` entry (comments/mentions/story_insights).

        Instagram sends ``field`` values like ``"comments"``, ``"mentions"`` or
        ``"story_insights"``. We fold them into the same UnifiedMessage shape
        used by DMs so the capture pipeline can treat them uniformly.
        """
        value = change_value or {}
        field = str(change_field or "").lower()
        sender = value.get("from") or {}
        result: dict = {
            "message_id": str(value.get("id") or value.get("comment_id") or value.get("media_id") or "").strip(),
            "sender_id": str(sender.get("id") or value.get("user_id") or "").strip(),
            "sender_name": str(sender.get("username") or sender.get("name") or "").strip(),
            "content": str(value.get("text") or value.get("message") or "").strip(),
            "message_type": "comment" if field == "comments" else (field or "comment"),
            "page_id": str(page_id or "").strip(),
            "post_id": str(value.get("media_id") or value.get("media", {}).get("id") or "").strip(),
            "attachments": [],
            "reply_to_message_id": str(value.get("parent_id") or ""),
            "timestamp": datetime.now(timezone.utc),
        }
        ts_raw = value.get("created_time") or value.get("timestamp")
        if ts_raw:
            try:
                ts_val = int(ts_raw)
                if ts_val > 10_000_000_000:
                    ts_val = ts_val // 1000
                result["timestamp"] = datetime.fromtimestamp(ts_val, tz=timezone.utc)
            except Exception:
                pass
        return result

    def normalize_message(self, raw_payload: dict) -> dict:
        """
        Extract message from Instagram webhook format.

        Instagram uses the same Meta webhook structure:
        {
          "entry": [{
            "id": "<page_id>",
            "messaging": [{
              "sender": {"id": "..."},
              "recipient": {"id": "..."},
              "message": {"mid": "...", "text": "Hello"}
            }]
          }]
        }
        """
        result: dict = {
            "message_id": "",
            "sender_id": "",
            "content": "",
            "message_type": "text",
            "page_id": "",
            "attachments": [],
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
                reply_to_info = message.get("reply_to") or {}
                if reply_to_info.get("mid"):
                    # Instagram reply thread — keep reply relationship
                    result["reply_to_message_id"] = str(reply_to_info.get("mid") or "").strip()
                result["content"] = str(
                    message.get("text")
                    or quick_reply.get("payload")
                    or postback.get("title")
                    or postback.get("payload")
                    or ""
                ).strip()
                if postback and not message:
                    result["message_type"] = "postback"
                if message.get("is_deleted"):
                    result["message_type"] = "deleted"

                # Handle attachments (images, videos, etc.)
                for att in message.get("attachments", []) or []:
                    att_type = str(att.get("type") or "").strip()
                    att_payload = att.get("payload", {}) or {}
                    url = str(att_payload.get("url") or "").strip()
                    if url:
                        result["attachments"].append(
                            UnifiedAttachment(
                                type=att_type or "file",
                                url=url,
                            )
                        )
                        result["message_type"] = att_type or "attachment"

                break  # Process first messaging event

        return result

    async def health_check(self) -> AdapterHealthStatus:
        """Instagram adapter health — check if Meta webhook secret is configured."""
        from shared.config import is_production

        secret_ok = bool(
            os.environ.get("INSTAGRAM_WEBHOOK_SECRET", "").strip() or os.environ.get("META_WEBHOOK_SECRET", "").strip()
        )
        return AdapterHealthStatus(
            adapter="InstagramAdapter",
            channel_type=ChannelType.INSTAGRAM,
            healthy=secret_ok or not is_production(),
            detail="configured" if secret_ok else "webhook_secret_missing",
        )
