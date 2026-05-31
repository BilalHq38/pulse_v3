"""
channel_layer/outbound.py — Outbound message routing engine.

Routes outbound messages through the correct channel adapter based on
the message's channel_type. Business services call this instead of
directly touching adapter internals.
"""

from __future__ import annotations

import logging
import time

from channel_layer.registry import ChannelAdapterRegistry
from channel_layer.schemas import (
    ChannelType,
    MessageDirection,
    SendResult,
    UnifiedAttachment,
    UnifiedMessage,
)
from shared.metrics import increment_counter

logger = logging.getLogger(__name__)


def _attachment_raw_metadata(item: dict) -> dict:
    raw_metadata = item.get("raw_metadata") or item.get("metadata") or {}
    return dict(raw_metadata) if isinstance(raw_metadata, dict) else {"value": str(raw_metadata)}


class OutboundRouter:
    """
    Routes outbound messages to the correct channel adapter.

    Usage:
        router = OutboundRouter(registry)
        result = await router.send(message, db)
    """

    def __init__(self, registry: ChannelAdapterRegistry) -> None:
        self._registry = registry

    async def send(
        self,
        message: UnifiedMessage,
        db,
    ) -> SendResult:
        """
        Route an outbound message through the appropriate adapter.

        Args:
            message: The UnifiedMessage to send (direction should be outbound).
            db: Database connection for tenant credential retrieval.

        Returns:
            SendResult from the adapter.
        """
        message.direction = MessageDirection.OUTBOUND

        adapter = self._registry.get_or_none(message.channel_type)
        if adapter is None:
            logger.error(
                "No adapter registered for outbound channel %s (tenant=%s)",
                message.channel_type.value,
                message.tenant_id,
            )
            return SendResult(
                success=False,
                error=f"Channel {message.channel_type.value} is not supported",
                channel_type=message.channel_type,
            )

        started_at = time.perf_counter()
        try:
            increment_counter(
                "channel_layer.outbound.attempted",
                labels={"channel": message.channel_type.value},
            )

            result = await adapter.send_message(message, db)
            latency_ms = round((time.perf_counter() - started_at) * 1000, 2)

            if result.success:
                increment_counter(
                    "channel_layer.outbound.success",
                    labels={"channel": message.channel_type.value},
                )
                delivery_provider = str((result.metadata or {}).get("delivery_provider") or "").strip()
                logger.info(
                    "Outbound message sent channel=%s provider=%s tenant=%s external_id=%s latency_ms=%s",
                    message.channel_type.value,
                    delivery_provider or message.channel_type.value,
                    message.tenant_id,
                    result.external_message_id or "n/a",
                    latency_ms,
                )
            else:
                increment_counter(
                    "channel_layer.outbound.failed",
                    labels={"channel": message.channel_type.value},
                )
                delivery_provider = str((result.metadata or {}).get("delivery_provider") or "").strip()
                logger.warning(
                    "Outbound message failed channel=%s provider=%s tenant=%s error=%s latency_ms=%s",
                    message.channel_type.value,
                    delivery_provider or message.channel_type.value,
                    message.tenant_id,
                    result.error,
                    latency_ms,
                )

            return result

        except Exception as exc:
            latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
            increment_counter(
                "channel_layer.outbound.error",
                labels={"channel": message.channel_type.value},
            )
            logger.error(
                "Outbound send exception channel=%s tenant=%s latency_ms=%s: %s",
                message.channel_type.value,
                message.tenant_id,
                latency_ms,
                exc,
            )
            return SendResult(
                success=False,
                error=str(exc),
                channel_type=message.channel_type,
            )

    async def send_to_channel(
        self,
        *,
        tenant_id: str,
        channel_type: ChannelType,
        external_user_id: str,
        content: str,
        db,
        subject: str = "",
        metadata: dict | None = None,
        conversation_id: str = "",
        attachments: list[dict] | None = None,
        db_message_id: str = "",
    ) -> SendResult:
        """
        Convenience method to build and send a message in one call.

        This is the primary method business services should use for outbound.
        """
        from core.utils import make_id

        attachment_models = [
            UnifiedAttachment(
                type=str(item.get("type") or "file").strip() or "file",
                url=str(item.get("url") or "").strip(),
                data_url=str(item.get("data_url") or "").strip(),
                name=str(item.get("name") or "").strip(),
                mime_type=str(item.get("mime_type") or "").strip(),
                size=int(item.get("size") or 0),
                raw_metadata=_attachment_raw_metadata(item),
            )
            for item in (attachments or [])
            if isinstance(item, dict)
        ]
        message_metadata = dict(metadata or {})
        if db_message_id and not message_metadata.get("db_message_id"):
            message_metadata["db_message_id"] = db_message_id

        message = UnifiedMessage(
            message_id=make_id(),
            tenant_id=tenant_id,
            user_id=external_user_id,
            external_user_id=external_user_id,
            channel_type=channel_type,
            direction=MessageDirection.OUTBOUND,
            content=content,
            subject=subject,
            metadata=message_metadata,
            attachments=attachment_models,
            resolved_conversation_id=conversation_id,
        )

        return await self.send(message, db)
