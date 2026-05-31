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
from shared.config import dedup_cache_ttl_seconds
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


def _channel_layer_dedup_cache_key(channel: str, tenant_id: str, key: str) -> str:
    return f"{str(tenant_id or '').strip()}:{str(channel or '').strip()}:{str(key or '').strip()}"


def _channel_layer_idempotency_key(channel: str, tenant_id: str, provider_event_id: str) -> str:
    provider_id = str(provider_event_id or "").strip()
    if not provider_id:
        return ""
    return f"channel_webhook:{str(tenant_id or '').strip()}:{str(channel or '').strip()}:{provider_id}"


def _extract_provider_event_id(channel_type: ChannelType, payload: dict) -> str:
    direct = str(
        (payload or {}).get("provider_event_id")
        or (payload or {}).get("message_id")
        or (payload or {}).get("message_id_header")
        or (payload or {}).get("client_message_id")
        or (payload or {}).get("event_id")
        or (payload or {}).get("id")
        or ""
    ).strip()
    if direct:
        return direct

    if channel_type == ChannelType.WHATSAPP:
        for entry in (payload or {}).get("entry", []) or []:
            for change in (entry or {}).get("changes", []) or []:
                value = (change or {}).get("value", {}) or {}
                for msg in value.get("messages", []) or []:
                    mid = str((msg or {}).get("provider_event_id") or (msg or {}).get("id") or "").strip()
                    if mid:
                        return mid

    if channel_type in (ChannelType.FACEBOOK, ChannelType.INSTAGRAM):
        for entry in (payload or {}).get("entry", []) or []:
            for event in (entry or {}).get("messaging", []) or []:
                message = (event or {}).get("message", {}) or {}
                postback = (event or {}).get("postback", {}) or {}
                mid = str(
                    message.get("mid")
                    or postback.get("mid")
                    or (event or {}).get("message_id")
                    or ""
                ).strip()
                if mid:
                    return mid
            for change in (entry or {}).get("changes", []) or []:
                value = (change or {}).get("value", {}) or {}
                mid = str(
                    value.get("comment_id")
                    or value.get("id")
                    or value.get("media_id")
                    or ""
                ).strip()
                if mid:
                    return mid

    return ""


def _log_channel_inbound_pipeline_stage(
    stage: str,
    channel: str,
    *,
    tenant_id: str = "",
    provider_event_id: str = "",
    idempotency_key: str = "",
    trace_id: str = "",
    duplicate: bool | None = None,
    message_id: str = "",
) -> None:
    duplicate_value = "" if duplicate is None else str(bool(duplicate)).lower()
    logger.info(
        "inbound_pipeline channel=%s stage=%s tenant_id=%s provider_event_id=%s idempotency_key=%s trace_id=%s duplicate=%s message_id=%s",
        str(channel or "").strip(),
        str(stage or "").strip(),
        str(tenant_id or "").strip(),
        str(provider_event_id or "").strip(),
        str(idempotency_key or "").strip(),
        str(trace_id or "").strip(),
        duplicate_value,
        str(message_id or "").strip(),
    )


async def _find_channel_layer_duplicate_message(
    db,
    *,
    tenant_id: str,
    provider_event_id: str = "",
    idempotency_key: str = "",
) -> dict:
    scoped_tenant = str(tenant_id or "").strip()
    provider_id = str(provider_event_id or "").strip()
    idem_key = str(idempotency_key or "").strip()
    if not db or not scoped_tenant or (not provider_id and not idem_key):
        return {}
    try:
        if provider_id:
            row = await db.fetchrow(
                "SELECT id,conversation_id FROM messages "
                "WHERE company_id=$1 AND external_message_id=$2 "
                "ORDER BY created_at DESC LIMIT 1",
                scoped_tenant,
                provider_id,
            )
            if row:
                return dict(row)
        if idem_key:
            row = await db.fetchrow(
                "SELECT id,conversation_id FROM messages "
                "WHERE company_id=$1 AND idempotency_key=$2 "
                "ORDER BY created_at DESC LIMIT 1",
                scoped_tenant,
                idem_key,
            )
            if row:
                return dict(row)
    except Exception as exc:
        logger.warning(
            "channel_layer inbound idempotency lookup failed tenant=%s provider_event_id=%s idempotency_key=%s: %s",
            scoped_tenant,
            provider_id,
            idem_key,
            exc,
        )
    return {}


