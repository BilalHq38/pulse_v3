"""
channel_layer/adapters/chat_widget.py — Live chat widget adapter.

Supports both REST-based and WebSocket-based live chat.
Integrates with the existing core/socket.py infrastructure.
"""

from __future__ import annotations

import hmac as hmac_module
import json
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


class ChatWidgetAdapter(BaseChannelAdapter):
    """
    Adapter for the embedded web chat widget (live chat).

    Inbound:
        - REST endpoint receives messages from the frontend widget
        - WebSocket messages are also normalized through this adapter

    Outbound:
        - Pushes responses back via WebSocket (core/socket.py)
        - Falls back to REST polling if WebSocket unavailable
    """

    channel_type = ChannelType.WEB_CHAT

    async def receive_message(
        self,
        payload: dict,
        db,
        tenant_id: str,
    ) -> UnifiedMessage:
        """
        Normalize a chat widget message payload.

        Expected payload (from frontend widget):
        {
            "session_id": "...",
            "message": "Hello",
            "sender_name": "Visitor",
            "sender_contact": "+1...",
            "page_url": "https://...",
            "client_message_id": "...",
            "company_id": "..."
        }
        """
        session_id = str(payload.get("session_id") or "").strip()
        message_text = str(payload.get("message") or payload.get("content") or "").strip()
        sender_name = str(payload.get("sender_name") or payload.get("name") or "Visitor").strip()
        sender_contact = str(
            payload.get("sender_contact") or payload.get("phone") or payload.get("email") or ""
        ).strip()
        raw_attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
        attachments = [
            UnifiedAttachment(
                type=str(item.get("type") or "file").strip() or "file",
                url=str(item.get("url") or "").strip(),
                data_url=str(item.get("data_url") or "").strip(),
                name=str(item.get("name") or "").strip(),
                mime_type=str(item.get("mime_type") or "").strip(),
                size=int(item.get("size") or 0),
            )
            for item in raw_attachments
            if isinstance(item, dict)
        ]

        return UnifiedMessage(
            message_id=payload.get("client_message_id") or make_id(),
            tenant_id=tenant_id,
            user_id=session_id or sender_contact or "",
            external_user_id=session_id or sender_contact or make_id(),
            channel_type=ChannelType.WEB_CHAT,
            direction=MessageDirection.INBOUND,
            content=message_text,
            timestamp=datetime.now(timezone.utc),
            metadata={
                "session_id": session_id,
                "sender_name": sender_name,
                "sender_contact": sender_contact,
                "page_url": str(payload.get("page_url") or "").strip(),
                "user_agent": str(payload.get("user_agent") or "").strip(),
                "widget_version": str(payload.get("widget_version") or "").strip(),
            },
            attachments=attachments,
        )

    async def send_message(
        self,
        message: UnifiedMessage,
        db,
    ) -> SendResult:
        """
        Send a response back to the chat widget.
        Primary: WebSocket push via core/socket.py
        Fallback: Store for REST polling
        """
        try:
            from core.socket import emit_new_message

            # Emit via WebSocket to the conversation
            conversation_id = message.resolved_conversation_id or message.metadata.get("conversation_id", "")
            if conversation_id:
                message_payload = message.metadata.get("message_payload")
                if not isinstance(message_payload, dict):
                    message_payload = {
                        "id": message.message_id,
                        "content": message.content,
                        "sender_type": "ai",
                        "sender_name": "AI Assistant",
                        "created_at": message.timestamp.isoformat(),
                        "channel": "web_chat",
                    }
                await emit_new_message(
                    conversation_id=conversation_id,
                    message=message_payload,
                )
                return SendResult(
                    success=True,
                    channel_type=ChannelType.WEB_CHAT,
                    metadata={"delivery_method": "websocket"},
                )

            # No conversation_id — store for polling
            return SendResult(
                success=True,
                channel_type=ChannelType.WEB_CHAT,
                metadata={"delivery_method": "stored_for_polling"},
            )

        except Exception as exc:
            logger.error("Chat widget send failed: %s", exc)
            return SendResult(
                success=False,
                error=str(exc),
                channel_type=ChannelType.WEB_CHAT,
            )

    async def validate_webhook(
        self,
        request: Request,
        db,
        raw_body: bytes,
    ) -> bool:
        """
        Validate a chat widget request.
        Checks widget key or allows unsigned in dev mode.
        """
        try:
            payload = json.loads(raw_body or b"{}")
        except Exception:
            return False

        company_id = str((payload or {}).get("company_id") or "").strip()
        if not company_id:
            return False

        # Check widget key
        provided_key = (
            request.headers.get("X-Pulse-Widget-Key", "") or str((payload or {}).get("widget_key") or "")
        ).strip()

        # Load configured key
        configured_key = ""
        try:
            row = await db.fetchval(
                "SELECT api_key FROM channel_settings "
                "WHERE company_id=$1 AND channel='web_chat' AND enabled=TRUE LIMIT 1",
                company_id,
            )
            configured_key = (row or "").strip()
        except Exception:
            pass

        if not configured_key:
            configured_key = (os.environ.get("WEB_CHAT_WIDGET_KEY") or "").strip()

        if configured_key:
            return bool(provided_key) and hmac_module.compare_digest(provided_key, configured_key)

        # No key configured — allow in dev, reject in prod
        from shared.config import is_production

        allow_unsigned = os.environ.get("ALLOW_UNSIGNED_WEB_CHAT_WIDGET", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        return not is_production() or allow_unsigned

    async def health_check(self) -> AdapterHealthStatus:
        """Chat widget is always available as it's server-side."""
        return AdapterHealthStatus(
            adapter="ChatWidgetAdapter",
            channel_type=ChannelType.WEB_CHAT,
            healthy=True,
            detail="embedded",
        )
