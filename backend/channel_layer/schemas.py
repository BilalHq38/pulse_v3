"""
channel_layer/schemas.py — Unified schemas consumed by all business services.

Every adapter normalizes raw payloads into these schemas so that downstream
consumers (Conversation Service, Orchestrator, AI) never see channel details.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ChannelType(str, Enum):
    WHATSAPP = "whatsapp"
    EMAIL = "email"
    WEB_CHAT = "web_chat"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"


class MessageDirection(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class UnifiedAttachment(BaseModel):
    """A single attachment in a unified message."""

    type: str = "file"  # image, video, audio, document, file
    url: str = ""
    data_url: str = ""
    name: str = ""
    mime_type: str = ""
    size: int = 0


class UnifiedMessage(BaseModel):
    """
    The canonical message format consumed by all business services.
    Every adapter MUST produce this for inbound messages and accept it for outbound.
    """

    message_id: str
    tenant_id: str  # company_id in the existing system
    user_id: str = ""  # canonical actor id used by orchestrator/services
    external_user_id: str = ""  # sender identity on the channel (phone, email, user_id)
    channel_type: ChannelType
    direction: MessageDirection = MessageDirection.INBOUND
    content: str = ""
    subject: str = ""  # email subject, empty for messaging channels
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)
    attachments: list[UnifiedAttachment] = Field(default_factory=list)
    reply_to_message_id: str = ""
    trace_id: str = ""

    # Resolved fields (populated during processing)
    resolved_customer_id: str = ""
    resolved_conversation_id: str = ""


class SendResult(BaseModel):
    """Result of an outbound send attempt through an adapter."""

    success: bool = False
    error: str = ""
    external_message_id: str = ""
    channel_type: ChannelType = ChannelType.WEB_CHAT
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdapterHealthStatus(BaseModel):
    """Health check result for a channel adapter."""

    adapter: str
    channel_type: ChannelType
    healthy: bool = False
    detail: str = ""


class ChannelCredentials(BaseModel):
    """Per-tenant credentials for a channel, loaded from DB."""

    tenant_id: str
    channel_type: ChannelType
    credentials: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
