"""
channel_layer/router.py — FastAPI router for unified channel endpoints.

Provides clean webhook endpoints and a unified send API that routes
through the channel abstraction layer.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from channel_layer.adapters.chat_widget import ChatWidgetAdapter
from channel_layer.adapters.email import EmailAdapter
from channel_layer.adapters.facebook import FacebookAdapter
from channel_layer.adapters.instagram import InstagramAdapter
from channel_layer.adapters.whatsapp import WhatsAppAdapter
from channel_layer.normalizer import MessageNormalizer
from channel_layer.outbound import OutboundRouter
from channel_layer.registry import ChannelAdapterRegistry
from channel_layer.schemas import ChannelType
from channel_layer.validators import (
    build_replay_fingerprint,
    check_replay,
    client_ip_from_request,
)
from shared.cache import get_cache_client
from shared.metrics import increment_counter
from shared.webhook_task_runner import create_safe_detached_task

logger = logging.getLogger(__name__)

router = APIRouter(tags=["channels"])

# ── Module-level singletons ──────────────────────────────────────────────────

_registry = ChannelAdapterRegistry()
_registry.register(WhatsAppAdapter())
_registry.register(EmailAdapter())
_registry.register(ChatWidgetAdapter())
_registry.register(FacebookAdapter())
_registry.register(InstagramAdapter())

_normalizer = MessageNormalizer()
_outbound = OutboundRouter(_registry)
_UNKNOWN_TENANT_RATE_LIMIT_PER_MINUTE = 60


async def _enforce_unknown_tenant_rate_limit(channel: str, ip: str) -> None:
    cache = get_cache_client(namespace="channel_unknown_tenant_rate_limit")
    minute_bucket = int(time.time() // 60)
    key = f"{channel}:{ip}:{minute_bucket}"
    state = await cache.get_json(key)
    count = int((state or {}).get("count") or 0) + 1
    await cache.set_json(key, {"count": count}, ttl_seconds=70)
    if count > _UNKNOWN_TENANT_RATE_LIMIT_PER_MINUTE:
        raise HTTPException(429, "Webhook rate limit exceeded")


def get_channel_registry() -> ChannelAdapterRegistry:
    """Get the global channel adapter registry."""
    return _registry


def get_outbound_router() -> OutboundRouter:
    """Get the global outbound message router."""
    return _outbound


# ── Request/Response Models ──────────────────────────────────────────────────


class SendMessageRequest(BaseModel):
    """API request to send a message through a channel."""

    channel: str
    tenant_id: str
    recipient_id: str
    content: str
    subject: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str = ""


class ChannelHealthResponse(BaseModel):
    """Response for channel health check endpoint."""

    supported_channels: list[str]
    adapters: list[dict[str, Any]]


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/channels/health")
async def channel_health() -> dict:
    """Health check for all registered channel adapters."""
    adapter_statuses = await _registry.health_check_all()
    return {
        "supported_channels": _registry.supported_channels,
        "adapters": adapter_statuses,
    }


@router.get("/channels/supported")
async def supported_channels() -> dict:
    """List all supported channel types."""
    return {
        "channels": _registry.supported_channels,
    }


@router.post("/channels/send")
async def send_channel_message(
    request: Request,
    payload: SendMessageRequest,
) -> dict:
    """
    Unified outbound message endpoint.

    Routes messages through the correct channel adapter based on
    the specified channel type.
    """
    db = request.app.state.db

    try:
        channel_type = ChannelType(payload.channel)
    except ValueError:
        raise HTTPException(
            400,
            f"Unsupported channel: {payload.channel}. Supported: {_registry.supported_channels}",
        )

    if not _registry.has(channel_type):
        raise HTTPException(
            400,
            f"No adapter registered for channel: {payload.channel}",
        )

    result = await _outbound.send_to_channel(
        tenant_id=payload.tenant_id,
        channel_type=channel_type,
        external_user_id=payload.recipient_id,
        content=payload.content,
        subject=payload.subject,
        metadata=payload.metadata,
        conversation_id=payload.conversation_id,
        db=db,
    )

    return {
        "success": result.success,
        "error": result.error,
        "external_message_id": result.external_message_id,
        "channel": result.channel_type.value,
    }


@router.post("/channels/webhook/{channel}")
async def unified_channel_webhook(
    request: Request,
    channel: str,
) -> dict:
    """
    Unified webhook endpoint for all channels.

    Each channel's adapter handles validation and normalization.
    This endpoint provides a single entry point for all inbound webhooks.

    Note: This is an OPTIONAL unified endpoint. The existing per-channel
    webhook endpoints in routers/webhooks.py continue to work. This
    endpoint can be used for new channel integrations.
    """
    try:
        channel_type = ChannelType(channel)
    except ValueError:
        raise HTTPException(400, f"Unknown channel: {channel}")

    adapter = _registry.get_or_none(channel_type)
    if adapter is None:
        raise HTTPException(400, f"No adapter for channel: {channel}")

    db = request.app.state.db
    raw_body = await request.body()

    increment_counter(
        "channel_layer.webhook.received",
        labels={"channel": channel},
    )

    # Validate the webhook
    is_valid = await adapter.validate_webhook(request, db, raw_body)
    if not is_valid:
        increment_counter(
            "channel_layer.webhook.rejected",
            labels={"channel": channel},
        )
        logger.warning(
            "Webhook validation failed channel=%s ip=%s",
            channel,
            client_ip_from_request(request),
        )
        raise HTTPException(401, "Invalid webhook signature")

    # Check replay
    fingerprint = build_replay_fingerprint(
        channel,
        signature=request.headers.get("X-Hub-Signature-256", ""),
        raw_body=raw_body,
    )
    await check_replay(channel, fingerprint)

    # Parse and normalize
    try:
        payload = json.loads(raw_body or b"{}")
    except Exception:
        raise HTTPException(400, "Invalid webhook payload")

    # Resolve tenant_id from payload or headers
    tenant_id = (
        request.headers.get("X-Tenant-ID", "")
        or request.headers.get("X-Company-Id", "")
        or str(payload.get("company_id") or "")
    ).strip()

    if not tenant_id:
        # Try to resolve from channel-specific metadata
        tenant_id = await _resolve_tenant_from_payload(db, channel_type, payload)

    if not tenant_id:
        source_ip = client_ip_from_request(request)
        await _enforce_unknown_tenant_rate_limit(channel, source_ip)
        increment_counter(
            "channel_layer.webhook.unknown_tenant",
            labels={"channel": channel},
        )
        logger.warning(
            "Webhook tenant resolution failed channel=%s ip=%s",
            channel,
            source_ip,
        )
        raise HTTPException(403, "Tenant could not be resolved for webhook payload")

    from shared.usage_guard import conversation_quota_allows

    if not await conversation_quota_allows(db, tenant_id):
        logger.warning(
            "channel_layer webhook skipped: conversation quota tenant=%s channel=%s",
            tenant_id,
            channel,
        )
        return {"status": "quota_exceeded", "channel": channel, "tenant_id": tenant_id}

    message = await adapter.receive_message(payload, db, tenant_id)
    message = await _normalizer.normalize(message, db)

    increment_counter(
        "channel_layer.webhook.processed",
        labels={"channel": channel},
    )

    logger.info(
        "Webhook processed channel=%s tenant=%s message_id=%s external_user=%s",
        channel,
        message.tenant_id,
        message.message_id,
        message.external_user_id,
    )

    # Forward normalized message to agent orchestrator pipeline (fire-and-forget)
    try:
        from agent_orchestrator.engine import build_orchestrator_engine
        from agent_orchestrator.schemas import MessageWorkflowRequest
        engine = build_orchestrator_engine(db)
        workflow_request = MessageWorkflowRequest(
            company_id=message.tenant_id,
            conversation_id=message.resolved_conversation_id or "",
            customer_id=message.resolved_customer_id or "",
            message_text=message.content,
            channel=message.channel_type.value,
            sender_contact=message.external_user_id,
            trace_id=message.trace_id or "",
            metadata=message.metadata,
        )
        create_safe_detached_task(
            db,
            engine.run_message_workflow(workflow_request),
            name=f"channel-webhook-orchestrator-{channel}",
            company_id=message.tenant_id,
            channel=message.channel_type.value,
            trace_id=message.trace_id or "",
            event_id=message.message_id,
            payload=message.metadata,
        )
    except Exception as exc:
        logger.warning(
            "Orchestrator dispatch failed for channel webhook channel=%s message_id=%s: %s",
            channel,
            message.message_id,
            exc,
        )

    return {
        "status": "received",
        "message_id": message.message_id,
        "channel": message.channel_type.value,
    }


async def _resolve_tenant_from_payload(
    db,
    channel_type: ChannelType,
    payload: dict,
) -> str:
    """Attempt to resolve tenant_id from channel-specific payload data."""
    if channel_type == ChannelType.WHATSAPP:
        for entry in payload.get("entry", []) or []:
            for change in (entry or {}).get("changes", []) or []:
                value = (change or {}).get("value", {}) or {}
                metadata = value.get("metadata", {}) or {}
                phone_number_id = str(metadata.get("phone_number_id") or "").strip()
                if phone_number_id:
                    try:
                        result = await db.fetchval(
                            "SELECT company_id FROM tenant_meta_config "
                            "WHERE channel='whatsapp' AND phone_number_id=$1 "
                            "AND is_active=TRUE LIMIT 1",
                            phone_number_id,
                        )
                        if result:
                            return str(result)
                    except Exception:
                        pass

    elif channel_type in (ChannelType.FACEBOOK, ChannelType.INSTAGRAM):
        for entry in payload.get("entry", []) or []:
            page_id = str((entry or {}).get("id") or "").strip()
            if page_id:
                try:
                    meta_channel = "facebook" if channel_type == ChannelType.FACEBOOK else "instagram"
                    # Check both business_account_id and page_id columns
                    result = await db.fetchval(
                        "SELECT company_id FROM tenant_meta_config "
                        "WHERE channel=$1 AND (business_account_id=$2 OR page_id=$2) "
                        "AND is_active=TRUE LIMIT 1",
                        meta_channel,
                        page_id,
                    )
                    if result:
                        return str(result)
                except Exception:
                    pass

    return ""