async def _check_channel_layer_duplicate(
    db,
    *,
    channel: str,
    tenant_id: str,
    provider_event_id: str = "",
    idempotency_key: str = "",
    trace_id: str = "",
) -> dict:
    scoped_channel = str(channel or "").strip()
    scoped_tenant = str(tenant_id or "").strip()
    provider_id = str(provider_event_id or "").strip()
    idem_key = str(idempotency_key or "").strip()
    _log_channel_inbound_pipeline_stage(
        "event_received",
        scoped_channel,
        tenant_id=scoped_tenant,
        provider_event_id=provider_id,
        idempotency_key=idem_key,
        trace_id=trace_id,
    )
    if not scoped_tenant or (not provider_id and not idem_key):
        _log_channel_inbound_pipeline_stage(
            "deduplicated",
            scoped_channel,
            tenant_id=scoped_tenant,
            provider_event_id=provider_id,
            idempotency_key=idem_key,
            trace_id=trace_id,
            duplicate=False,
        )
        return {"duplicate": False}

    existing = await _find_channel_layer_duplicate_message(
        db,
        tenant_id=scoped_tenant,
        provider_event_id=provider_id,
        idempotency_key=idem_key,
    )
    if existing:
        _log_channel_inbound_pipeline_stage(
            "deduplicated",
            scoped_channel,
            tenant_id=scoped_tenant,
            provider_event_id=provider_id,
            idempotency_key=idem_key,
            trace_id=trace_id,
            duplicate=True,
            message_id=str(existing.get("id") or ""),
        )
        return {
            "duplicate": True,
            "dedup_stage": "message_store",
            "message_id": str(existing.get("id") or ""),
            "conversation_id": str(existing.get("conversation_id") or ""),
        }

    cache = get_cache_client(namespace="inbound_dedup")
    cache_material = idem_key or provider_id
    if cache_material:
        dedup_key = _channel_layer_dedup_cache_key(scoped_channel, scoped_tenant, cache_material)
        try:
            cached = await cache.get_json(dedup_key)
            if cached:
                cached_message_id = str((cached or {}).get("message_id") or provider_id or "").strip()
                _log_channel_inbound_pipeline_stage(
                    "deduplicated",
                    scoped_channel,
                    tenant_id=scoped_tenant,
                    provider_event_id=provider_id,
                    idempotency_key=idem_key,
                    trace_id=trace_id,
                    duplicate=True,
                    message_id=cached_message_id,
                )
                return {
                    "duplicate": True,
                    "dedup_stage": "cache",
                    "message_id": cached_message_id,
                    "conversation_id": str((cached or {}).get("conversation_id") or ""),
                }
            await cache.set_json(
                dedup_key,
                {
                    "channel": scoped_channel,
                    "tenant_id": scoped_tenant,
                    "provider_event_id": provider_id,
                    "idempotency_key": idem_key,
                    "trace_id": str(trace_id or ""),
                },
                ttl_seconds=dedup_cache_ttl_seconds(),
            )
        except Exception as exc:
            logger.warning(
                "channel_layer inbound dedup cache unavailable tenant=%s channel=%s provider_event_id=%s: %s",
                scoped_tenant,
                scoped_channel,
                provider_id,
                exc,
            )

    _log_channel_inbound_pipeline_stage(
        "deduplicated",
        scoped_channel,
        tenant_id=scoped_tenant,
        provider_event_id=provider_id,
        idempotency_key=idem_key,
        trace_id=trace_id,
        duplicate=False,
    )
    return {"duplicate": False}


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

    provider_event_id = _extract_provider_event_id(channel_type, payload)
    idempotency_key = str(payload.get("idempotency_key") or "").strip() or _channel_layer_idempotency_key(
        channel,
        tenant_id,
        provider_event_id,
    )
    trace_id = str(payload.get("trace_id") or request.headers.get("X-Trace-Id", "") or "").strip()
    dedup_result = await _check_channel_layer_duplicate(
        db,
        channel=channel,
        tenant_id=tenant_id,
        provider_event_id=provider_event_id,
        idempotency_key=idempotency_key,
        trace_id=trace_id,
    )
    if dedup_result.get("duplicate"):
        return {
            "status": "duplicate",
            "duplicate": True,
            "message_id": dedup_result.get("message_id", ""),
            "conversation_id": dedup_result.get("conversation_id", ""),
            "channel": channel,
            "tenant_id": tenant_id,
        }

    message = await adapter.receive_message(payload, db, tenant_id)
    message = await _normalizer.normalize(message, db)
    provider_event_id = provider_event_id or message.message_id
    idempotency_key = idempotency_key or _channel_layer_idempotency_key(
        message.channel_type.value,
        message.tenant_id,
        provider_event_id,
    )
    if trace_id and not message.trace_id:
        message.trace_id = trace_id
    message.metadata.update(
        {
            "provider_event_id": provider_event_id,
            "external_message_id": provider_event_id,
            "idempotency_key": idempotency_key,
            "trace_id": message.trace_id or trace_id,
        }
    )

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
        workflow_metadata = dict(message.metadata or {})
        if not str(workflow_metadata.get("message_id") or "").strip():
            workflow_metadata["message_id"] = message.message_id
        if not str(workflow_metadata.get("external_message_id") or "").strip():
            workflow_metadata["external_message_id"] = provider_event_id
        if not str(workflow_metadata.get("provider_event_id") or "").strip():
            workflow_metadata["provider_event_id"] = provider_event_id
        if not str(workflow_metadata.get("idempotency_key") or "").strip():
            workflow_metadata["idempotency_key"] = idempotency_key
        workflow_request = MessageWorkflowRequest(
            company_id=message.tenant_id,
            conversation_id=message.resolved_conversation_id or "",
            customer_id=message.resolved_customer_id or "",
            message_id=message.message_id,
            external_message_id=provider_event_id,
            provider_event_id=provider_event_id,
            idempotency_key=workflow_metadata["idempotency_key"],
            message_text=message.content,
            channel=message.channel_type.value,
            sender_contact=message.external_user_id,
            trace_id=message.trace_id or "",
            metadata=workflow_metadata,
        )
        _log_channel_inbound_pipeline_stage(
            "passed_to_pipeline",
            message.channel_type.value,
            tenant_id=message.tenant_id,
            provider_event_id=provider_event_id,
            idempotency_key=workflow_metadata["idempotency_key"],
            trace_id=message.trace_id or trace_id,
            message_id=message.message_id,
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
