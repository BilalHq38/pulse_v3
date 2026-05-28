"""
channel_layer/base.py — Abstract base class for channel adapters.

All adapters (WhatsApp, Email, Chat Widget, Instagram) implement this interface.
Business services never touch adapter internals — they consume UnifiedMessage only.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from fastapi import Request

from channel_layer.schemas import (
    AdapterHealthStatus,
    ChannelType,
    SendResult,
    UnifiedMessage,
)

logger = logging.getLogger(__name__)


class BaseChannelAdapter(ABC):
    """
    Contract that every channel adapter must satisfy.

    Lifecycle:
        Inbound:  validate_webhook → receive_message → UnifiedMessage
        Outbound: send_message(UnifiedMessage) → SendResult
    """

    channel_type: ChannelType

    @abstractmethod
    async def receive_message(
        self,
        payload: dict,
        db,
        tenant_id: str,
    ) -> UnifiedMessage:
        """
        Parse a raw webhook/event payload and return a normalized UnifiedMessage.

        Args:
            payload: The raw JSON payload from the external service.
            db: Database connection for tenant credential lookups.
            tenant_id: The resolved company_id for this event.

        Returns:
            A fully normalized UnifiedMessage ready for business logic.
        """
        ...

    @abstractmethod
    async def send_message(
        self,
        message: UnifiedMessage,
        db,
    ) -> SendResult:
        """
        Send a message through this channel's external API.

        Args:
            message: The UnifiedMessage to send (direction=outbound).
            db: Database connection for credential retrieval.

        Returns:
            SendResult indicating success/failure and external message ID.
        """
        ...

    @abstractmethod
    async def validate_webhook(
        self,
        request: Request,
        db,
        raw_body: bytes,
    ) -> bool:
        """
        Validate an inbound webhook's signature/authenticity.

        Args:
            request: The FastAPI Request object (for headers).
            db: Database connection for secret lookups.
            raw_body: The raw request body bytes for HMAC verification.

        Returns:
            True if the webhook is authentic, False otherwise.

        Raises:
            HTTPException: If validation fails with a specific HTTP status.
        """
        ...

    def normalize_message(self, raw_payload: dict) -> dict:
        """
        Extract the core message fields from a raw payload.
        This is a lightweight pre-processing step before full receive_message().

        Default implementation returns the payload as-is.
        Adapters should override for channel-specific extraction.
        """
        return raw_payload

    async def health_check(self) -> AdapterHealthStatus:
        """
        Check if this adapter's external service is reachable.
        Default returns healthy. Override for actual connectivity checks.
        """
        return AdapterHealthStatus(
            adapter=self.__class__.__name__,
            channel_type=self.channel_type,
            healthy=True,
            detail="default",
        )

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} channel={self.channel_type.value}>"
