"""routers/webhooks.py — Webhook endpoints using PostgreSQL."""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
import time
from agent_orchestrator.schemas import LeadWorkflowRequest, MessageWorkflowRequest
from channel_layer.normalizer import MessageNormalizer
from channel_layer.router import get_channel_registry, get_outbound_router
from channel_layer.schemas import (
    ChannelType,
    MessageDirection,
    UnifiedAttachment,
    UnifiedMessage,
)
from channel_layer.social_lead_detector import detect as detect_social_lead

from services.ai_service.facade import (
    build_sentiment_gate,
    wait_for_ai_response_timing,
)
from services.ai_service.common import estimate_tokens
from services.agent_orchestrator.facade import (
    orchestrate_lead_workflow,
    orchestrate_message_workflow,
)
from core.socket import emit_new_message
from core.utils import make_id, now_ts
from shared.background_queue import get_background_queue, serialize_coroutine
from shared.cache import get_cache_client
from shared.config import (
    ai_input_token_budget,
    ai_response_cooldown_seconds,
    dedup_cache_ttl_seconds,
    identity_tenant_api_keys,
    is_production,
    meta_webhook_event_replay_ttl_seconds,
    meta_webhook_rate_limit_per_minute,
    outbound_retry_base_delay_seconds,
    service_urls,
    unprocessed_event_max_retries,
    unprocessed_event_retry_base_seconds,
    webhook_identity_resolve_timeout_seconds,
    webhook_message_history_fetch_limit,
    webhook_replay_ttl_seconds,
    webhook_signature_max_skew_seconds,
)
from shared.tracing import current_trace_context
from shared.webhook_task_runner import create_safe_detached_task
from services.billing_helpers import relaxed_billing_env
from shared.usage_guard import (
    inbound_conversation_billing_precheck,
    insert_conversation_usage_relaxed,
    insert_conversation_usage_row,
)
from services.db_helpers import (
    convert_lead_to_customer_state,
    escalate_conversation_to_human,
    fetch_messages_with_attachments,
    get_current_user_flexible,
    is_company_ai_enabled,
    insert_chat_history_record,
    normalize_customer_contact_phone,
    persist_ai_session_record,
    persist_chat_history,
    r,
    record_webhook_event,
    resolve_customer_by_contact,
    save_message_attachments,
    upsert_customer_social_profile,
    _notify_agents_handoff,
)
from services.messaging_service import _persist_outbound_message_state
from services.meta_service import (
    decrypt_meta_secret,
    fetch_user_profile,
    get_meta_config,
    process_delivery_status_webhook,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_IDENTITY_BASE_URL = service_urls().identity.rstrip("/")
_WEBHOOK_REPLAY_TTL_SECONDS = webhook_replay_ttl_seconds()
_WEBHOOK_MAX_SKEW_SECONDS = webhook_signature_max_skew_seconds()
_META_WEBHOOK_RATE_LIMIT_PER_MINUTE = meta_webhook_rate_limit_per_minute()
_META_EVENT_REPLAY_TTL_SECONDS = meta_webhook_event_replay_ttl_seconds()
_UNPROCESSED_EVENT_MAX_RETRIES = unprocessed_event_max_retries()
_UNPROCESSED_EVENT_RETRY_BASE_SECONDS = unprocessed_event_retry_base_seconds()
_UNPROCESSED_SCHEMA_READY = False
_UNPROCESSED_SCHEMA_LOCK = asyncio.Lock()
_CHANNEL_NORMALIZER = MessageNormalizer()


def _db(req):
    return req.app.state.db


def _float_or_none(value):
    try:
        return float(value)
    except Exception:
        return None


def _looks_like_phone(value: str) -> bool:
    digits = re.sub(r"\D", "", str(value or "").strip())
    return len(digits) >= 7


def _extract_sender_contact_fields(channel: str, sender_contact: str, metadata_payload: dict | None) -> dict[str, str]:
    payload = dict(metadata_payload or {})
    normalized_channel = str(channel or "").strip().lower()
    raw_contact = str(sender_contact or "").strip()
    metadata_contact = str(payload.get("sender_contact") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    social_profile_id = str(payload.get("social_profile_id") or "").strip()
    channel_id = raw_contact or session_id or metadata_contact or social_profile_id

    email = ""
    phone = ""
    profile_id = ""
    if normalized_channel == "whatsapp":
        phone = raw_contact or metadata_contact
        channel_id = phone or channel_id
    elif normalized_channel == "email":
        email = (raw_contact or metadata_contact).lower()
        channel_id = email or channel_id
    elif normalized_channel in {"facebook", "instagram"}:
        profile_id = social_profile_id or raw_contact or metadata_contact
        channel_id = profile_id or channel_id
    elif normalized_channel == "web_chat":
        candidate = metadata_contact or raw_contact
        if "@" in candidate:
            email = candidate.lower()
        elif _looks_like_phone(candidate):
            phone = candidate
        channel_id = session_id or channel_id
    else:
        candidate = metadata_contact or raw_contact
        if "@" in candidate:
            email = candidate.lower()
        elif _looks_like_phone(candidate):
            phone = candidate

    return {
        "email": email,
        "phone": phone,
        "social_profile_id": profile_id,
        "channel_id": str(channel_id or "").strip(),
    }


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _client_ip_from_request(request: Request) -> str:
    forwarded = (request.headers.get("X-Forwarded-For") or "").strip()
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return (request.client.host if request.client else "").strip() or "unknown"


def _meta_allowlist_networks() -> list[Any]:
    raw = (os.environ.get("META_WEBHOOK_IP_ALLOWLIST") or "").strip()
    if not raw:
        return []
    networks: list[ipaddress._BaseNetwork] = []
    for token in raw.split(","):
        candidate = token.strip()
        if not candidate:
            continue
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError:
            logger.warning("Ignoring invalid META_WEBHOOK_IP_ALLOWLIST entry=%s", candidate)
    return networks


def _normalize_unix_timestamp(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text or not text.isdigit():
        return None
    parsed = int(text)
    if parsed > 10_000_000_000:
        parsed = parsed // 1000
    return parsed if parsed > 0 else None


def _extract_meta_payload_timestamp(payload: dict) -> int | None:
    timestamps: list[int] = []
    for entry in payload.get("entry", []) or []:
        entry_time = _normalize_unix_timestamp((entry or {}).get("time"))
        if entry_time:
            timestamps.append(entry_time)
        for change in (entry or {}).get("changes", []) or []:
            value = (change or {}).get("value", {}) or {}
            for status_item in value.get("statuses", []) or []:
                status_ts = _normalize_unix_timestamp((status_item or {}).get("timestamp"))
                if status_ts:
                    timestamps.append(status_ts)
            for message_item in value.get("messages", []) or []:
                msg_ts = _normalize_unix_timestamp((message_item or {}).get("timestamp"))
                if msg_ts:
                    timestamps.append(msg_ts)
    return max(timestamps) if timestamps else None


def _assert_meta_payload_timestamp_fresh(request: Request, payload: dict, channel: str) -> int:
    header_ts = (
        request.headers.get("X-Hub-Signature-Timestamp")
        or request.headers.get("X-Meta-Timestamp")
        or request.headers.get("X-Webhook-Timestamp")
        or ""
    )
    ts = _normalize_unix_timestamp(header_ts)
    if ts is None:
        ts = _extract_meta_payload_timestamp(payload)
    if ts is None:
        logger.warning(
            "Meta webhook missing timestamp channel=%s ip=%s",
            channel,
            _client_ip_from_request(request),
        )
        raise HTTPException(401, "Missing webhook timestamp")
    current_ts = int(datetime.now(timezone.utc).timestamp())
    if abs(current_ts - ts) > _WEBHOOK_MAX_SKEW_SECONDS:
        logger.warning(
            "Meta webhook timestamp rejected channel=%s ts=%s now=%s ip=%s",
            channel,
            ts,
            current_ts,
            _client_ip_from_request(request),
        )
        raise HTTPException(401, "Webhook timestamp is outside accepted window")
    return ts


def _extract_meta_event_id(
    request: Request,
    *,
    channel: str,
    payload: dict,
    raw_body: bytes,
    signature: str,
    timestamp: int,
) -> str:
    explicit = (
        request.headers.get("X-Meta-Event-Id")
        or request.headers.get("X-Hub-Delivery")
        or request.headers.get("X-Webhook-Id")
        or ""
    ).strip()
    if explicit:
        return explicit

    ids: list[str] = []
    for entry in payload.get("entry", []) or []:
        entry_id = str((entry or {}).get("id") or "").strip()
        if entry_id:
            ids.append(entry_id)
        for change in (entry or {}).get("changes", []) or []:
            value = (change or {}).get("value", {}) or {}
            for status_item in value.get("statuses", []) or []:
                status_id = str((status_item or {}).get("id") or "").strip()
                if status_id:
                    ids.append(status_id)
            for msg in value.get("messages", []) or []:
                msg_id = str((msg or {}).get("id") or "").strip()
                if msg_id:
                    ids.append(msg_id)
        for event in (entry or {}).get("messaging", []) or []:
            sender_id = str(((event or {}).get("sender") or {}).get("id") or "").strip()
            recipient_id = str(((event or {}).get("recipient") or {}).get("id") or "").strip()
            if sender_id:
                ids.append(sender_id)
            if recipient_id:
                ids.append(recipient_id)
            delivery = (event or {}).get("delivery", {}) or {}
            for mid in delivery.get("mids", []) or []:
                mid_id = str(mid or "").strip()
                if mid_id:
                    ids.append(mid_id)

    unique_ids = sorted({item for item in ids if item})
    base = {
        "channel": channel,
        "timestamp": timestamp,
        "signature": signature,
        "ids": unique_ids[:20],
        "body_hash": hashlib.sha256(raw_body).hexdigest(),
    }
    return hashlib.sha256(json.dumps(base, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()


async def _enforce_meta_webhook_rate_limit(request: Request, channel: str) -> None:
    cache = get_cache_client(namespace="meta_webhook_rate_limit")
    ip = _client_ip_from_request(request)
    minute_bucket = int(datetime.now(timezone.utc).timestamp() // 60)
    key = f"{channel}:{ip}:{minute_bucket}"
    state = await cache.get_json(key)
    count = int((state or {}).get("count") or 0) + 1
    await cache.set_json(key, {"count": count}, ttl_seconds=70)
    if count > _META_WEBHOOK_RATE_LIMIT_PER_MINUTE:
        logger.warning(
            "Meta webhook rate limit exceeded channel=%s ip=%s count=%s limit=%s",
            channel,
            ip,
            count,
            _META_WEBHOOK_RATE_LIMIT_PER_MINUTE,
        )
        raise HTTPException(429, "Webhook rate limit exceeded")


def _enforce_meta_webhook_ip_allowlist(request: Request, channel: str) -> None:
    allowlist = _meta_allowlist_networks()
    if not allowlist:
        return
    ip_text = _client_ip_from_request(request)
    try:
        ip_value = ipaddress.ip_address(ip_text)
    except ValueError:
        logger.warning(
            "Meta webhook rejected due to invalid client IP channel=%s ip=%s",
            channel,
            ip_text,
        )
        raise HTTPException(403, "Webhook source is not allowed")
    if any(ip_value in network for network in allowlist):
        return
    logger.warning(
        "Meta webhook rejected by IP allowlist channel=%s ip=%s",
        channel,
        ip_text,
    )
    raise HTTPException(403, "Webhook source is not allowed")


async def _check_meta_event_replay(channel: str, event_id: str) -> None:
    cache = get_cache_client(namespace="meta_webhook_event_replay")
    key = f"{channel}:{event_id}"
    if await cache.get_json(key):
        logger.warning("Meta webhook replay detected channel=%s event_id=%s", channel, event_id)
        raise HTTPException(409, "Replay webhook event blocked")
    await cache.set_json(key, {"seen": True}, ttl_seconds=_META_EVENT_REPLAY_TTL_SECONDS)


def _meta_webhook_secret(channel: str) -> str:
    channel_map = {
        "whatsapp": "WHATSAPP_WEBHOOK_SECRET",
        "facebook": "FACEBOOK_WEBHOOK_SECRET",
        "instagram": "INSTAGRAM_WEBHOOK_SECRET",
        "lead_form": "FACEBOOK_WEBHOOK_SECRET",
    }
    specific = (os.environ.get(channel_map.get(channel, "")) or "").strip()
    shared = (os.environ.get("META_WEBHOOK_SECRET") or "").strip()
    secret = specific or shared
    if not secret:
        raise HTTPException(503, "Webhook signing secret is not configured")
    return secret


def _append_unique_secret(secrets: list[str], value: str) -> None:
    secret = (value or "").strip()
    if secret and secret not in secrets:
        secrets.append(secret)


async def _candidate_meta_webhook_secrets(db, channel: str, raw_body: bytes) -> list[str]:
    secrets: list[str] = []
    try:
        _append_unique_secret(secrets, _meta_webhook_secret(channel))
    except HTTPException:
        pass

    payload = _decode_webhook_json(raw_body)
    candidate_company_ids: set[str] = set()
    if channel == "whatsapp":
        for entry in payload.get("entry", []) or []:
            business_account_id = str((entry or {}).get("id") or "").strip()
            if business_account_id:
                resolved = await _resolve_inbound_company_id(
                    db,
                    "whatsapp",
                    {"business_account_id": business_account_id},
                )
                if resolved:
                    candidate_company_ids.add(resolved)
            for change in (entry or {}).get("changes", []) or []:
                value = (change or {}).get("value", {}) or {}
                metadata = value.get("metadata", {}) or {}
                phone_number_id = str(metadata.get("phone_number_id") or "").strip()
                if phone_number_id:
                    resolved = await _resolve_inbound_company_id(
                        db,
                        "whatsapp",
                        {"phone_number_id": phone_number_id},
                    )
                    if resolved:
                        candidate_company_ids.add(resolved)
    else:
        target_channel = "facebook" if channel == "lead_form" else channel
        for entry in payload.get("entry", []) or []:
            page_id = str((entry or {}).get("id") or "").strip()
            if not page_id:
                continue
            resolved = await _resolve_inbound_company_id(
                db,
                target_channel if target_channel in {"facebook", "instagram"} else channel,
                {"page_id": page_id},
            )
            if resolved:
                candidate_company_ids.add(resolved)

    if candidate_company_ids:
        target_channel = "facebook" if channel == "lead_form" else channel
        rows = await db.fetch(
            "SELECT webhook_secret_enc FROM tenant_meta_config "
            "WHERE company_id = ANY($1::text[]) AND channel=$2 AND is_active=TRUE",
            list(candidate_company_ids),
            target_channel,
        )
        for row in rows:
            encrypted_secret = str((dict(row) if row else {}).get("webhook_secret_enc") or "").strip()
            if not encrypted_secret:
                continue
            try:
                _append_unique_secret(secrets, decrypt_meta_secret(encrypted_secret))
            except HTTPException:
                logger.warning(
                    "Failed to decrypt tenant webhook secret channel=%s company_candidates=%s",
                    channel,
                    sorted(candidate_company_ids),
                )
    return secrets


def _require_external_secret(env_name: str) -> str:
    value = (os.environ.get(env_name) or "").strip()
    if not value:
        raise HTTPException(503, f"{env_name} is not configured")
    return value


def _decode_webhook_json(raw_body: bytes) -> dict:
    try:
        text = raw_body.decode("utf-8") if raw_body else "{}"
        payload = json.loads(text or "{}")
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        raise HTTPException(400, "Invalid webhook payload") from exc


def _extract_hex_signature(raw_value: str) -> str:
    token = (raw_value or "").strip()
    if not token:
        return ""
    if "=" in token:
        _, token = token.split("=", 1)
    return token.strip().lower()


def _verify_hmac_sha256(secret: str, message: bytes, provided_signature: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return bool(provided_signature) and hmac.compare_digest(expected, provided_signature)


async def _check_replay(channel: str, replay_fingerprint: str) -> None:
    from channel_layer.validators import check_replay as _channel_check_replay

    await _channel_check_replay(channel, replay_fingerprint)


def _is_trusted_whatsapp_web_bridge_request(request: Request) -> bool:
    """Node WhatsApp Web.js bridge is internal. It signs with the app webhook secret; tenant DB secrets
    may not match. When X-Bridge-Secret matches env, skip Meta multi-secret HMAC.
    """
    expected = (os.environ.get("WHATSAPP_BRIDGE_SECRET") or os.environ.get("BRIDGE_SECRET") or "").strip()
    provided = (request.headers.get("X-Bridge-Secret") or "").strip()
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided, expected)


async def _verify_meta_webhook_request(
    request: Request,
    db,
    channel: str,
    raw_body: bytes,
) -> str:
    bridge_trusted = channel == "whatsapp" and _is_trusted_whatsapp_web_bridge_request(request)
    await _enforce_meta_webhook_rate_limit(request, channel)
    if not bridge_trusted:
        _enforce_meta_webhook_ip_allowlist(request, channel)

    signature_header = request.headers.get("X-Hub-Signature-256", "")
    provided_signature = _extract_hex_signature(signature_header)
    if not bridge_trusted:
        if not provided_signature:
            logger.warning(
                "Meta webhook missing signature channel=%s ip=%s",
                channel,
                _client_ip_from_request(request),
            )
            raise HTTPException(401, "Missing webhook signature")
        candidate_secrets = await _candidate_meta_webhook_secrets(db, channel, raw_body)
        if not candidate_secrets:
            raise HTTPException(503, "Webhook signing secret is not configured")
        if not any(_verify_hmac_sha256(secret, raw_body, provided_signature) for secret in candidate_secrets):
            logger.warning(
                "Meta webhook invalid signature channel=%s ip=%s",
                channel,
                _client_ip_from_request(request),
            )
            raise HTTPException(401, "Invalid webhook signature")
    elif not provided_signature:
        provided_signature = hashlib.sha256(raw_body).hexdigest()

    payload = _decode_webhook_json(raw_body)
    event_timestamp = _assert_meta_payload_timestamp_fresh(request, payload, channel)
    event_id = _extract_meta_event_id(
        request,
        channel=channel,
        payload=payload,
        raw_body=raw_body,
        signature=provided_signature,
        timestamp=event_timestamp,
    )
    await _check_meta_event_replay(channel, event_id)

    replay_fingerprint = hashlib.sha256(f"{channel}:{provided_signature}".encode("utf-8") + raw_body).hexdigest()
    await _check_replay(channel, replay_fingerprint)
    return event_id


def _assert_timestamp_fresh(timestamp_raw: str) -> int:
    try:
        ts = int((timestamp_raw or "").strip())
    except Exception as exc:
        raise HTTPException(401, "Missing or invalid webhook timestamp") from exc
    now_ts_value = int(datetime.now(timezone.utc).timestamp())
    if abs(now_ts_value - ts) > _WEBHOOK_MAX_SKEW_SECONDS:
        raise HTTPException(401, "Webhook timestamp is outside accepted window")
    return ts


async def _verify_signed_header_webhook(
    request: Request,
    *,
    channel: str,
    secret_env: str,
    raw_body: bytes,
) -> None:
    secret = _require_external_secret(secret_env)
    timestamp = _assert_timestamp_fresh(request.headers.get("X-Webhook-Timestamp", ""))
    signature = _extract_hex_signature(request.headers.get("X-Webhook-Signature", ""))
    if not signature:
        raise HTTPException(401, "Missing webhook signature")
    signed_payload = f"{timestamp}.".encode("utf-8") + raw_body
    if not _verify_hmac_sha256(secret, signed_payload, signature):
        raise HTTPException(401, "Invalid webhook signature")

    replay_id = (request.headers.get("X-Webhook-Id") or "").strip()
    fingerprint_source = replay_id or f"{timestamp}:{signature}"
    replay_fingerprint = hashlib.sha256(f"{channel}:{fingerprint_source}".encode("utf-8") + raw_body).hexdigest()
    await _check_replay(channel, replay_fingerprint)


async def _resolve_web_chat_widget_key(db, company_id: str) -> str:
    scoped_company_id = (company_id or "").strip()
    if not scoped_company_id:
        return ""
    try:
        value = await db.fetchval(
            "SELECT api_key FROM channel_settings WHERE company_id=$1 AND channel='web_chat' AND enabled=TRUE LIMIT 1",
            scoped_company_id,
        )
        return (value or "").strip()
    except Exception as exc:
        logger.warning(
            "Failed to resolve web chat widget key company_id=%s: %s",
            scoped_company_id,
            exc,
        )
        return ""


async def _verify_web_chat_widget_request(
    request: Request,
    db,
    payload: dict,
) -> None:
    company_id = str((payload or {}).get("company_id") or "").strip()
    if not company_id:
        raise HTTPException(400, "company_id is required")

    provided_widget_key = (
        request.headers.get("X-Pulse-Widget-Key", "") or str((payload or {}).get("widget_key") or "")
    ).strip()
    configured_widget_key = await _resolve_web_chat_widget_key(db, company_id)
    if not configured_widget_key:
        configured_widget_key = (os.environ.get("WEB_CHAT_WIDGET_KEY") or "").strip()

    if configured_widget_key:
        if not provided_widget_key or not hmac.compare_digest(
            provided_widget_key,
            configured_widget_key,
        ):
            raise HTTPException(401, "Invalid widget key")
    else:
        allow_unsigned = _is_truthy(os.environ.get("ALLOW_UNSIGNED_WEB_CHAT_WIDGET", "true"))
        if is_production() or not allow_unsigned:
            raise HTTPException(
                401,
                "Unsigned web chat widget requests are not allowed",
            )

    client_message_id = (
        str((payload or {}).get("client_message_id") or "").strip()
        or (request.headers.get("X-Client-Message-Id") or "").strip()
    )
    if client_message_id:
        session_id = str((payload or {}).get("session_id") or "").strip()
        replay_fingerprint = hashlib.sha256(
            f"{company_id}:{session_id}:{client_message_id}".encode("utf-8")
        ).hexdigest()
        await _check_replay("web_chat_widget", replay_fingerprint)


async def _verify_external_purchase_auth(request: Request, raw_body: bytes, legacy_query_token: str | None) -> None:
    allow_legacy_query_token = _is_truthy(os.environ.get("ALLOW_LEGACY_EXTERNAL_WEBHOOK_QUERY_TOKEN"))
    if allow_legacy_query_token:
        expected_legacy_token = (os.environ.get("EXTERNAL_WEBHOOK_TOKEN") or "").strip()
        supplied_legacy_token = (legacy_query_token or "").strip()
        if (
            expected_legacy_token
            and supplied_legacy_token
            and hmac.compare_digest(expected_legacy_token, supplied_legacy_token)
        ):
            replay_fingerprint = hashlib.sha256(b"external_purchase_legacy" + raw_body).hexdigest()
            await _check_replay("external_purchase", replay_fingerprint)
            return
    await _verify_signed_header_webhook(
        request,
        channel="external_purchase",
        secret_env="EXTERNAL_WEBHOOK_SECRET",
        raw_body=raw_body,
    )


async def _is_valid_verify_token(db, channel: str, hub_token: str) -> bool:
    provided = (hub_token or "").strip()
    if not provided:
        return False
    base = (os.environ.get("WEBHOOK_VERIFY_TOKEN") or "").strip()
    if base and hmac.compare_digest(provided, base):
        return True
    matches = await db.fetchval(
        "SELECT COUNT(*) FROM channel_settings WHERE channel=$1 AND enabled=TRUE AND verify_token=$2",
        channel,
        provided,
    )
    if int(matches or 0) > 0:
        return True
    meta_channel = "facebook" if channel == "lead_form" else channel
    meta_matches = await db.fetchval(
        "SELECT COUNT(*) FROM tenant_meta_config WHERE channel=$1 AND is_active=TRUE AND verify_token=$2",
        meta_channel,
        provided,
    )
    return int(meta_matches or 0) > 0


async def _record_webhook_event_safe(
    db,
    channel: str,
    payload: dict,
    *,
    metadata: Optional[dict] = None,
    external_id: str = "",
    resolved_company_id: str = "",
    allow_direct_company_id: bool = True,
) -> None:
    try:
        company_id = (resolved_company_id or "").strip()
        if not company_id:
            company_id = await _resolve_inbound_company_id(
                db,
                channel,
                metadata,
                allow_direct_company_id=allow_direct_company_id,
            )
        if not company_id:
            logger.warning(
                "Skipping webhook event audit because company_id is unresolved channel=%s",
                channel,
            )
            return
        await record_webhook_event(
            db,
            channel,
            json.dumps(payload),
            "received",
            external_id=external_id,
            company_id=company_id,
        )
    except Exception as exc:
        logger.warning("Webhook event audit failed channel=%s error=%s", channel, exc)


async def _ensure_unprocessed_events_table(db) -> None:
    global _UNPROCESSED_SCHEMA_READY
    if _UNPROCESSED_SCHEMA_READY:
        return
    async with _UNPROCESSED_SCHEMA_LOCK:
        if _UNPROCESSED_SCHEMA_READY:
            return
        await db.execute(
            "CREATE TABLE IF NOT EXISTS unprocessed_events ("
            "id TEXT PRIMARY KEY,"
            "channel TEXT NOT NULL,"
            "event_id TEXT NOT NULL,"
            "raw_payload TEXT NOT NULL DEFAULT '',"
            "metadata TEXT NOT NULL DEFAULT '',"
            "reason TEXT NOT NULL DEFAULT '',"
            "status TEXT NOT NULL DEFAULT 'pending',"
            "retry_count INTEGER NOT NULL DEFAULT 0,"
            "next_retry_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            "last_error TEXT NOT NULL DEFAULT '',"
            "resolved_company_id TEXT NOT NULL DEFAULT '',"
            "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            "resolved_at TIMESTAMPTZ"
            ")"
        )
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_unprocessed_events_channel_event "
            "ON unprocessed_events(channel, event_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_unprocessed_events_status_retry "
            "ON unprocessed_events(status, next_retry_at)"
        )
        _UNPROCESSED_SCHEMA_READY = True


async def _store_unprocessed_event(
    db,
    *,
    channel: str,
    event_id: str,
    payload: dict,
    metadata: Optional[dict] = None,
    reason: str,
    retry_count: int = 0,
    next_retry_at: datetime | None = None,
    last_error: str = "",
) -> None:
    await _ensure_unprocessed_events_table(db)
    payload_text = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=True)
    metadata_text = json.dumps(metadata or {}, separators=(",", ":"), ensure_ascii=True)
    await db.execute(
        "INSERT INTO unprocessed_events("
        "id,channel,event_id,raw_payload,metadata,reason,status,retry_count,next_retry_at,last_error,updated_at,created_at"
        ") VALUES($1,$2,$3,$4,$5,$6,'pending',$7,$8,$9,NOW(),NOW()) "
        "ON CONFLICT (channel, event_id) DO UPDATE SET "
        "raw_payload=EXCLUDED.raw_payload, metadata=EXCLUDED.metadata, reason=EXCLUDED.reason, "
        "retry_count=GREATEST(unprocessed_events.retry_count, EXCLUDED.retry_count), "
        "next_retry_at=LEAST(unprocessed_events.next_retry_at, EXCLUDED.next_retry_at), "
        "last_error=EXCLUDED.last_error, updated_at=NOW()",
        make_id(),
        channel,
        event_id,
        payload_text,
        metadata_text,
        reason,
        max(0, int(retry_count)),
        next_retry_at or datetime.now(timezone.utc),
        (last_error or "")[:500],
    )


async def _mark_unprocessed_event_resolved(
    db,
    *,
    channel: str,
    event_id: str,
    company_id: str,
) -> None:
    await _ensure_unprocessed_events_table(db)
    await db.execute(
        "UPDATE unprocessed_events SET status='resolved', resolved_company_id=$1, "
        "resolved_at=NOW(), updated_at=NOW() WHERE channel=$2 AND event_id=$3",
        (company_id or "").strip(),
        channel,
        event_id,
    )


def _redact_inbound_metadata(value: Any) -> Any:
    secret_markers = (
        "secret",
        "token",
        "signature",
        "authorization",
        "password",
        "api_key",
        "access_key",
        "webhook_secret",
    )
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key or "")
            lowered = key_text.lower()
            if any(marker in lowered for marker in secret_markers):
                redacted[key_text] = "[redacted]"
                continue
            redacted[key_text] = _redact_inbound_metadata(item)
        return redacted
    if isinstance(value, list):
        return [_redact_inbound_metadata(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_inbound_metadata(item) for item in value]
    return value


def _derive_unprocessed_event_id(channel: str, metadata: Optional[dict], payload: Optional[dict], fallback: str = "") -> str:
    explicit = str(fallback or "").strip()
    if explicit:
        return explicit
    meta = dict(metadata or {})
    for key in (
        "event_id",
        "external_message_id",
        "inbound_external_message_id",
        "message_id",
        "mid",
    ):
        value = str(meta.get(key, "") or "").strip()
        if value:
            return value
    payload_blob = {
        "channel": str(channel or "").strip(),
        "metadata": _redact_inbound_metadata(meta),
        "payload": _redact_inbound_metadata(payload or {}),
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload_blob, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"{str(channel or 'event').strip() or 'event'}:{fingerprint[:40]}"


async def _validate_resolved_company_id(db, company_id: str, *, channel: str, descriptor: str) -> str:
    scoped_company_id = str(company_id or "").strip()
    if not scoped_company_id:
        return ""
    exists = await db.fetchval(
        "SELECT id FROM companies WHERE id=$1 AND deleted_at IS NULL LIMIT 1",
        scoped_company_id,
    )
    if exists:
        return scoped_company_id
    logger.error(
        "Resolved inbound tenant failed validation channel=%s descriptor=%s company_id=%s",
        channel,
        descriptor,
        scoped_company_id,
    )
    return ""


async def _store_unresolved_inbound_event(
    db,
    *,
    channel: str,
    metadata: Optional[dict],
    payload: Optional[dict],
    reason: str,
    event_id: str = "",
) -> None:
    safe_metadata = _redact_inbound_metadata(dict(metadata or {}))
    safe_payload = _redact_inbound_metadata(dict(payload or {}))
    derived_event_id = _derive_unprocessed_event_id(channel, safe_metadata, safe_payload, fallback=event_id)
    await _store_unprocessed_event(
        db,
        channel=channel,
        event_id=derived_event_id,
        payload=safe_payload,
        metadata=safe_metadata,
        reason=reason,
        last_error=reason,
    )


async def _retry_unprocessed_events(
    db,
    *,
    channel: str,
    limit: int = 20,
) -> None:
    await _ensure_unprocessed_events_table(db)
    rows = await db.fetch(
        "SELECT channel,event_id,raw_payload,retry_count FROM unprocessed_events "
        "WHERE status='pending' AND channel=$1 AND next_retry_at <= NOW() "
        "ORDER BY next_retry_at ASC LIMIT $2",
        channel,
        max(1, int(limit)),
    )
    if not rows:
        next_due_seconds = await db.fetchval(
            "SELECT EXTRACT(EPOCH FROM (MIN(next_retry_at) - NOW())) "
            "FROM unprocessed_events WHERE status='pending' AND channel=$1",
            channel,
        )
        if next_due_seconds is not None:
            delay_seconds = max(1.0, float(next_due_seconds) + 1.0)
            _schedule_unprocessed_retry(db, channel, delay_seconds=delay_seconds)
        return

    for row in rows:
        row_data = dict(row or {})
        event_id = str(row_data.get("event_id") or "").strip()
        retry_count = int(row_data.get("retry_count") or 0)
        raw_payload = str(row_data.get("raw_payload") or "").strip()
        payload = _decode_webhook_json(raw_payload.encode("utf-8"))
        try:
            if channel == "whatsapp":
                resolved = await _handle_whatsapp_webhook_payload(
                    db,
                    payload,
                    event_id=event_id,
                    store_unresolved=False,
                )
            elif channel == "facebook":
                resolved = await _handle_facebook_webhook_payload(
                    db,
                    payload,
                    event_id=event_id,
                    store_unresolved=False,
                )
            elif channel == "instagram":
                resolved = await _handle_instagram_webhook_payload(
                    db,
                    payload,
                    event_id=event_id,
                    store_unresolved=False,
                )
            elif channel == "lead_form":
                resolved = await _handle_lead_form_webhook_payload(
                    db,
                    payload,
                    event_id=event_id,
                    store_unresolved=False,
                )
            else:
                resolved = False
        except Exception as exc:
            resolved = False
            logger.warning(
                "Unprocessed event retry failed channel=%s event_id=%s retry=%s error=%s",
                channel,
                event_id,
                retry_count,
                exc,
            )

        if resolved:
            continue

        next_retry_count = retry_count + 1
        if next_retry_count >= _UNPROCESSED_EVENT_MAX_RETRIES:
            await db.execute(
                "UPDATE unprocessed_events SET status='failed', retry_count=$1, "
                "last_error='tenant_unresolved_after_retries', updated_at=NOW() "
                "WHERE channel=$2 AND event_id=$3",
                next_retry_count,
                channel,
                event_id,
            )
            continue

        delay_seconds = min(
            3600,
            _UNPROCESSED_EVENT_RETRY_BASE_SECONDS * (2 ** min(next_retry_count, 8)),
        )
        next_due_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        await db.execute(
            "UPDATE unprocessed_events SET retry_count=$1, next_retry_at=$2, updated_at=NOW() "
            "WHERE channel=$3 AND event_id=$4",
            next_retry_count,
            next_due_at,
            channel,
            event_id,
        )
        _schedule_unprocessed_retry(db, channel, delay_seconds=delay_seconds + 1)


async def _retry_unprocessed_events_after_delay(
    db,
    *,
    channel: str,
    delay_seconds: float,
) -> None:
    delay = max(0.0, float(delay_seconds or 0.0))
    if delay > 0:
        await asyncio.sleep(delay)
    await _retry_unprocessed_events(db, channel=channel)


async def _enqueue_delayed_unprocessed_retry(
    db,
    *,
    channel: str,
    delay_seconds: float,
) -> None:
    delay = max(0.0, float(delay_seconds or 0.0))
    if delay <= 0:
        await _retry_unprocessed_events(db, channel=channel)
        return

    queue = get_background_queue()
    if queue is None or not queue.enabled:
        await _retry_unprocessed_events_after_delay(
            db,
            channel=channel,
            delay_seconds=delay,
        )
        return

    retry_job_name = f"retry-unprocessed-{channel}"
    retry_job_id = f"{retry_job_name}:{make_id()}"
    coro = _retry_unprocessed_events(db, channel=channel)
    try:
        spec = serialize_coroutine(
            coro,
            name=retry_job_name,
            job_id=retry_job_id,
        )
    except Exception:
        try:
            coro.close()
        except Exception:
            pass
        await _retry_unprocessed_events_after_delay(
            db,
            channel=channel,
            delay_seconds=delay,
        )
        return

    try:
        now_epoch = time.time()
        spec["not_before"] = now_epoch + delay
        spec["queued_at"] = now_epoch
        await queue.enqueue_spec(spec)
    except Exception as exc:
        logger.warning(
            "Failed to enqueue delayed unprocessed retry channel=%s delay=%s error=%s",
            channel,
            delay,
            exc,
        )
        await _retry_unprocessed_events_after_delay(
            db,
            channel=channel,
            delay_seconds=delay,
        )
    finally:
        try:
            coro.close()
        except Exception:
            pass


def _schedule_unprocessed_retry(db, channel: str, *, delay_seconds: float = 0.0) -> None:
    retry_job_name = f"retry-unprocessed-{channel}"
    retry_job_id = f"{retry_job_name}:{make_id()}"
    timeout_seconds = None
    if delay_seconds and delay_seconds > 0:
        timeout_seconds = max(120.0, float(delay_seconds) + 60.0)
    create_safe_detached_task(
        db,
        _enqueue_delayed_unprocessed_retry(
            db,
            channel=channel,
            delay_seconds=delay_seconds,
        ),
        name=retry_job_name,
        job_id=retry_job_id,
        timeout_seconds=timeout_seconds,
        channel=channel,
        source_queue="unprocessed_event_retry",
    )


async def _resolve_single_company_id(
    db,
    query: str,
    *args,
    channel: str,
    descriptor: str,
) -> str:
    rows = await db.fetch(query, *args)
    company_ids = sorted(
        {
            (dict(row).get("company_id", "") or "").strip()
            for row in rows
            if (dict(row).get("company_id", "") or "").strip()
        }
    )
    if len(company_ids) == 1:
        return company_ids[0]
    if len(company_ids) > 1:
        logger.warning(
            "Inbound webhook tenant resolution is ambiguous channel=%s descriptor=%s company_ids=%s",
            channel,
            descriptor,
            ",".join(company_ids),
        )
    return ""


def _extract_messenger_status_updates(event: dict) -> list[dict]:
    updates: list[dict] = []
    delivery = event.get("delivery") if isinstance(event, dict) else None
    if isinstance(delivery, dict):
        watermark = str(delivery.get("watermark") or "").strip()
        mids = delivery.get("mids") if isinstance(delivery.get("mids"), list) else []
        for mid in mids:
            message_id = str(mid or "").strip()
            if message_id:
                updates.append(
                    {
                        "id": message_id,
                        "status": "delivered",
                        "timestamp": watermark,
                    }
                )

    read_event = event.get("read") if isinstance(event, dict) else None
    if isinstance(read_event, dict):
        watermark = str(read_event.get("watermark") or "").strip()
        mids = read_event.get("mids") if isinstance(read_event.get("mids"), list) else []
        for mid in mids:
            message_id = str(mid or "").strip()
            if message_id:
                updates.append(
                    {
                        "id": message_id,
                        "status": "read",
                        "timestamp": watermark,
                    }
                )
    return updates


async def _fetch_meta_sender_profile(
    db,
    *,
    company_id: str,
    channel: str,
    sender_id: str,
) -> dict:
    scoped_company_id = (company_id or "").strip()
    user_id = (sender_id or "").strip()
    if not scoped_company_id or not user_id:
        return {}
    try:
        config = await get_meta_config(
            db,
            scoped_company_id,
            channel=channel,
            include_secrets=True,
        )
        return await fetch_user_profile(
            db,
            scoped_company_id,
            config,
            user_id=user_id,
            channel=channel,
        )
    except HTTPException:
        return {}
    except Exception as exc:
        logger.warning(
            "Meta sender profile lookup failed channel=%s company_id=%s sender_id=%s: %s",
            channel,
            scoped_company_id,
            user_id,
            exc,
        )
        return {}


async def _persist_customer_social_identity(
    db,
    *,
    company_id: str,
    customer_id: str,
    channel: str,
    sender_id: str,
    profile: dict,
) -> None:
    scoped_company_id = (company_id or "").strip()
    cid = (customer_id or "").strip()
    platform = (channel or "").strip().lower()
    profile_id = (sender_id or "").strip()
    if not scoped_company_id or not cid or platform not in {"facebook", "instagram", "whatsapp"}:
        return

    try:
        if profile_id:
            await db.execute(
                "INSERT INTO customer_social_profiles(customer_id,platform,profile_id) VALUES($1,$2,$3) "
                "ON CONFLICT (customer_id,platform) DO UPDATE SET profile_id=EXCLUDED.profile_id",
                cid,
                platform,
                profile_id,
            )
        display_name = str((profile or {}).get("name") or "").strip()
        avatar_url = str((profile or {}).get("profile_picture") or "").strip()
        if display_name or avatar_url:
            await db.execute(
                "UPDATE customers SET "
                "name=CASE WHEN $1<>'' THEN $1 ELSE name END, "
                "avatar=CASE WHEN $2<>'' THEN $2 ELSE avatar END, "
                "updated_at=NOW() "
                "WHERE id=$3 AND company_id=$4",
                display_name,
                avatar_url,
                cid,
                scoped_company_id,
            )
    except Exception as exc:
        logger.warning(
            "Customer social profile persist failed company_id=%s customer_id=%s channel=%s sender_id=%s: %s",
            scoped_company_id,
            cid,
            platform,
            profile_id,
            exc,
        )


async def _handle_whatsapp_webhook_payload(
    db,
    payload: dict,
    *,
    event_id: str = "",
    store_unresolved: bool = True,
    allow_direct_company_id: bool = False,
) -> bool:
    resolved_company_id = ""
    dedup_cache = get_cache_client(namespace="inbound_dedup")
    try:
        registry = get_channel_registry()
        adapter = registry.get_or_none(ChannelType.WHATSAPP)
        if adapter is None:
            raise RuntimeError("WhatsApp adapter is not registered")

        processed_any = False
        unresolved_metadata: dict = {}
        for entry in payload.get("entry", []) or []:
            for change in (entry or {}).get("changes", []) or []:
                value = (change or {}).get("value", {}) or {}
                value_metadata = value.get("metadata", {}) or {}
                inbound_metadata = {
                    "phone_number_id": value_metadata.get("phone_number_id", ""),
                    "recipient_phone_number": value_metadata.get("display_phone_number", ""),
                    "business_account_id": (value.get("business_account_id", "") or (entry or {}).get("id", "")),
                    "company_id": value_metadata.get("company_id", ""),
                }
                unresolved_metadata = {k: v for k, v in inbound_metadata.items() if v}
                resolved_company_id = await _resolve_inbound_company_id(
                    db,
                    "whatsapp",
                    inbound_metadata,
                    allow_direct_company_id=allow_direct_company_id,
                    store_unprocessed=store_unresolved,
                    event_id=event_id,
                    payload=payload,
                )
                if not resolved_company_id:
                    logger.warning(
                        "Unknown tenant for signed Meta webhook channel=whatsapp event_id=%s metadata=%s",
                        event_id,
                        unresolved_metadata,
                    )
                    if store_unresolved:
                        _schedule_unprocessed_retry(db, "whatsapp")
                    continue

                await _record_webhook_event_safe(
                    db,
                    "whatsapp",
                    payload,
                    metadata=inbound_metadata,
                    resolved_company_id=resolved_company_id,
                    allow_direct_company_id=False,
                )
                messages = value.get("messages", []) or []
                contacts = value.get("contacts", []) or []

                raw_statuses = value.get("statuses", []) or []
                if raw_statuses:
                    await process_delivery_status_webhook(
                        db,
                        company_id=resolved_company_id,
                        channel="whatsapp",
                        statuses=raw_statuses,
                    )
                    processed_any = True
                    if not messages:
                        continue

                for i, msg in enumerate(messages):
                    sender_phone = str((msg or {}).get("from") or "").strip()
                    if not sender_phone:
                        continue

                    contact = contacts[i] if i < len(contacts) else {}
                    single_payload = {
                        "entry": [
                            {
                                "id": (entry or {}).get("id", ""),
                                "changes": [
                                    {
                                        "value": {
                                            "metadata": value_metadata,
                                            "messages": [msg],
                                            "contacts": [contact] if contact else [],
                                        }
                                    }
                                ],
                            }
                        ]
                    }

                    unified_message = await adapter.receive_message(
                        single_payload,
                        db,
                        resolved_company_id,
                    )
                    unified_message.trace_id = _trace_id_from_context(
                        str((unified_message.metadata or {}).get("trace_id") or "")
                    )
                    unified_message.metadata.update(
                        {
                            **inbound_metadata,
                            "company_id": resolved_company_id,
                            "source": "whatsapp_webhook",
                            "message_type": str((msg or {}).get("type") or "text"),
                            "event_id": event_id,
                            "trace_id": unified_message.trace_id,
                        }
                    )
                    unified_message = await _CHANNEL_NORMALIZER.normalize(unified_message, db)
                    dedup_message_id = str(
                        (msg or {}).get("id")
                        or unified_message.message_id
                        or (unified_message.metadata or {}).get("inbound_external_message_id")
                        or (unified_message.metadata or {}).get("external_message_id")
                        or ""
                    ).strip()
                    if dedup_message_id:
                        dedup_key = _dedup_cache_key("whatsapp", resolved_company_id, dedup_message_id)
                        try:
                            cached = await dedup_cache.get_json(dedup_key)
                            if cached:
                                logger.info(
                                    "Skipping duplicate WhatsApp inbound before persistence company_id=%s event_id=%s external_message_id=%s trace_id=%s",
                                    resolved_company_id,
                                    event_id,
                                    dedup_message_id,
                                    unified_message.trace_id,
                                )
                                continue
                            await dedup_cache.set_json(
                                dedup_key,
                                {
                                    "event_id": event_id,
                                    "message_id": dedup_message_id,
                                    "channel": "whatsapp",
                                },
                                ttl_seconds=dedup_cache_ttl_seconds(),
                            )
                        except Exception as exc:
                            logger.warning(
                                "WhatsApp inbound dedup cache unavailable company_id=%s event_id=%s external_message_id=%s error=%s",
                                resolved_company_id,
                                event_id,
                                dedup_message_id,
                                exc,
                            )

                    sender_name = (
                        str(((contact or {}).get("profile") or {}).get("name") or "").strip()
                        or str((unified_message.metadata or {}).get("profile_name") or "").strip()
                        or f"WhatsApp {sender_phone}"
                    )
                    if not (str(unified_message.content or "").strip() or unified_message.attachments):
                        continue

                    await _process_unified_incoming_message(
                        db,
                        unified_message,
                        sender_name=sender_name,
                        sender_contact=sender_phone,
                    )
                    processed_any = True

        if not processed_any:
            if store_unresolved and event_id:
                await _store_unprocessed_event(
                    db,
                    channel="whatsapp",
                    event_id=event_id,
                    payload=payload,
                    metadata=unresolved_metadata,
                    reason="tenant_unresolved",
                )
                _schedule_unprocessed_retry(db, "whatsapp")
            return False

        if event_id:
            await _mark_unprocessed_event_resolved(
                db,
                channel="whatsapp",
                event_id=event_id,
                company_id=resolved_company_id,
            )
        _schedule_unprocessed_retry(db, "whatsapp")
        return True
    except Exception as exc:
        logger.error("WhatsApp webhook error: %s", exc)
        if store_unresolved and event_id:
            await _store_unprocessed_event(
                db,
                channel="whatsapp",
                event_id=event_id,
                payload=payload,
                metadata={"resolved_company_id": resolved_company_id},
                reason="processing_error",
                last_error=str(exc),
            )
        return False


async def _handle_facebook_webhook_payload(
    db,
    payload: dict,
    *,
    event_id: str = "",
    store_unresolved: bool = True,
) -> bool:
    try:
        entries = payload.get("entry", []) or []
        entry_contexts: list[tuple[dict, str, str]] = []
        for entry in entries:
            page_id = str((entry or {}).get("id") or "").strip()
            company_id = await _resolve_inbound_company_id(
                db,
                "facebook",
                {"page_id": page_id},
                allow_direct_company_id=False,
                store_unprocessed=store_unresolved,
                event_id=event_id,
                payload=payload,
            )
            if not company_id:
                logger.warning(
                    "Unknown tenant for signed Meta webhook channel=facebook event_id=%s page_id=%s",
                    event_id,
                    page_id,
                )
                if store_unresolved:
                    _schedule_unprocessed_retry(db, "facebook")
                return False
            entry_contexts.append((entry, page_id, company_id))

        for entry, page_id, company_id in entry_contexts:
            await _record_webhook_event_safe(
                db,
                "facebook",
                payload,
                metadata={"page_id": page_id},
                resolved_company_id=company_id,
                allow_direct_company_id=False,
            )
            adapter = get_channel_registry().get_or_none(ChannelType.FACEBOOK)
            if adapter is None:
                raise RuntimeError("Facebook adapter is not registered")

            # Page "feed" webhook: comments / reactions / post activity.
            # These are not DMs but count as lead signals when the commenter
            # expresses interest; route through the social capture helper.
            for change in entry.get("changes") or []:
                try:
                    await _handle_social_change_event(
                        db,
                        channel="facebook",
                        change=change or {},
                        page_id=page_id,
                        company_id=company_id,
                        event_id=event_id,
                        adapter=adapter,
                    )
                except Exception as change_exc:
                    logger.warning(
                        "Facebook feed change handling failed: %s",
                        change_exc,
                    )

            for evt in entry.get("messaging", []):
                statuses = _extract_messenger_status_updates(evt or {})
                if statuses:
                    await process_delivery_status_webhook(
                        db,
                        company_id=company_id,
                        channel="facebook",
                        statuses=statuses,
                    )

                msg_payload = (evt or {}).get("message", {}) or {}
                if msg_payload.get("is_echo"):
                    continue
                sid = str(((evt or {}).get("sender", {}) or {}).get("id") or "").strip()
                if not sid:
                    continue

                single_payload = {
                    "entry": [
                        {
                            "id": page_id,
                            "messaging": [evt],
                        }
                    ]
                }
                unified_message = await adapter.receive_message(
                    single_payload,
                    db,
                    company_id,
                )
                unified_message.trace_id = _trace_id_from_context(
                    str((unified_message.metadata or {}).get("trace_id") or "")
                )
                unified_message.metadata.update(
                    {
                        "page_id": page_id,
                        "company_id": company_id,
                        "source": "facebook_webhook",
                        "event_id": event_id,
                        "trace_id": unified_message.trace_id,
                    }
                )
                unified_message = await _CHANNEL_NORMALIZER.normalize(unified_message, db)
                if not (str(unified_message.content or "").strip() or unified_message.attachments):
                    continue

                profile = await _fetch_meta_sender_profile(
                    db,
                    company_id=company_id,
                    channel="facebook",
                    sender_id=sid,
                )
                sender_name = str(profile.get("name") or "").strip() or f"Facebook User {sid[:8]}"
                processed = await _process_unified_incoming_message(
                    db,
                    unified_message,
                    sender_name=sender_name,
                    sender_contact=sid,
                )
                if processed:
                    await _persist_customer_social_identity(
                        db,
                        company_id=company_id,
                        customer_id=processed.get("customer_id", ""),
                        channel="facebook",
                        sender_id=sid,
                        profile=profile,
                    )

        if event_id and entry_contexts:
            await _mark_unprocessed_event_resolved(
                db,
                channel="facebook",
                event_id=event_id,
                company_id=entry_contexts[0][2],
            )
        _schedule_unprocessed_retry(db, "facebook")
        return True
    except Exception as exc:
        logger.error("Facebook webhook error: %s", exc)
        if store_unresolved and event_id:
            await _store_unprocessed_event(
                db,
                channel="facebook",
                event_id=event_id,
                payload=payload,
                metadata={},
                reason="processing_error",
                last_error=str(exc),
            )
        return False


async def _handle_instagram_webhook_payload(
    db,
    payload: dict,
    *,
    event_id: str = "",
    store_unresolved: bool = True,
) -> bool:
    try:
        entries = payload.get("entry", []) or []
        entry_contexts: list[tuple[dict, str, str]] = []
        for entry in entries:
            page_id = str((entry or {}).get("id") or "").strip()
            company_id = await _resolve_inbound_company_id(
                db,
                "instagram",
                {"page_id": page_id},
                allow_direct_company_id=False,
                store_unprocessed=store_unresolved,
                event_id=event_id,
                payload=payload,
            )
            if not company_id:
                logger.warning(
                    "Unknown tenant for signed Meta webhook channel=instagram event_id=%s page_id=%s",
                    event_id,
                    page_id,
                )
                if store_unresolved:
                    _schedule_unprocessed_retry(db, "instagram")
                return False
            entry_contexts.append((entry, page_id, company_id))

        for entry, page_id, company_id in entry_contexts:
            await _record_webhook_event_safe(
                db,
                "instagram",
                payload,
                metadata={"page_id": page_id},
                resolved_company_id=company_id,
                allow_direct_company_id=False,
            )
            adapter = get_channel_registry().get_or_none(ChannelType.INSTAGRAM)
            if adapter is None:
                raise RuntimeError("Instagram adapter is not registered")

            # Instagram "comments" / "mentions" / "story_insights" changes.
            for change in entry.get("changes") or []:
                try:
                    await _handle_social_change_event(
                        db,
                        channel="instagram",
                        change=change or {},
                        page_id=page_id,
                        company_id=company_id,
                        event_id=event_id,
                        adapter=adapter,
                    )
                except Exception as change_exc:
                    logger.warning(
                        "Instagram feed change handling failed: %s",
                        change_exc,
                    )

            for evt in entry.get("messaging", []):
                statuses = _extract_messenger_status_updates(evt or {})
                if statuses:
                    await process_delivery_status_webhook(
                        db,
                        company_id=company_id,
                        channel="instagram",
                        statuses=statuses,
                    )

                msg_payload = (evt or {}).get("message", {}) or {}
                if msg_payload.get("is_echo"):
                    continue
                sid = str(((evt or {}).get("sender", {}) or {}).get("id") or "").strip()
                if not sid:
                    continue

                single_payload = {
                    "entry": [
                        {
                            "id": page_id,
                            "messaging": [evt],
                        }
                    ]
                }
                unified_message = await adapter.receive_message(
                    single_payload,
                    db,
                    company_id,
                )
                unified_message.trace_id = _trace_id_from_context(
                    str((unified_message.metadata or {}).get("trace_id") or "")
                )
                unified_message.metadata.update(
                    {
                        "page_id": page_id,
                        "company_id": company_id,
                        "source": "instagram_webhook",
                        "event_id": event_id,
                        "trace_id": unified_message.trace_id,
                    }
                )
                unified_message = await _CHANNEL_NORMALIZER.normalize(unified_message, db)
                if not (str(unified_message.content or "").strip() or unified_message.attachments):
                    continue

                profile = await _fetch_meta_sender_profile(
                    db,
                    company_id=company_id,
                    channel="instagram",
                    sender_id=sid,
                )
                sender_name = (
                    str(profile.get("name") or "").strip()
                    or str(profile.get("username") or "").strip()
                    or f"Instagram User {sid[:8]}"
                )
                processed = await _process_unified_incoming_message(
                    db,
                    unified_message,
                    sender_name=sender_name,
                    sender_contact=sid,
                )
                if processed:
                    await _persist_customer_social_identity(
                        db,
                        company_id=company_id,
                        customer_id=processed.get("customer_id", ""),
                        channel="instagram",
                        sender_id=sid,
                        profile=profile,
                    )

        if event_id and entry_contexts:
            await _mark_unprocessed_event_resolved(
                db,
                channel="instagram",
                event_id=event_id,
                company_id=entry_contexts[0][2],
            )
        _schedule_unprocessed_retry(db, "instagram")
        return True
    except Exception as exc:
        logger.error("Instagram webhook error: %s", exc)
        if store_unresolved and event_id:
            await _store_unprocessed_event(
                db,
                channel="instagram",
                event_id=event_id,
                payload=payload,
                metadata={},
                reason="processing_error",
                last_error=str(exc),
            )
        return False


async def _handle_lead_form_webhook_payload(
    db,
    payload: dict,
    *,
    event_id: str = "",
    store_unresolved: bool = True,
) -> bool:
    try:
        entries = payload.get("entry", [payload]) or [payload]
        entry_contexts: list[tuple[dict, str]] = []
        for entry in entries:
            page_id = str((entry or {}).get("id") or "").strip()
            company_id = await _resolve_inbound_company_id(
                db,
                "lead_form",
                {"page_id": page_id},
                allow_direct_company_id=False,
                store_unprocessed=store_unresolved,
                event_id=event_id,
                payload=payload,
            )
            if not company_id:
                logger.warning(
                    "Unknown tenant for signed Meta webhook channel=lead_form event_id=%s page_id=%s",
                    event_id,
                    page_id,
                )
                if store_unresolved:
                    _schedule_unprocessed_retry(db, "lead_form")
                return False
            entry_contexts.append((entry, company_id))

        for entry, company_id in entry_contexts:
            await _record_webhook_event_safe(
                db,
                "lead_form",
                payload,
                metadata={"page_id": entry.get("id", "")},
                resolved_company_id=company_id,
                allow_direct_company_id=False,
            )
            for change in entry.get("changes", [entry]):
                value = change.get("value", change)
                if "leadgen_id" in value:
                    field_data = value.get("field_data", {}) or {}
                    lead_data = {
                        "name": field_data.get("full_name", value.get("name", "Ad Lead")),
                        "email": field_data.get("email", ""),
                        "phone": field_data.get("phone_number", ""),
                    }
                else:
                    lead_data = {
                        "name": value.get("name", value.get("full_name", "Form Lead")),
                        "email": value.get("email", ""),
                        "phone": value.get("phone", value.get("phone_number", "")),
                    }
                source = value.get("source", value.get("platform", "social_ads"))
                await _auto_capture_lead(
                    db,
                    "lead_form",
                    lead_data["name"],
                    lead_data.get("email") or lead_data.get("phone", ""),
                    f"Lead form from {source}",
                    metadata={
                        "company_id": company_id,
                        "page_id": entry.get("id", ""),
                    },
                )

        if event_id and entry_contexts:
            await _mark_unprocessed_event_resolved(
                db,
                channel="lead_form",
                event_id=event_id,
                company_id=entry_contexts[0][1],
            )
        _schedule_unprocessed_retry(db, "lead_form")
        return True
    except Exception as exc:
        logger.error("Lead form webhook error: %s", exc)
        if store_unresolved and event_id:
            await _store_unprocessed_event(
                db,
                channel="lead_form",
                event_id=event_id,
                payload=payload,
                metadata={},
                reason="processing_error",
                last_error=str(exc),
            )
        return False


async def _handle_external_purchase_webhook_payload(
    db,
    body: dict,
    *,
    external_purchase_id: str,
    purchase_id: str,
) -> None:
    await _record_webhook_event_safe(
        db,
        "external_purchase",
        body,
        metadata={"company_id": body.get("company_id", "")},
        external_id=external_purchase_id,
    )
    try:
        phone = (body.get("customer_phone", "") or "").strip()
        name = (body.get("customer_name", "Website Buyer") or "").strip()
        inbound_company_id = (body.get("company_id", "") or "").strip()
        if not inbound_company_id:
            raise HTTPException(400, "company_id is required for external purchases")
        existing_customer = r(
            await db.fetchrow(
                "SELECT * FROM customers WHERE phone=$1 AND company_id=$2 LIMIT 1",
                phone,
                inbound_company_id,
            )
        )
        lead = r(
            await db.fetchrow(
                "SELECT * FROM leads WHERE phone=$1 AND company_id=$2 LIMIT 1",
                phone,
                inbound_company_id,
            )
        )
        company_id = inbound_company_id or (existing_customer or lead or {}).get("company_id", "")
        customer_id_for_purchase = (existing_customer or {}).get("id", "") if existing_customer else ""
        await db.execute(
            "INSERT INTO external_purchases(id,company_id,customer_id,customer_phone,customer_name,product_name,cost,currency,created_by,purchased_at,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW(),NOW())",  # noqa: E501
            external_purchase_id,
            company_id,
            customer_id_for_purchase,
            phone,
            name,
            body.get("product_name", ""),
            float(body.get("cost", 0) or 0),
            body.get("currency", "USD"),
            body.get("source", "external"),
        )
        current_user = {"company_id": company_id}
        if lead:
            customer = await convert_lead_to_customer_state(db, lead, current_user)
        elif existing_customer:
            await db.execute(
                "UPDATE customers SET lifecycle_stage='customer',updated_at=NOW() WHERE id=$1",
                existing_customer["id"],
            )
            customer = r(await db.fetchrow("SELECT * FROM customers WHERE id=$1", existing_customer["id"]))
        else:
            new_customer_id = make_id()
            await db.execute(
                "INSERT INTO customers(id,company_id,name,email,phone,segment,avatar,lifecycle_stage,lifetime_value,avg_sentiment,recent_tickets,complaint_count,days_since_last_contact,total_conversations,created_at,updated_at) VALUES($1,$2,$3,'',$4,'general','','customer',0,0,0,0,0,0,NOW(),NOW())",  # noqa: E501
                new_customer_id,
                company_id,
                name,
                phone,
            )
            customer = r(await db.fetchrow("SELECT * FROM customers WHERE id=$1", new_customer_id))
        await db.execute(
            "INSERT INTO purchases(id,customer_id,company_id,amount,currency,product_category,product_name,purchase_date,created_at,updated_at) VALUES($1,$2,$3,$4,$5,'general',$6,NOW(),NOW(),NOW())",  # noqa: E501
            purchase_id,
            customer["id"],
            company_id,
            float(body.get("cost", 0)),
            body.get("currency", "USD"),
            body.get("product_name", ""),
        )
        await db.execute(
            "UPDATE customers SET lifecycle_stage='customer',lifetime_value=lifetime_value+$1,updated_at=NOW() WHERE id=$2",  # noqa: E501
            float(body.get("cost", 0)),
            customer["id"],
        )
    except Exception as exc:
        logger.error("External purchase webhook error: %s", exc)


async def _resolve_inbound_company_id(
    db,
    channel: str,
    metadata: Optional[dict] = None,
    *,
    allow_direct_company_id: bool = True,
    store_unprocessed: bool = False,
    event_id: str = "",
    payload: Optional[dict] = None,
) -> str:
    meta = metadata or {}
    safe_channel = str(channel or "").strip()
    safe_event_id = str(event_id or "").strip()

    async def _validated(candidate: str, *, descriptor: str) -> str:
        return await _validate_resolved_company_id(
            db,
            candidate,
            channel=safe_channel,
            descriptor=descriptor,
        )

    direct_company_id = (meta.get("company_id", "") or "").strip()
    if allow_direct_company_id and direct_company_id:
        resolved = await _validated(direct_company_id, descriptor="direct_company_id")
        if resolved:
            return resolved
        logger.warning(
            "Inbound company_id metadata not found in companies table channel=%s company_id=%s",
            channel,
            direct_company_id,
        )
    phone_number_id = (meta.get("phone_number_id", "") or "").strip()
    recipient_phone_number = (meta.get("recipient_phone_number", "") or "").strip()
    page_id = (meta.get("page_id", "") or "").strip()
    business_account_id = (meta.get("business_account_id", "") or "").strip()

    if channel == "whatsapp":
        if phone_number_id:
            resolved = await _resolve_single_company_id(
                db,
                "SELECT company_id FROM whatsapp_channels wc "
                "JOIN companies c ON c.id=wc.company_id AND c.deleted_at IS NULL "
                "WHERE wc.is_active=TRUE AND wc.phone_number_id=$1",
                phone_number_id,
                channel=channel,
                descriptor=f"whatsapp_channel_phone_number_id:{phone_number_id}",
            )
            if resolved:
                validated = await _validated(
                    resolved,
                    descriptor=f"whatsapp_channel_phone_number_id:{phone_number_id}",
                )
                if validated:
                    return validated
            resolved = await _resolve_single_company_id(
                db,
                "SELECT company_id FROM tenant_meta_config tmc "
                "JOIN companies c ON c.id=tmc.company_id AND c.deleted_at IS NULL "
                "WHERE tmc.channel='whatsapp' AND tmc.is_active=TRUE AND tmc.phone_number_id=$1",
                phone_number_id,
                channel=channel,
                descriptor=f"tenant_meta_phone_number_id:{phone_number_id}",
            )
            if resolved:
                validated = await _validated(
                    resolved,
                    descriptor=f"tenant_meta_phone_number_id:{phone_number_id}",
                )
                if validated:
                    return validated
            resolved = await _resolve_single_company_id(
                db,
                "SELECT cs.company_id FROM channel_settings cs "
                "JOIN companies c ON c.id=cs.company_id AND c.deleted_at IS NULL "
                "WHERE cs.channel='whatsapp' AND cs.enabled=TRUE AND cs.phone_number_id=$1",
                phone_number_id,
                channel=channel,
                descriptor=f"phone_number_id:{phone_number_id}",
            )
            if resolved:
                validated = await _validated(resolved, descriptor=f"phone_number_id:{phone_number_id}")
                if validated:
                    return validated
        if recipient_phone_number:
            resolved = await _resolve_single_company_id(
                db,
                "SELECT cs.company_id FROM channel_settings cs "
                "JOIN companies c ON c.id=cs.company_id AND c.deleted_at IS NULL "
                "WHERE cs.channel='whatsapp' AND cs.enabled=TRUE AND cs.phone_number=$1",
                recipient_phone_number,
                channel=channel,
                descriptor=f"recipient_phone_number:{recipient_phone_number}",
            )
            if resolved:
                validated = await _validated(
                    resolved,
                    descriptor=f"recipient_phone_number:{recipient_phone_number}",
                )
                if validated:
                    return validated
        if business_account_id:
            resolved = await _resolve_single_company_id(
                db,
                "SELECT company_id FROM whatsapp_channels wc "
                "JOIN companies c ON c.id=wc.company_id AND c.deleted_at IS NULL "
                "WHERE wc.is_active=TRUE AND wc.business_account_id=$1",
                business_account_id,
                channel=channel,
                descriptor=f"whatsapp_channel_business_account_id:{business_account_id}",
            )
            if resolved:
                validated = await _validated(
                    resolved,
                    descriptor=f"whatsapp_channel_business_account_id:{business_account_id}",
                )
                if validated:
                    return validated
            resolved = await _resolve_single_company_id(
                db,
                "SELECT company_id FROM tenant_meta_config tmc "
                "JOIN companies c ON c.id=tmc.company_id AND c.deleted_at IS NULL "
                "WHERE tmc.channel='whatsapp' AND tmc.is_active=TRUE AND tmc.business_account_id=$1",
                business_account_id,
                channel=channel,
                descriptor=f"tenant_meta_business_account_id:{business_account_id}",
            )
            if resolved:
                validated = await _validated(
                    resolved,
                    descriptor=f"tenant_meta_business_account_id:{business_account_id}",
                )
                if validated:
                    return validated
            resolved = await _resolve_single_company_id(
                db,
                "SELECT cs.company_id FROM channel_settings cs "
                "JOIN companies c ON c.id=cs.company_id AND c.deleted_at IS NULL "
                "WHERE cs.channel='whatsapp' AND cs.enabled=TRUE AND cs.page_id=$1",
                business_account_id,
                channel=channel,
                descriptor=f"business_account_id:{business_account_id}",
            )
            if resolved:
                validated = await _validated(
                    resolved,
                    descriptor=f"business_account_id:{business_account_id}",
                )
                if validated:
                    return validated
    if channel == "lead_form" and page_id:
        resolved = await _resolve_single_company_id(
            db,
            "SELECT company_id FROM tenant_meta_config tmc "
            "JOIN companies c ON c.id=tmc.company_id AND c.deleted_at IS NULL "
            "WHERE tmc.channel='facebook' AND tmc.is_active=TRUE "
            "AND (tmc.business_account_id=$1 OR tmc.catalog_id=$1)",
            page_id,
            channel=channel,
            descriptor=f"lead_form_meta_page_id:{page_id}",
        )
        if resolved:
            validated = await _validated(resolved, descriptor=f"lead_form_meta_page_id:{page_id}")
            if validated:
                return validated
        resolved = await _resolve_single_company_id(
            db,
            "SELECT company_id FROM channel_settings cs "
            "JOIN companies c ON c.id=cs.company_id AND c.deleted_at IS NULL "
            "WHERE cs.channel='facebook' AND cs.enabled=TRUE AND cs.page_id=$1",
            page_id,
            channel=channel,
            descriptor=f"lead_form_page_id:{page_id}",
        )
        if resolved:
            validated = await _validated(resolved, descriptor=f"lead_form_page_id:{page_id}")
            if validated:
                return validated
    if channel in {"facebook", "instagram"} and page_id:
        resolved = await _resolve_single_company_id(
            db,
            "SELECT company_id FROM ("
            "SELECT tmc.company_id FROM tenant_meta_config tmc "
            "JOIN companies c0 ON c0.id=tmc.company_id AND c0.deleted_at IS NULL "
            "WHERE tmc.channel=$1 AND tmc.is_active=TRUE "
            "AND (tmc.business_account_id=$2 OR tmc.catalog_id=$2) "
            "UNION ALL "
            "SELECT cs.company_id FROM channel_settings cs "
            "JOIN companies c ON c.id=cs.company_id AND c.deleted_at IS NULL "
            "WHERE cs.channel=$1 AND cs.enabled=TRUE AND cs.page_id=$2 "
            "UNION ALL "
            "SELECT sa.company_id FROM social_accounts sa "
            "JOIN companies c2 ON c2.id=sa.company_id AND c2.deleted_at IS NULL "
            "WHERE sa.platform=$1 AND sa.is_active=TRUE AND sa.page_id=$2"
            ") matches",
            channel,
            page_id,
            channel=channel,
            descriptor=f"page_id:{page_id}",
        )
        if resolved:
            validated = await _validated(resolved, descriptor=f"page_id:{page_id}")
            if validated:
                return validated

    redacted_metadata = {
        key: value
        for key, value in (_redact_inbound_metadata(meta) or {}).items()
        if value not in ("", None, [], {})
    }
    logger.warning(
        "Unable to resolve inbound company context channel=%s event_id=%s metadata=%s",
        safe_channel,
        safe_event_id,
        redacted_metadata,
    )
    if store_unprocessed:
        await _store_unresolved_inbound_event(
            db,
            channel=safe_channel,
            metadata=redacted_metadata,
            payload=payload,
            reason="tenant_unresolved",
            event_id=safe_event_id,
        )
    return ""


def _message_preview(content: str, attachments: Optional[list], sender_type: str) -> str:
    text = (content or "").strip()
    if text:
        return text[:100]
    files = [item for item in (attachments or []) if isinstance(item, dict)]
    if not files:
        return ""
    has_image = any(str(item.get("type") or item.get("file_type") or "").lower() == "image" for item in files)
    if has_image:
        return "Received image" if sender_type == "customer" else "Sent image"
    return "Received attachment" if sender_type == "customer" else "Sent attachment"


async def _capture_lead_snapshot(
    db,
    lead: dict | None,
    *,
    source: str,
    action: str,
    extra_metadata: dict | None = None,
) -> None:
    if not lead:
        return
    try:
        from data_pipeline.ingestion.raw_store import capture_raw_lead

        await capture_raw_lead(
            db,
            company_id=str(lead.get("company_id") or "").strip(),
            lead=lead,
            source=source,
            metadata={"action": action, **dict(extra_metadata or {})},
        )
    except Exception as exc:
        logger.warning(
            "webhook lead pipeline capture failed lead_id=%s action=%s error=%s",
            lead.get("id", ""),
            action,
            exc,
        )


async def _load_message_with_attachments(db, message_id: str) -> dict:
    msg = r(await db.fetchrow("SELECT * FROM messages WHERE id=$1", message_id)) or {}
    if not msg:
        return {}
    rows = await db.fetch(
        "SELECT * FROM message_attachments WHERE message_id=$1 ORDER BY created_at ASC",
        message_id,
    )
    msg["attachments"] = [
        {
            "id": row["id"],
            "type": row["file_type"],
            "url": row["file_url"],
            "name": row["file_name"],
            "size": row["file_size"],
        }
        for row in rows
    ]
    return msg


def _workflow_debug_dump(workflow: Any) -> Any:
    if workflow is None:
        return None
    if hasattr(workflow, "model_dump"):
        try:
            return workflow.model_dump()
        except Exception:
            return repr(workflow)
    return workflow


def _coerce_workflow_dict(
    value: Any,
    *,
    field_name: str,
    workflow: Any,
    context_label: str,
    company_id: str,
    conversation_id: str,
    channel: str,
    trace_id: str,
) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    logger.warning(
        "Unexpected workflow field shape context=%s field=%s company_id=%s conversation_id=%s channel=%s trace_id=%s workflow=%s",
        context_label,
        field_name,
        company_id,
        conversation_id,
        channel,
        trace_id,
        _workflow_debug_dump(workflow),
    )
    return {}


def _coerce_workflow_list(
    value: Any,
    *,
    field_name: str,
    workflow: Any,
    context_label: str,
    company_id: str,
    conversation_id: str,
    channel: str,
    trace_id: str,
) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    logger.warning(
        "Unexpected workflow list shape context=%s field=%s company_id=%s conversation_id=%s channel=%s trace_id=%s workflow=%s",
        context_label,
        field_name,
        company_id,
        conversation_id,
        channel,
        trace_id,
        _workflow_debug_dump(workflow),
    )
    return []


def _extract_workflow_outputs(
    workflow: Any,
    *,
    context_label: str,
    company_id: str,
    conversation_id: str,
    channel: str,
    trace_id: str,
) -> tuple[dict, dict, dict, dict]:
    agent_outputs = getattr(workflow, "agent_outputs", None)
    if agent_outputs is None:
        logger.warning(
            "Workflow agent_outputs missing context=%s company_id=%s conversation_id=%s channel=%s trace_id=%s workflow=%s",
            context_label,
            company_id,
            conversation_id,
            channel,
            trace_id,
            _workflow_debug_dump(workflow),
        )
        return {}, {}, {}, {}
    return (
        _coerce_workflow_dict(
            getattr(agent_outputs, "capture", None),
            field_name="agent_outputs.capture",
            workflow=workflow,
            context_label=context_label,
            company_id=company_id,
            conversation_id=conversation_id,
            channel=channel,
            trace_id=trace_id,
        ),
        _coerce_workflow_dict(
            getattr(agent_outputs, "qualification", None),
            field_name="agent_outputs.qualification",
            workflow=workflow,
            context_label=context_label,
            company_id=company_id,
            conversation_id=conversation_id,
            channel=channel,
            trace_id=trace_id,
        ),
        _coerce_workflow_dict(
            getattr(agent_outputs, "support", None),
            field_name="agent_outputs.support",
            workflow=workflow,
            context_label=context_label,
            company_id=company_id,
            conversation_id=conversation_id,
            channel=channel,
            trace_id=trace_id,
        ),
        _coerce_workflow_dict(
            getattr(agent_outputs, "analytics", None),
            field_name="agent_outputs.analytics",
            workflow=workflow,
            context_label=context_label,
            company_id=company_id,
            conversation_id=conversation_id,
            channel=channel,
            trace_id=trace_id,
        ),
    )


def _estimate_conversation_history_tokens(messages: list[dict]) -> int:
    payload = json.dumps(messages or [], ensure_ascii=False, default=str)
    return estimate_tokens(payload)


def _truncate_history_for_token_budget(
    msgs_history: list[dict],
    *,
    company_id: str,
    conversation_id: str,
    channel: str,
    trace_id: str,
) -> list[dict]:
    history = list(msgs_history or [])
    budget = ai_input_token_budget()
    if budget <= 0 or not history:
        return history
    original_estimate = _estimate_conversation_history_tokens(history)
    trimmed_history = list(history)
    removed_count = 0
    final_estimate = original_estimate
    while len(trimmed_history) > 1 and final_estimate > budget:
        trimmed_history.pop(0)
        removed_count += 1
        final_estimate = _estimate_conversation_history_tokens(trimmed_history)
    if removed_count:
        logger.warning(
            "Conversation history truncated for token budget company_id=%s conversation_id=%s channel=%s trace_id=%s removed_messages=%s original_tokens=%s final_tokens=%s budget=%s",
            company_id,
            conversation_id,
            channel,
            trace_id,
            removed_count,
            original_estimate,
            final_estimate,
            budget,
        )
    return trimmed_history


async def _recent_human_agent_message_within_cooldown(
    db,
    *,
    company_id: str,
    conversation_id: str,
) -> dict:
    cooldown_seconds = ai_response_cooldown_seconds()
    if not company_id or not conversation_id or cooldown_seconds <= 0:
        return {}
    row = await db.fetchrow(
        "SELECT id,sender_id,sender_name,created_at FROM messages "
        "WHERE company_id=$1 AND conversation_id=$2 AND sender_type='agent' "
        "AND created_at >= NOW() - ($3 * INTERVAL '1 second') "
        "ORDER BY created_at DESC LIMIT 1",
        company_id,
        conversation_id,
        cooldown_seconds,
    )
    return r(row)


async def _emit_outbound_failure_notice(
    db,
    *,
    company_id: str,
    conversation_id: str,
    channel: str,
    trace_id: str,
    failed_message_id: str,
    error: str,
) -> None:
    if not db or not company_id or not conversation_id:
        return
    logger.warning(
        "Emitting outbound failure notice company_id=%s conversation_id=%s channel=%s message_id=%s trace_id=%s error=%s",
        company_id,
        conversation_id,
        channel,
        failed_message_id,
        trace_id,
        error,
    )
    notice_id = make_id()
    notice_text = (
        f"Delivery failed on {channel}. Message {failed_message_id or 'unknown'} was not sent. "
        f"Error: {str(error or 'Unknown delivery error').strip()}"
    )
    await db.execute(
        "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,is_alert,read,created_at) "
        "VALUES($1,$2,$3,$4,'system','system','System',TRUE,FALSE,NOW())",
        notice_id,
        company_id,
        conversation_id,
        notice_text,
    )
    notice_message = r(await db.fetchrow("SELECT * FROM messages WHERE id=$1", notice_id))
    await emit_new_message(conversation_id, notice_message)


def _dedup_cache_key(channel: str, company_id: str, external_message_id: str) -> str:
    return f"{str(company_id or '').strip()}:{str(channel or '').strip()}:{str(external_message_id or '').strip()}"


async def _run_lead_workflow_sync(
    db,
    *,
    company_id: str,
    lead: dict,
    customer: dict,
    source: str,
    raw_message: str,
    metadata: dict | None = None,
) -> dict:
    workflow = await orchestrate_lead_workflow(
        LeadWorkflowRequest(
            company_id=company_id,
            lead_id=str(lead.get("id") or ""),
            customer_id=str(customer.get("id") or ""),
            source=source,
            raw_message=raw_message,
            lead=lead,
            customer=customer,
            metadata=dict(metadata or {}),
        ),
        db=db,
    )
    _, qualification, support, _ = _extract_workflow_outputs(
        workflow,
        context_label="lead_workflow_sync",
        company_id=company_id,
        conversation_id=str(customer.get("conversation_id") or ""),
        channel=str(source or "lead"),
        trace_id=str((metadata or {}).get("trace_id") or ""),
    )
    await db.execute(
        "UPDATE leads SET score=$1,grade=$2,phase=$3,scoring_reason=$4,next_action=$5,updated_at=NOW() WHERE id=$6",
        int(qualification.get("score", 0) or 0),
        str(qualification.get("grade") or lead.get("grade") or "cold"),
        str(qualification.get("phase") or lead.get("phase") or "awareness"),
        str(qualification.get("reasoning") or ""),
        str(qualification.get("next_action") or ""),
        str(lead.get("id") or ""),
    )
    nurture_message = str(support.get("response") or "").strip()
    nurture_stage = str(support.get("stage") or qualification.get("phase") or lead.get("phase") or "awareness")
    if nurture_message and support.get("deliver_response"):
        await db.execute(
            "INSERT INTO lead_activities(id,lead_id,company_id,type,content,stage,created_at) "
            "VALUES($1,$2,$3,'auto_nurture',$4,$5,NOW())",
            make_id(),
            str(lead.get("id") or ""),
            company_id,
            nurture_message,
            nurture_stage,
        )
        await db.execute(
            "INSERT INTO lead_nurture_messages(id,lead_id,company_id,message,phase,sent,created_at) "
            "VALUES($1,$2,$3,$4,$5,FALSE,NOW())",
            make_id(),
            str(lead.get("id") or ""),
            company_id,
            nurture_message,
            nurture_stage,
        )
    return r(await db.fetchrow("SELECT * FROM leads WHERE id=$1", str(lead.get("id") or "")))


async def _handle_social_change_event(
    db,
    *,
    channel: str,
    change: dict,
    page_id: str,
    company_id: str,
    event_id: str,
    adapter,
) -> bool:
    """Normalize a Facebook/Instagram ``changes[]`` entry into a UnifiedMessage
    and push it through the standard capture pipeline.

    Applies ``social_lead``/``interested``/``hot_lead`` tags to the captured
    customer + lead when the detector flags the comment as a lead signal.
    """
    field = str((change or {}).get("field") or "").lower()
    value = (change or {}).get("value") or {}
    if not value:
        return False

    # For FB we only currently care about feed comments. For IG we care about
    # "comments" (feed comments) and "mentions" (when user tags the page).
    if channel == "facebook":
        if field != "feed":
            return False
        item = str(value.get("item") or "").lower()
        if item not in {"comment"}:
            return False
        normalized = adapter.normalize_comment_event(value, page_id)
        channel_type = ChannelType.FACEBOOK
        sender_label = "Facebook Commenter"
    elif channel == "instagram":
        if field not in {"comments", "mentions"}:
            return False
        normalized = adapter.normalize_comment_event(value, field, page_id)
        channel_type = ChannelType.INSTAGRAM
        sender_label = "Instagram Commenter"
    else:
        return False

    content = str(normalized.get("content") or "").strip()
    sender_id = str(normalized.get("sender_id") or "").strip()
    if not content or not sender_id:
        return False
    if normalized.get("message_type") in {"comment_deleted", "like"}:
        return False

    sender_name = str(normalized.get("sender_name") or "").strip() or f"{sender_label} {sender_id[:8]}"

    unified_message = UnifiedMessage(
        message_id=normalized.get("message_id") or f"comment_{sender_id}",
        tenant_id=company_id,
        user_id=sender_id,
        external_user_id=sender_id,
        channel_type=channel_type,
        direction=MessageDirection.INBOUND,
        content=content,
        timestamp=normalized.get("timestamp"),
        metadata={
            "page_id": page_id,
            "company_id": company_id,
            "source": f"{channel}_webhook_comment",
            "event_id": event_id,
            "message_type": normalized.get("message_type", "comment"),
            "post_id": normalized.get("post_id", ""),
            "is_social_signal": True,
            "sender_username": sender_name,
        },
        reply_to_message_id=str(normalized.get("reply_to_message_id") or ""),
    )
    unified_message = await _CHANNEL_NORMALIZER.normalize(unified_message, db)

    signal = detect_social_lead(content, platform=channel)
    if not signal.is_lead and not unified_message.resolved_customer_id:
        # Not a lead signal and we don't already know this person — skip so we
        # don't flood the CRM with random comments.
        logger.debug(
            "Skipping non-lead comment channel=%s sender=%s",
            channel,
            sender_id,
        )
        return False

    unified_message.metadata["social_signal"] = signal.to_dict()

    processed = await _process_unified_incoming_message(
        db,
        unified_message,
        sender_name=sender_name,
        sender_contact=sender_id,
    )
    if not processed:
        return False

    customer_id = str(processed.get("customer_id") or "").strip()
    lead_id = str(processed.get("lead_id") or "").strip()
    tags_to_apply = list(signal.tags)
    # Always mark explicitly as a social_lead from this surface.
    if "social_lead" not in tags_to_apply:
        tags_to_apply.insert(0, "social_lead")
    if channel not in tags_to_apply:
        tags_to_apply.append(channel)
    await _apply_social_tags(
        db,
        customer_id=customer_id,
        lead_id=lead_id,
        tags=tags_to_apply,
    )
    return True


async def _apply_social_tags(
    db,
    *,
    customer_id: str,
    lead_id: str,
    tags: list[str],
) -> None:
    """Insert tags into ``customer_tags`` and ``lead_tags`` idempotently.

    Silently swallows per-tag failures so a single bad tag never breaks the
    capture flow.
    """
    unique_tags: list[str] = []
    seen: set[str] = set()
    for tag in tags or []:
        cleaned = str(tag or "").strip().lower()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        unique_tags.append(cleaned)
    if not unique_tags:
        return

    for tag in unique_tags:
        if customer_id:
            try:
                await db.execute(
                    "INSERT INTO customer_tags(customer_id,tag) VALUES($1,$2) ON CONFLICT DO NOTHING",
                    customer_id,
                    tag,
                )
            except Exception as exc:
                logger.debug("customer_tag insert failed (%s): %s", tag, exc)
        if lead_id:
            try:
                await db.execute(
                    "INSERT INTO lead_tags(lead_id,tag) VALUES($1,$2) ON CONFLICT DO NOTHING",
                    lead_id,
                    tag,
                )
            except Exception as exc:
                logger.debug("lead_tag insert failed (%s): %s", tag, exc)


async def _auto_capture_lead(
    db,
    channel: str,
    sender_name: str,
    sender_contact: str,
    message_text: str,
    metadata: dict = None,
):
    """Auto-capture lead from incoming channel message."""
    try:
        metadata_payload = dict(metadata or {})
        company_id = await _resolve_inbound_company_id(
            db,
            channel,
            metadata_payload,
            store_unprocessed=True,
            event_id=_derive_unprocessed_event_id(channel, metadata_payload, {"metadata": metadata_payload}),
            payload={"metadata": metadata_payload},
        )
        if not company_id:
            logger.warning(
                "Skipping inbound message capture because tenant resolution failed channel=%s sender_contact=%s",
                channel,
                sender_contact,
            )
            return None
        existing = None
        explicit_customer_id = str(metadata_payload.get("customer_id") or "").strip()
        if explicit_customer_id:
            existing = r(
                await db.fetchrow(
                    "SELECT * FROM customers WHERE id=$1 AND company_id=$2 LIMIT 1",
                    explicit_customer_id,
                    company_id,
                )
            )
        social_profile_id = str(metadata_payload.get("social_profile_id") or "").strip()
        if social_profile_id and not existing and channel in {"facebook", "instagram"}:
            existing = r(
                await db.fetchrow(
                    "SELECT c.* FROM customers c "
                    "JOIN customer_social_profiles csp ON csp.customer_id=c.id "
                    "WHERE c.company_id=$1 AND csp.platform=$2 AND csp.profile_id=$3 "
                    "ORDER BY c.updated_at DESC LIMIT 1",
                    company_id,
                    channel,
                    social_profile_id,
                )
            )
        if sender_contact and not existing:
            # Exact match first (cheap, indexed).
            existing = r(
                await db.fetchrow(
                    "SELECT * FROM customers WHERE (phone=$1 OR email=$1) AND company_id=$2 "
                    "ORDER BY updated_at DESC LIMIT 1",
                    sender_contact,
                    company_id,
                )
            )
            # Identity fallback — case-insensitive email + last-10-digits phone.
            if not existing:
                contact_str = str(sender_contact).strip()
                if "@" in contact_str:
                    existing = r(
                        await db.fetchrow(
                            "SELECT * FROM customers WHERE company_id=$1 "
                            "AND LOWER(email)=$2 ORDER BY updated_at DESC LIMIT 1",
                            company_id,
                            contact_str.lower(),
                        )
                    )
                else:
                    import re as _re_cap

                    digits = _re_cap.sub(r"\D", "", contact_str)
                    last10 = digits[-10:] if len(digits) >= 10 else digits
                    if last10:
                        existing = r(
                            await db.fetchrow(
                                "SELECT * FROM customers WHERE company_id=$1 "
                                "AND regexp_replace(phone, '\\D', '', 'g') LIKE $2 "
                                "ORDER BY updated_at DESC LIMIT 1",
                                company_id,
                                f"%{last10}",
                            )
                        )
        contact_fields = _extract_sender_contact_fields(channel, sender_contact, metadata_payload)
        social_profile_id = contact_fields["social_profile_id"] or social_profile_id
        if not existing:
            existing = await resolve_customer_by_contact(
                db,
                company_id,
                phone=contact_fields["phone"],
                email=contact_fields["email"],
                channel=channel,
                channel_profile_id=social_profile_id,
                explicit_customer_id=explicit_customer_id,
            )
        if not existing:
            nid = make_id()
            normalized_phone = (
                await normalize_customer_contact_phone(db, company_id, contact_fields["phone"])
                if contact_fields["phone"]
                else ""
            )
            await db.execute(
                "INSERT INTO customers(id,company_id,name,email,phone,segment,avatar,lifecycle_stage,lifetime_value,avg_sentiment,"  # noqa: E501
                "recent_tickets,complaint_count,days_since_last_contact,total_conversations,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,$5,'general','','lead',0,0,0,0,0,0,NOW(),NOW())",
                nid,
                company_id,
                sender_name or "Unknown Contact",
                contact_fields["email"],
                normalized_phone,
            )
            existing = r(await db.fetchrow("SELECT * FROM customers WHERE id=$1", nid))
        else:
            customer_updates = []
            args = []
            if company_id and not existing.get("company_id"):
                customer_updates.append(f"company_id=${len(args) + 1}")
                args.append(company_id)
                existing["company_id"] = company_id
            if sender_name and sender_name != existing.get("name"):
                customer_updates.append(f"name=${len(args) + 1}")
                args.append(sender_name)
                existing["name"] = sender_name
            normalized_email = contact_fields["email"]
            normalized_phone = (
                await normalize_customer_contact_phone(db, company_id, contact_fields["phone"])
                if contact_fields["phone"]
                else ""
            )
            existing_phone_digits = re.sub(r"\D", "", str(existing.get("phone") or ""))
            normalized_phone_digits = re.sub(r"\D", "", normalized_phone)
            if normalized_email and not existing.get("email"):
                customer_updates.append(f"email=${len(args) + 1}")
                args.append(normalized_email)
                existing["email"] = normalized_email
            if normalized_phone and (
                not existing_phone_digits
                or (
                    len(normalized_phone_digits) > len(existing_phone_digits)
                    and normalized_phone_digits.endswith(existing_phone_digits)
                )
            ):
                customer_updates.append(f"phone=${len(args) + 1}")
                args.append(normalized_phone)
                existing["phone"] = normalized_phone
            if existing.get("lifecycle_stage") != "customer":
                customer_updates.append(f"lifecycle_stage=${len(args) + 1}")
                args.append("lead")
                existing["lifecycle_stage"] = "lead"
            if customer_updates:
                args.append(existing["id"])
                await db.execute(
                    f"UPDATE customers SET {', '.join(customer_updates)},updated_at=NOW() WHERE id=${len(args)}",
                    *args,
                )
        await upsert_customer_social_profile(db, existing["id"], channel, social_profile_id)
        await db.execute(
            "INSERT INTO customer_tags(customer_id,tag) VALUES($1,'auto-captured') ON CONFLICT DO NOTHING",
            existing["id"],
        )
        await db.execute(
            "INSERT INTO customer_tags(customer_id,tag) VALUES($1,$2) ON CONFLICT DO NOTHING",
            existing["id"],
            channel,
        )
        await db.execute(
            "INSERT INTO customer_channels(customer_id,channel) VALUES($1,$2) ON CONFLICT DO NOTHING",
            existing["id"],
            channel,
        )
        if existing.get("lifecycle_stage") == "customer":
            return {
                "customer": existing,
                "lead_id": None,
                "lead": None,
                "is_new": False,
                "company_id": company_id,
            }

        existing_lead = None
        existing_customer_lead_id = str(existing.get("lead_id") or "").strip()
        if existing_customer_lead_id:
            existing_lead = r(
                await db.fetchrow(
                    "SELECT * FROM leads WHERE id=$1 AND company_id=$2 LIMIT 1",
                    existing_customer_lead_id,
                    company_id,
                )
            )
        lookup_value = existing.get("email") or existing.get("phone") or ""
        if not existing_lead and lookup_value:
            existing_lead = r(
                await db.fetchrow(
                    "SELECT * FROM leads WHERE (email=$1 OR phone=$1) AND company_id=$2 "
                    "ORDER BY updated_at DESC LIMIT 1",
                    lookup_value,
                    company_id,
                )
            )
        if not existing_lead:
            lid = make_id()
            await db.execute(
                "INSERT INTO leads(id,company_id,name,email,phone,source,status,score,grade,phase,notes,assigned_to,assigned_name,created_at,updated_at) "  # noqa: E501
                "VALUES($1,$2,$3,$4,$5,$6,'new',0,'cold','awareness',$7,'','',NOW(),NOW())",
                lid,
                company_id,
                sender_name or "Unknown",
                existing.get("email", ""),
                existing.get("phone", ""),
                channel,
                f"Auto-captured from {channel}: {message_text[:200]}",
            )
            await db.execute(
                "INSERT INTO lead_channels(lead_id,channel) VALUES($1,$2) ON CONFLICT DO NOTHING",
                lid,
                channel,
            )
            await db.execute(
                "UPDATE customers SET lead_id=$1,updated_at=NOW() WHERE id=$2 AND company_id=$3",
                lid,
                existing["id"],
                company_id,
            )
            try:
                lead_snapshot = await _run_lead_workflow_sync(
                    db,
                    company_id=company_id,
                    lead=r(await db.fetchrow("SELECT * FROM leads WHERE id=$1", lid)),
                    customer=existing,
                    source=channel,
                    raw_message=message_text,
                    metadata={
                        **metadata_payload,
                        "source": f"webhook_{channel}",
                    },
                )
            except Exception as e:
                logger.error(f"Lead workflow failed: {e}")
                lead_snapshot = r(await db.fetchrow("SELECT * FROM leads WHERE id=$1", lid))
            await _capture_lead_snapshot(
                db,
                lead_snapshot,
                source=channel,
                action="lead_auto_captured",
            )
            existing["lead_id"] = lid
            return {
                "customer": existing,
                "lead_id": lid,
                "lead": lead_snapshot,
                "is_new": True,
                "company_id": company_id,
            }

        lead_updates = []
        args = []
        if company_id and not existing_lead.get("company_id"):
            lead_updates.append(f"company_id=${len(args) + 1}")
            args.append(company_id)
        if sender_name and sender_name != existing_lead.get("name"):
            lead_updates.append(f"name=${len(args) + 1}")
            args.append(sender_name)
        if lead_updates:
            args.append(existing_lead["id"])
            await db.execute(
                f"UPDATE leads SET {', '.join(lead_updates)},updated_at=NOW() WHERE id=${len(args)}",
                *args,
            )
        await db.execute(
            "INSERT INTO lead_channels(lead_id,channel) VALUES($1,$2) ON CONFLICT DO NOTHING",
            existing_lead["id"],
            channel,
        )
        if existing_customer_lead_id != existing_lead["id"]:
            await db.execute(
                "UPDATE customers SET lead_id=$1,updated_at=NOW() WHERE id=$2 AND company_id=$3",
                existing_lead["id"],
                existing["id"],
                company_id,
            )
            existing["lead_id"] = existing_lead["id"]
        await db.execute(
            "INSERT INTO lead_activities(id,lead_id,company_id,type,content,stage,created_at) VALUES($1,$2,$3,'message',$4,'',NOW())",  # noqa: E501
            make_id(),
            existing_lead["id"],
            company_id,
            f"New {channel} message: {message_text[:200]}",
        )
        refreshed_lead = r(await db.fetchrow("SELECT * FROM leads WHERE id=$1", existing_lead["id"]))
        try:
            refreshed_lead = await _run_lead_workflow_sync(
                db,
                company_id=company_id,
                lead=refreshed_lead,
                customer=existing,
                source=channel,
                raw_message=message_text,
                metadata={
                    **metadata_payload,
                    "source": f"webhook_{channel}",
                },
            )
        except Exception as e:
            logger.error(f"Lead workflow refresh failed: {e}")
        await _capture_lead_snapshot(
            db,
            refreshed_lead,
            source=channel,
            action="lead_activity_updated",
        )
        return {
            "customer": existing,
            "lead_id": existing_lead["id"],
            "lead": refreshed_lead,
            "is_new": False,
            "company_id": company_id,
        }
    except Exception as e:
        logger.error(f"Lead capture failed: {e}")
        return None


async def _try_auto_unify(db, company_id: str, customer: dict, channel: str):
    """After customer creation/update, resolve identity via identity_service."""
    if not customer:
        return
    try:
        tenant_keys = identity_tenant_api_keys()
        tenant_id = (company_id or "").strip()
        if tenant_id not in tenant_keys:
            logger.warning(
                "Skipping auto-unify for customer=%s due to missing tenant mapping tenant_id=%s",
                customer.get("id"),
                tenant_id,
            )
            return
        tenant_api_key = tenant_keys.get(tenant_id)
        if not tenant_api_key:
            return

        normalized_channel = (channel or "pulse_customer").strip().lower()
        if normalized_channel not in {"whatsapp", "facebook", "instagram", "web_chat", "pulse_customer"}:
            normalized_channel = "pulse_customer"

        payload = {
            "platform": normalized_channel,
            "platform_user_id": str(customer.get("id") or "").strip(),
            "phone_number": (customer.get("phone") or "").strip() or None,
            "email_address": (customer.get("email") or "").strip() or None,
            "full_name": (customer.get("name") or "").strip() or None,
            "username": (customer.get("name") or "").strip() or None,
        }
        if not payload["platform_user_id"]:
            return

        async with httpx.AsyncClient(timeout=webhook_identity_resolve_timeout_seconds()) as client:
            response = await client.post(
                f"{_IDENTITY_BASE_URL}/api/identity/resolve",
                headers={
                    "X-Tenant-ID": tenant_id,
                    "X-API-Key": tenant_api_key,
                    "X-User-Role": "admin",
                },
                json=payload,
            )
        if response.status_code >= 400:
            logger.warning(
                "Identity resolve failed for customer=%s status=%s",
                customer.get("id"),
                response.status_code,
            )
            return

        body = response.json() if response.content else {}
        resolved_profile_id = str(body.get("customer_id") or "").strip()
        resolved_profile = body.get("profile") or {}
        member_count = int((resolved_profile or {}).get("member_count") or 0)
        write_result = ""
        if resolved_profile_id and str(customer.get("id") or "").strip() and tenant_id:
            metadata_patch = json.dumps(
                {
                    "identity_unification": {
                        "profile_id": resolved_profile_id,
                        "match_type": str(body.get("match_type") or "").strip(),
                        "confidence_score": float(body.get("confidence_score") or 0),
                        "review_required": bool(body.get("review_required", False)),
                        "merge_performed": bool(body.get("merge_performed", False)),
                        "member_count": member_count,
                        "channel": normalized_channel,
                        "unified": member_count > 1,
                        "resolved_at": datetime.now(timezone.utc).isoformat(),
                    }
                },
                ensure_ascii=True,
                separators=(",", ":"),
            )
            write_result = await db.execute(
                "UPDATE customers "
                "SET metadata = (CASE WHEN jsonb_typeof(metadata) = 'object' THEN metadata ELSE '{}'::jsonb END) "
                "|| $1::jsonb, updated_at=NOW() "
                "WHERE id=$2 AND company_id=$3",
                metadata_patch,
                customer.get("id"),
                tenant_id,
            )
        logger.info(
            "Identity resolved customer=%s resolved_profile_id=%s unified_members=%s review_required=%s write_result=%s",
            customer.get("id"),
            resolved_profile_id,
            member_count,
            body.get("review_required", False),
            write_result or "skipped",
        )
    except Exception as exc:
        logger.warning("Auto-unification identity resolve failed: %s", exc)


def _trace_id_from_context(fallback: str = "") -> str:
    trace = current_trace_context()
    if trace and trace.trace_id:
        return str(trace.trace_id).strip()
    return str(fallback or "").strip().replace("-", "")


def _channel_type_from_name(channel: str) -> ChannelType | None:
    mapping = {
        "whatsapp": ChannelType.WHATSAPP,
        "facebook": ChannelType.FACEBOOK,
        "instagram": ChannelType.INSTAGRAM,
        "web_chat": ChannelType.WEB_CHAT,
        "email": ChannelType.EMAIL,
    }
    return mapping.get(str(channel or "").strip().lower())


def _legacy_attachments_from_unified(attachments: list[UnifiedAttachment]) -> list[dict]:
    normalized: list[dict] = []
    for item in attachments or []:
        attachment = item.model_dump() if hasattr(item, "model_dump") else dict(item or {})
        url = str(attachment.get("url") or attachment.get("data_url") or "").strip()
        if not url:
            continue
        normalized.append(
            {
                "type": str(attachment.get("type") or "file").strip() or "file",
                "url": url,
                "data_url": str(attachment.get("data_url") or "").strip(),
                "name": str(attachment.get("name") or "").strip(),
                "mime_type": str(attachment.get("mime_type") or "").strip(),
                "size": int(attachment.get("size") or 0),
            }
        )
    return normalized


def _resolve_outbound_recipient(channel: str, convo: dict, customer: dict, sender_contact: str = "") -> str:
    normalized_channel = str(channel or "").strip().lower()
    conversation_channel_id = str(convo.get("channel_id") or convo.get("session_id") or "").strip()
    if normalized_channel == "whatsapp":
        return str(conversation_channel_id or customer.get("phone") or sender_contact or "").strip()
    if normalized_channel in {"facebook", "instagram"}:
        return str(conversation_channel_id or sender_contact or "").strip()
    if normalized_channel == "web_chat":
        return str(conversation_channel_id or sender_contact or customer.get("email") or customer.get("id") or "").strip()
    if normalized_channel == "email":
        return str(conversation_channel_id or customer.get("email") or sender_contact or "").strip()
    return str(conversation_channel_id or sender_contact or "").strip()


async def _send_outbound_response_via_channel_layer(
    *,
    db,
    company_id: str,
    channel: str,
    recipient_id: str,
    content: str,
    conversation_id: str,
    attachments: list[dict] | None = None,
    db_message_id: str = "",
    metadata: dict | None = None,
) -> tuple[bool, str]:
    channel_type = _channel_type_from_name(channel)
    if not channel_type:
        error = f"Unsupported outbound channel: {channel}"
        if db_message_id:
            await _persist_outbound_message_state(
                db,
                company_id=company_id,
                db_message_id=db_message_id,
                delivery_status="failed",
            )
            try:
                await _emit_outbound_failure_notice(
                    db,
                    company_id=company_id,
                    conversation_id=conversation_id,
                    channel=channel,
                    trace_id=str((metadata or {}).get("trace_id") or ""),
                    failed_message_id=db_message_id,
                    error=error,
                )
            except Exception:
                logger.exception(
                    "Failed to emit outbound failure notice for unsupported channel company_id=%s conversation_id=%s channel=%s message_id=%s",
                    company_id,
                    conversation_id,
                    channel,
                    db_message_id,
                )
        return False, error
    recipient = str(recipient_id or "").strip()
    if not recipient:
        error = "Missing outbound recipient"
        if db_message_id:
            await _persist_outbound_message_state(
                db,
                company_id=company_id,
                db_message_id=db_message_id,
                delivery_status="failed",
            )
            try:
                await _emit_outbound_failure_notice(
                    db,
                    company_id=company_id,
                    conversation_id=conversation_id,
                    channel=channel,
                    trace_id=str((metadata or {}).get("trace_id") or ""),
                    failed_message_id=db_message_id,
                    error=error,
                )
            except Exception:
                logger.exception(
                    "Failed to emit outbound failure notice for missing recipient company_id=%s conversation_id=%s channel=%s message_id=%s",
                    company_id,
                    conversation_id,
                    channel,
                    db_message_id,
                )
        return False, error

    outbound = get_outbound_router()
    message_metadata = dict(metadata or {})
    if db_message_id and not message_metadata.get("db_message_id"):
        message_metadata["db_message_id"] = db_message_id
    trace_id = str(message_metadata.get("trace_id") or "").strip() or _trace_id_from_context()
    message_metadata["trace_id"] = trace_id

    max_attempts = 3
    base_delay = outbound_retry_base_delay_seconds()
    external_message_id = ""
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        try:
            result = await outbound.send_to_channel(
                tenant_id=company_id,
                channel_type=channel_type,
                external_user_id=recipient,
                content=content,
                db=db,
                metadata=message_metadata,
                conversation_id=conversation_id,
                attachments=attachments or [],
                db_message_id=db_message_id,
            )
            external_message_id = str(result.external_message_id or "").strip()
            last_error = str(result.error or "").strip()
            if result.success:
                if db_message_id:
                    await _persist_outbound_message_state(
                        db,
                        company_id=company_id,
                        db_message_id=db_message_id,
                        delivery_status="delivered",
                        external_message_id=external_message_id,
                    )
                return True, ""
            logger.warning(
                "Outbound send attempt failed company_id=%s conversation_id=%s channel=%s message_id=%s trace_id=%s attempt=%s/%s error=%s",
                company_id,
                conversation_id,
                channel,
                db_message_id,
                trace_id,
                attempt,
                max_attempts,
                last_error or "unknown",
            )
        except Exception as exc:
            last_error = str(exc).strip() or "Unhandled outbound send error"
            logger.exception(
                "Outbound send attempt raised company_id=%s conversation_id=%s channel=%s message_id=%s trace_id=%s attempt=%s/%s",
                company_id,
                conversation_id,
                channel,
                db_message_id,
                trace_id,
                attempt,
                max_attempts,
            )
        if attempt < max_attempts:
            await asyncio.sleep(base_delay * (2 ** (attempt - 1)))

    if db_message_id:
        await _persist_outbound_message_state(
            db,
            company_id=company_id,
            db_message_id=db_message_id,
            delivery_status="failed",
            external_message_id=external_message_id,
        )
        try:
            await _emit_outbound_failure_notice(
                db,
                company_id=company_id,
                conversation_id=conversation_id,
                channel=channel,
                trace_id=trace_id,
                failed_message_id=db_message_id,
                error=last_error,
            )
        except Exception:
            logger.exception(
                "Failed to emit outbound failure notice company_id=%s conversation_id=%s channel=%s message_id=%s trace_id=%s",
                company_id,
                conversation_id,
                channel,
                db_message_id,
                trace_id,
            )
    logger.error(
        "Outbound send permanently failed company_id=%s conversation_id=%s channel=%s message_id=%s trace_id=%s error=%s",
        company_id,
        conversation_id,
        channel,
        db_message_id,
        trace_id,
        last_error or "unknown",
    )
    return False, last_error or "Outbound send failed"


async def _process_unified_incoming_message(
    db,
    message: UnifiedMessage,
    *,
    sender_name: str = "",
    sender_contact: str = "",
    metadata: dict | None = None,
):
    inferred_sender_name = (
        str(sender_name or "").strip()
        or str((message.metadata or {}).get("profile_name") or "").strip()
        or f"{message.channel_type.value} user"
    )
    inferred_sender_contact = str(sender_contact or message.external_user_id or "").strip()

    payload_metadata = dict(message.metadata or {})
    payload_metadata.update(dict(metadata or {}))
    if not payload_metadata.get("company_id"):
        payload_metadata["company_id"] = str(message.tenant_id or "").strip()
    if message.resolved_customer_id and not payload_metadata.get("customer_id"):
        payload_metadata["customer_id"] = str(message.resolved_customer_id or "").strip()
    if (
        message.channel_type in (ChannelType.INSTAGRAM, ChannelType.FACEBOOK)
        and message.external_user_id
        and not payload_metadata.get("social_profile_id")
    ):
        payload_metadata["social_profile_id"] = str(message.external_user_id or "").strip()
    if not payload_metadata.get("inbound_external_message_id"):
        payload_metadata["inbound_external_message_id"] = str(message.message_id or "").strip()
    if not payload_metadata.get("adapter"):
        payload_metadata["adapter"] = message.channel_type.value
    if not payload_metadata.get("trace_id"):
        payload_metadata["trace_id"] = _trace_id_from_context(str(message.trace_id or "").strip())

    logger.info(
        "Inbound unified message received channel=%s tenant=%s user_id=%s external_user=%s message_id=%s trace_id=%s",
        message.channel_type.value,
        message.tenant_id,
        message.user_id,
        message.external_user_id,
        message.message_id,
        payload_metadata.get("trace_id", ""),
    )

    processed = await _process_incoming_message(
        db,
        message.channel_type.value,
        inferred_sender_name,
        inferred_sender_contact,
        str(message.content or "").strip(),
        attachments=_legacy_attachments_from_unified(list(message.attachments or [])),
        metadata=payload_metadata,
    )

    # Opportunistic lead tagging: detect interest keywords in the content and
    # tag the captured customer/lead accordingly. Safe no-op if no match.
    try:
        if processed:
            content_text = str(message.content or "").strip()
            if content_text:
                sig = detect_social_lead(
                    content_text,
                    platform=message.channel_type.value,
                )
                tags = [t for t in sig.tags if t and t != message.channel_type.value]
                # Preserve the already-existing platform tag behaviour without
                # duplicating it on every message.
                if tags:
                    await _apply_social_tags(
                        db,
                        customer_id=str(processed.get("customer_id") or ""),
                        lead_id=str(processed.get("lead_id") or ""),
                        tags=tags,
                    )
    except Exception as tag_exc:
        logger.debug("Social tag enrichment failed: %s", tag_exc)

    return processed


async def _process_incoming_message(
    db,
    channel: str,
    sender_name: str,
    sender_contact: str,
    message_text: str,
    attachments: Optional[list] = None,
    metadata: dict = None,
):
    metadata_payload = dict(metadata or {})
    if sender_contact and not metadata_payload.get("sender_contact"):
        metadata_payload["sender_contact"] = sender_contact
    result = await _auto_capture_lead(db, channel, sender_name, sender_contact, message_text, metadata_payload)
    if not result:
        return None
    customer = result["customer"]
    lead = dict(result.get("lead") or {})
    cid = customer.get("id", "")
    company_id = result.get("company_id") or customer.get("company_id", "")
    company_id = await _validate_resolved_company_id(
        db,
        company_id,
        channel=channel,
        descriptor="process_incoming_message",
    )
    if not company_id:
        logger.error(
            "Inbound message aborted due to invalid tenant channel=%s sender_contact=%s customer_id=%s",
            channel,
            sender_contact,
            cid,
        )
        return None
    inbound_external_message_id = str(
        metadata_payload.get("inbound_external_message_id") or metadata_payload.get("external_message_id") or ""
    ).strip()
    channel_binding = _extract_sender_contact_fields(channel, sender_contact, metadata_payload)["channel_id"]

    if inbound_external_message_id and company_id:
        existing = r(
            await db.fetchrow(
                "SELECT id,conversation_id FROM messages "
                "WHERE company_id=$1 AND external_message_id=$2 "
                "ORDER BY created_at DESC LIMIT 1",
                company_id,
                inbound_external_message_id,
            )
        )
        if existing:
            logger.info(
                "Duplicate inbound event ignored company_id=%s channel=%s external_message_id=%s message_id=%s",
                company_id,
                channel,
                inbound_external_message_id,
                existing.get("id", ""),
            )
            return {
                "conversation_id": existing.get("conversation_id", ""),
                "message_id": existing.get("id", ""),
                "customer_id": cid,
                "lead_id": result.get("lead_id", ""),
                "customer_message": await _load_message_with_attachments(db, existing.get("id", "")),
                "ai_message": None,
                "sentiment_analysis": build_sentiment_gate(message_text, {}),
                "duplicate": True,
            }

    msg_id = make_id()
    ext_part = inbound_external_message_id.strip()
    usage_idempotency_key = (
        f"in:{company_id}:{channel}:{ext_part}" if ext_part else f"in:{company_id}:{channel}:msg:{msg_id}"
    )
    saved_attachments: list[dict] = []
    convo: dict = {}
    convo_id = ""
    sent_score = None
    sent_emotion = None
    sent_conf = None
    intent_type = None
    sentiment = {}
    conversation_sentiment = {}
    intent = {}
    sentiment_gate = build_sentiment_gate(message_text, {})
    support_plan: dict = {}
    history_message = {
        "id": msg_id,
        "conversation_id": "",
        "content": message_text,
        "sender_type": "customer",
        "sender_name": customer.get("name", "Unknown"),
        "attachments": [],
        "created_at": now_ts(),
        "company_id": company_id,
    }
    try:
        async with db.transaction() as conn:
            if relaxed_billing_env():
                billing_gate = "allow"
            else:
                billing_gate = await inbound_conversation_billing_precheck(
                    conn, company_id, idempotency_key=usage_idempotency_key
                )
            if billing_gate == "duplicate":
                logger.info(
                    "Inbound billing idempotent replay skipped company_id=%s channel=%s key=%s",
                    company_id,
                    channel,
                    usage_idempotency_key,
                )
                return None
            if billing_gate == "denied":
                logger.warning(
                    "Inbound message skipped: monthly conversation quota exceeded company_id=%s channel=%s",
                    company_id,
                    channel,
                )
                return None

            convo = r(
                await conn.fetchrow(
                    "SELECT * FROM conversations WHERE customer_id=$1 AND channel=$2 AND status=ANY($3) AND company_id=$4 "
                    "ORDER BY updated_at DESC LIMIT 1",
                    cid,
                    channel,
                    ["open", "pending", "escalated"],
                    company_id,
                )
            )
            if not convo:
                convo_id = make_id()
                # Match db_helpers get_or_create_contact_conversation: list columns through agent_type, then NOT NULL sentiment fields as bound parameters.
                await conn.execute(
                    "INSERT INTO conversations(id,company_id,customer_id,customer_name,customer_avatar,channel,subject,status,priority,assigned_to,assigned_name,ai_handled,agent_type,"  # noqa: E501
                    "channel_id,sentiment_score,sentiment_label,message_count,last_message,last_message_at,unread_count,session_id,page_url,created_at,updated_at) "  # noqa: E501
                    "VALUES($1,$2,$3,$4,$5,$6,$7,'open','medium',$8,$9,TRUE,'generic',$10,$11::numeric,$12,0,'',NOW(),0,'','',NOW(),NOW())",
                    convo_id,
                    company_id,
                    cid,
                    customer.get("name", "Unknown"),
                    "",
                    channel,
                    f"New {channel} conversation",
                    "",
                    "",
                    channel_binding,
                    0,
                    "neutral",
                )
                await conn.execute(
                    "INSERT INTO conversation_tags(conversation_id,tag) VALUES($1,'auto-captured') ON CONFLICT DO NOTHING",
                    convo_id,
                )
                convo = r(await conn.fetchrow("SELECT * FROM conversations WHERE id=$1", convo_id))
            elif company_id and not convo.get("company_id"):
                await conn.execute(
                    "UPDATE conversations SET company_id=$1,updated_at=NOW() WHERE id=$2",
                    company_id,
                    convo["id"],
                )
                convo["company_id"] = company_id
            if channel_binding and convo.get("channel_id") != channel_binding:
                await conn.execute(
                    "UPDATE conversations SET channel_id=$1,updated_at=NOW() WHERE id=$2 AND company_id=$3",
                    channel_binding,
                    convo["id"],
                    company_id,
                )
                convo["channel_id"] = channel_binding

            convo_id = str(convo["id"])
            history_message["conversation_id"] = convo_id
            await conn.execute(
                "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,sentiment_score,"
                "sentiment_emotion,sentiment_confidence,intent_type,external_message_id,read,created_at) "
                "VALUES($1,$2,$3,$4,'customer',$5,$6,$7,$8,$9,$10,$11,FALSE,NOW())",
                msg_id,
                company_id,
                convo_id,
                message_text,
                cid,
                customer.get("name", "Unknown"),
                sent_score,
                sent_emotion,
                sent_conf,
                intent_type,
                inbound_external_message_id,
            )
            if relaxed_billing_env():
                await insert_conversation_usage_relaxed(
                    conn,
                    company_id,
                    channel=channel,
                    idempotency_key=usage_idempotency_key,
                )
            else:
                await insert_conversation_usage_row(
                    conn,
                    company_id,
                    channel=channel,
                    idempotency_key=usage_idempotency_key,
                )
            saved_attachments = await save_message_attachments(conn, msg_id, attachments or [])
            history_message["attachments"] = saved_attachments
            await insert_chat_history_record(conn, convo, history_message)
            await conn.execute(
                "UPDATE conversations SET last_message=$1,last_message_at=NOW(),updated_at=NOW(),message_count=message_count+1,unread_count=unread_count+1 "  # noqa: E501
                "WHERE id=$2",
                _message_preview(message_text, saved_attachments, "customer"),
                convo_id,
            )
    except Exception:
        logger.exception(
            "Inbound message transaction failed company_id=%s channel=%s message_id=%s external_message_id=%s",
            company_id,
            channel,
            msg_id,
            inbound_external_message_id,
        )
        return None

    create_safe_detached_task(
        db,
        _try_auto_unify(db, company_id, customer, channel),
        name=f"webhook-auto-unify-{msg_id}",
        company_id=company_id,
        channel=channel,
        trace_id=str(metadata_payload.get("trace_id") or ""),
        event_id=inbound_external_message_id or msg_id,
        payload={"customer_id": cid, "message_id": msg_id},
    )
    try:
        from data_pipeline.ingestion.raw_store import capture_raw_message

        await capture_raw_message(
            db,
            conversation=dict(convo),
            message=dict(history_message),
            source=str(convo.get("channel") or "message"),
        )
    except Exception as exc:
        logger.warning(
            "raw message capture failed company_id=%s conversation_id=%s message_id=%s error=%s",
            company_id,
            convo_id,
            msg_id,
            exc,
        )
    trace_id = _trace_id_from_context(str(metadata_payload.get("trace_id") or ""))
    if trace_id:
        metadata_payload["trace_id"] = trace_id

    try:
        msgs_history = await fetch_messages_with_attachments(
            db,
            convo_id,
            limit=webhook_message_history_fetch_limit(),
        )
        msgs_history = _truncate_history_for_token_budget(
            msgs_history,
            company_id=company_id,
            conversation_id=convo_id,
            channel=channel,
            trace_id=trace_id,
        )
        workflow = await orchestrate_message_workflow(
            MessageWorkflowRequest(
                trace_id=trace_id,
                company_id=company_id,
                conversation_id=convo_id,
                customer_id=cid,
                lead_id=str(result.get("lead_id") or ""),
                message_id=msg_id,
                channel=channel,
                source=str(metadata_payload.get("source") or channel),
                message_text=message_text,
                sender_name=customer.get("name", "Unknown"),
                sender_contact=sender_contact,
                conversation_context=msgs_history,
                customer=customer,
                lead=lead,
                metadata={
                    **metadata_payload,
                    "source": metadata_payload.get("source") or f"webhook_{channel}",
                    "trace_id": trace_id,
                },
            ),
            db=db,
        )
        capture, _, support_output, _ = _extract_workflow_outputs(
            workflow,
            context_label="process_incoming_message",
            company_id=company_id,
            conversation_id=convo_id,
            channel=channel,
            trace_id=trace_id,
        )
        support_plan = dict(support_output or {})
        sentiment = _coerce_workflow_dict(
            capture.get("sentiment"),
            field_name="capture.sentiment",
            workflow=workflow,
            context_label="process_incoming_message",
            company_id=company_id,
            conversation_id=convo_id,
            channel=channel,
            trace_id=trace_id,
        )
        conversation_sentiment = _coerce_workflow_dict(
            capture.get("conversation_sentiment"),
            field_name="capture.conversation_sentiment",
            workflow=workflow,
            context_label="process_incoming_message",
            company_id=company_id,
            conversation_id=convo_id,
            channel=channel,
            trace_id=trace_id,
        )
        intent = _coerce_workflow_dict(
            capture.get("intent"),
            field_name="capture.intent",
            workflow=workflow,
            context_label="process_incoming_message",
            company_id=company_id,
            conversation_id=convo_id,
            channel=channel,
            trace_id=trace_id,
        )
        sentiment_gate = _coerce_workflow_dict(
            capture.get("sentiment_gate"),
            field_name="capture.sentiment_gate",
            workflow=workflow,
            context_label="process_incoming_message",
            company_id=company_id,
            conversation_id=convo_id,
            channel=channel,
            trace_id=trace_id,
        ) or build_sentiment_gate(message_text, sentiment)
        sent_score = _float_or_none(sentiment.get("score"))
        sent_emotion = str(sentiment.get("emotion", "neutral"))
        sent_conf = _float_or_none(sentiment.get("confidence"))
        intent_type = str(intent.get("intent", ""))
        await db.execute(
            "UPDATE messages SET sentiment_score=$1,sentiment_emotion=$2,sentiment_confidence=$3,intent_type=$4 WHERE id=$5",  # noqa: E501
            sent_score,
            sent_emotion,
            sent_conf,
            intent_type,
            msg_id,
        )
        conversation_score = _float_or_none((conversation_sentiment or {}).get("score"))
        conversation_label = str(
            (conversation_sentiment or {}).get("sentiment_label")
            or (conversation_sentiment or {}).get("label")
            or (conversation_sentiment or {}).get("emotion")
            or sent_emotion
            or "neutral"
        )
        await db.execute(
            "UPDATE conversations SET sentiment_score=$1,sentiment_label=$2 WHERE id=$3",
            conversation_score,
            conversation_label,
            convo_id,
        )
        if not sentiment_gate.get("ai_response_allowed", True):
            for tag in ["toxic", "escalating"]:
                await db.execute(
                    "INSERT INTO conversation_tags(conversation_id,tag) VALUES($1,$2) ON CONFLICT DO NOTHING",
                    convo_id,
                    tag,
                )
            if dict(sentiment_gate.get("risk_flags") or {}).get("possible_hate_speech"):
                await db.execute(
                    "INSERT INTO conversation_tags(conversation_id,tag) VALUES($1,'possible-hate-speech') ON CONFLICT DO NOTHING",  # noqa: E501
                    convo_id,
                )
    except Exception as e:
        logger.error(f"Message orchestration failed: {e}")

    customer_message = await _load_message_with_attachments(db, msg_id)
    await emit_new_message(convo_id, customer_message)

    ai_message = None
    recent_human_agent_message = await _recent_human_agent_message_within_cooldown(
        db,
        company_id=company_id,
        conversation_id=convo_id,
    )
    if recent_human_agent_message:
        logger.info(
            "Skipping AI reply due to recent human agent activity company_id=%s conversation_id=%s channel=%s trace_id=%s cooldown_seconds=%s agent_message_id=%s agent_name=%s",
            company_id,
            convo_id,
            channel,
            trace_id,
            ai_response_cooldown_seconds(),
            recent_human_agent_message.get("id", ""),
            recent_human_agent_message.get("sender_name", ""),
        )
    elif convo.get("ai_handled", True) and await is_company_ai_enabled(db, company_id):
        try:
            support_result = dict(support_plan or {})
            if not support_result:
                support_result = {
                    "response": "",
                    "confidence": 0.0,
                    "deliver_response": False,
                    "escalate": True,
                    "escalation_reason": "Orchestrator response was unavailable for this inbound message.",
                    "next_action": "manual_review",
                    "api_error": True,
                }
            if support_result.get("api_error") and not support_result.get("response"):
                system_id = make_id()
                await db.execute(
                    "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,is_alert,read,created_at) "  # noqa: E501
                    "VALUES($1,$2,$3,$4,'system','system','System',TRUE,FALSE,NOW())",
                    system_id,
                    company_id,
                    convo_id,
                    "AI service unavailable - Please respond manually",
                )
                system_message = r(await db.fetchrow("SELECT * FROM messages WHERE id=$1", system_id))
                await emit_new_message(convo_id, system_message)
                return {
                    "conversation_id": convo_id,
                    "message_id": msg_id,
                    "customer_id": cid,
                    "lead_id": result["lead_id"],
                    "customer_message": customer_message,
                    "ai_message": None,
                    "sentiment_analysis": sentiment_gate,
                }
            if support_result.get("escalate"):
                escalation = await escalate_conversation_to_human(
                    db,
                    convo_id,
                    company_id,
                    customer.get("name", "Customer"),
                    channel,
                    reason=str(
                        support_result.get("escalation_reason")
                        or "AI responses paused after detecting a human handoff request or critical issue."
                    ),
                    automatic=True,
                )
                await emit_new_message(convo_id, escalation["message"])
                create_safe_detached_task(
                    db,
                    _notify_agents_handoff(
                        db,
                        None,
                        {},
                        company_id,
                        convo_id,
                        customer.get("name", "Customer"),
                        float(support_result.get("confidence", 0.0) or 0.0),
                    ),
                    name=f"notify-handoff-{convo_id}",
                    company_id=company_id,
                    channel=channel,
                    trace_id=trace_id,
                    event_id=inbound_external_message_id or msg_id,
                )
                return {
                    "conversation_id": convo_id,
                    "message_id": msg_id,
                    "customer_id": cid,
                    "lead_id": result["lead_id"],
                    "customer_message": customer_message,
                    "ai_message": None,
                    "sentiment_analysis": sentiment_gate,
                }
            if support_result.get("deliver_response") and support_result.get("response"):
                ai_started_at = time.monotonic()
                await wait_for_ai_response_timing(message_text, ai_started_at)
                ai_id = make_id()
                await db.execute(
                    "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,ai_confidence,read,created_at) "  # noqa: E501
                    "VALUES($1,$2,$3,$4,'ai','ai-assistant','AI Assistant',$5,FALSE,NOW())",
                    ai_id,
                    company_id,
                    convo_id,
                    support_result["response"],
                    float(support_result.get("confidence", 0.0) or 0.0),
                )
                ai_attachments = await save_message_attachments(
                    db,
                    ai_id,
                    _coerce_workflow_list(
                        support_result.get("attachments"),
                        field_name="support.attachments",
                        workflow=None,
                        context_label="process_incoming_message",
                        company_id=company_id,
                        conversation_id=convo_id,
                        channel=channel,
                        trace_id=trace_id,
                    )
                    or _coerce_workflow_list(
                        support_result.get("product_images"),
                        field_name="support.product_images",
                        workflow=None,
                        context_label="process_incoming_message",
                        company_id=company_id,
                        conversation_id=convo_id,
                        channel=channel,
                        trace_id=trace_id,
                    ),
                )
                await persist_chat_history(
                    db,
                    convo,
                    {
                        "id": ai_id,
                        "conversation_id": convo_id,
                        "content": support_result["response"],
                        "sender_type": "ai",
                        "sender_name": "AI Assistant",
                        "attachments": ai_attachments,
                        "created_at": now_ts(),
                    },
                )
                await db.execute(
                    "UPDATE conversations SET last_message=$1,last_message_at=NOW(),message_count=message_count+1 WHERE id=$2",  # noqa: E501
                    _message_preview(support_result["response"], ai_attachments, "ai"),
                    convo_id,
                )
                create_safe_detached_task(
                    db,
                    persist_ai_session_record(
                        db,
                        company_id,
                        convo_id,
                        message_text,
                        support_result["response"],
                        {
                            "confidence": float(support_result.get("confidence", 0.0) or 0.0),
                            "source": f"webhook_{channel}",
                            "llm_id": support_result.get("llm_id", ""),
                            "agent_id": support_result.get("agent_id", ""),
                            "intent_name": support_result.get("intent_name", ""),
                            "channel": channel,
                        },
                    ),
                    name=f"persist-ai-session-{convo_id}",
                    company_id=company_id,
                    channel=channel,
                    trace_id=trace_id,
                    event_id=ai_id,
                )
                ai_message = await _load_message_with_attachments(db, ai_id)
                await emit_new_message(convo_id, ai_message)
                recipient_id = _resolve_outbound_recipient(
                    channel,
                    convo,
                    customer,
                    sender_contact,
                )
                if recipient_id:
                    sent, error = await _send_outbound_response_via_channel_layer(
                        db=db,
                        company_id=company_id,
                        channel=channel,
                        recipient_id=recipient_id,
                        content=support_result["response"],
                        conversation_id=convo_id,
                        attachments=ai_attachments,
                        db_message_id=ai_id,
                        metadata={
                            "source": f"webhook_{channel}",
                            "trace_id": trace_id,
                        },
                    )
                    if not sent:
                        logger.warning(
                            "Unified outbound send failed conversation=%s channel=%s error=%s",
                            convo_id,
                            channel,
                            error,
                        )
                else:
                    await _persist_outbound_message_state(
                        db,
                        company_id=company_id,
                        db_message_id=ai_id,
                        delivery_status="failed",
                    )
                    await _emit_outbound_failure_notice(
                        db,
                        company_id=company_id,
                        conversation_id=convo_id,
                        channel=channel,
                        trace_id=trace_id,
                        failed_message_id=ai_id,
                        error="Missing outbound recipient",
                    )
                    logger.warning(
                        "Unified outbound skipped due to missing recipient company_id=%s conversation_id=%s channel=%s trace_id=%s",
                        company_id,
                        convo_id,
                        channel,
                        trace_id,
                    )
            else:
                escalation = await escalate_conversation_to_human(
                    db,
                    convo_id,
                    company_id,
                    customer.get("name", "Customer"),
                    channel,
                    reason=str(
                        support_result.get("escalation_reason")
                        or f"AI confidence was too low ({float(support_result.get('confidence', 0.0) or 0.0):.0%}). Human review is required."  # noqa: E501
                    ),
                    automatic=True,
                )
                await emit_new_message(convo_id, escalation["message"])
                create_safe_detached_task(
                    db,
                    _notify_agents_handoff(
                        db,
                        None,
                        {},
                        company_id,
                        convo_id,
                        customer.get("name", "Customer"),
                        float(support_result.get("confidence", 0.0) or 0.0),
                    ),
                    name=f"notify-handoff-{convo_id}",
                    company_id=company_id,
                    channel=channel,
                    trace_id=trace_id,
                    event_id=inbound_external_message_id or msg_id,
                )
        except Exception as e:
            logger.error(f"AI auto-response failed: {e}")
    return {
        "conversation_id": convo_id,
        "message_id": msg_id,
        "customer_id": cid,
        "lead_id": result["lead_id"],
        "customer_message": customer_message,
        "ai_message": ai_message,
        "sentiment_analysis": sentiment_gate,
    }


# Webhook endpoints
@router.get("/webhooks/whatsapp")
async def whatsapp_verify(
    request: Request,
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    db = _db(request)
    if hub_mode == "subscribe" and await _is_valid_verify_token(db, "whatsapp", hub_token or ""):
        return int(hub_challenge) if hub_challenge else ""
    raise HTTPException(403, "Verification failed")


@router.get("/webhooks/whatsapp/meta")
async def whatsapp_meta_verify(
    request: Request,
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    return await whatsapp_verify(
        request=request,
        hub_mode=hub_mode,
        hub_token=hub_token,
        hub_challenge=hub_challenge,
    )


@router.post("/webhooks/whatsapp")
async def whatsapp_webhook(request: Request):
    db = _db(request)
    raw_body = await request.body()
    event_id = await _verify_meta_webhook_request(request, db, "whatsapp", raw_body)
    payload = _decode_webhook_json(raw_body)
    bridge_secret = (os.environ.get("WHATSAPP_BRIDGE_SECRET") or os.environ.get("BRIDGE_SECRET") or "").strip()
    provided_bridge_secret = (request.headers.get("X-Bridge-Secret") or "").strip()
    trusted_bridge = bool(
        bridge_secret and provided_bridge_secret and hmac.compare_digest(provided_bridge_secret, bridge_secret)
    )
    create_safe_detached_task(
        db,
        _handle_whatsapp_webhook_payload(
            db,
            payload,
            event_id=event_id,
            allow_direct_company_id=trusted_bridge,
        ),
        name="webhook-whatsapp",
        channel="whatsapp",
        event_id=event_id,
        payload=payload,
    )
    return {"status": "received"}


@router.post("/webhooks/whatsapp/meta")
async def whatsapp_meta_webhook(request: Request):
    return await whatsapp_webhook(request)


@router.get("/webhooks/facebook")
async def facebook_verify(
    request: Request,
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    db = _db(request)
    if hub_mode == "subscribe" and await _is_valid_verify_token(db, "facebook", hub_token or ""):
        return int(hub_challenge) if hub_challenge else ""
    raise HTTPException(403, "Verification failed")


@router.post("/webhooks/facebook")
async def facebook_webhook(request: Request):
    db = _db(request)
    raw_body = await request.body()
    event_id = await _verify_meta_webhook_request(request, db, "facebook", raw_body)
    payload = _decode_webhook_json(raw_body)
    create_safe_detached_task(
        db,
        _handle_facebook_webhook_payload(db, payload, event_id=event_id),
        name="webhook-facebook",
        channel="facebook",
        event_id=event_id,
        payload=payload,
    )
    return {"status": "received"}


@router.get("/webhooks/instagram")
async def instagram_verify(
    request: Request,
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    db = _db(request)
    if hub_mode == "subscribe" and await _is_valid_verify_token(
        db,
        "instagram",
        hub_token or "",
    ):
        return int(hub_challenge) if hub_challenge else ""
    raise HTTPException(403, "Verification failed")


@router.post("/webhooks/instagram")
async def instagram_webhook(request: Request):
    db = _db(request)
    raw_body = await request.body()
    event_id = await _verify_meta_webhook_request(request, db, "instagram", raw_body)
    payload = _decode_webhook_json(raw_body)
    create_safe_detached_task(
        db,
        _handle_instagram_webhook_payload(db, payload, event_id=event_id),
        name="webhook-instagram",
        channel="instagram",
        event_id=event_id,
        payload=payload,
    )
    return {"status": "received"}


@router.post("/webhooks/web-chat")
async def web_chat_webhook(request: Request):
    db = _db(request)
    raw_body = await request.body()
    payload = _decode_webhook_json(raw_body)
    has_signed_headers = bool(request.headers.get("X-Webhook-Timestamp") or request.headers.get("X-Webhook-Signature"))
    if has_signed_headers:
        await _verify_signed_header_webhook(
            request,
            channel="web_chat",
            secret_env="WEB_CHAT_WEBHOOK_SECRET",
            raw_body=raw_body,
        )
    else:
        await _verify_web_chat_widget_request(request, db, payload)

    create_safe_detached_task(
        db,
        _record_webhook_event_safe(
            db,
            "web_chat",
            payload,
            metadata={"company_id": payload.get("company_id", "")},
        ),
        name="webhook-web-chat-audit",
        channel="web_chat",
        event_id=str(payload.get("event_id") or payload.get("message_id") or ""),
        payload=payload,
    )
    try:
        adapter = get_channel_registry().get_or_none(ChannelType.WEB_CHAT)
        if adapter is None:
            raise RuntimeError("Web chat adapter is not registered")

        company_id = str(payload.get("company_id") or "").strip()
        unified_message = await adapter.receive_message(payload, db, company_id)
        unified_message.trace_id = _trace_id_from_context(str((unified_message.metadata or {}).get("trace_id") or ""))
        unified_message.metadata.update(
            {
                "company_id": company_id,
                "source": "webhook_web_chat",
                "trace_id": unified_message.trace_id,
            }
        )
        unified_message = await _CHANNEL_NORMALIZER.normalize(unified_message, db)

        session_id = str(
            (unified_message.metadata or {}).get("session_id") or payload.get("session_id") or make_id()
        ).strip()
        content = str(unified_message.content or "").strip()
        attachments = _legacy_attachments_from_unified(list(unified_message.attachments or []))
        if not attachments and isinstance(payload.get("attachments", []), list):
            attachments = list(payload.get("attachments", []))
        customer_name = str(
            (unified_message.metadata or {}).get("sender_name") or payload.get("customer_name") or "Website Visitor"
        ).strip()
        customer_email = str(
            (unified_message.metadata or {}).get("sender_contact") or payload.get("customer_email") or ""
        ).strip()
        company_id = str(unified_message.tenant_id or company_id).strip()
        page_url = str((unified_message.metadata or {}).get("page_url") or payload.get("page_url") or "").strip()
        if not content and not attachments:
            return {"status": "error", "detail": "No message content or attachments"}
        convo = r(
            await db.fetchrow(
                "SELECT * FROM conversations WHERE company_id=$1 AND session_id=$2 LIMIT 1",
                company_id,
                session_id,
            )
        )
        if not convo:
            # Omni-channel identity resolution: reuse the existing customer by
            # normalized email or phone (last-10-digits) before creating a new
            # one. This ensures the same person reaching us via widget + WhatsApp
            # + email collapses to a single profile.
            resolved_customer_id = str(getattr(unified_message, "resolved_customer_id", "") or "").strip()
            if not resolved_customer_id and customer_email:
                normalized_email = customer_email.strip().lower()
                if normalized_email:
                    existing = await db.fetchval(
                        "SELECT id FROM customers WHERE company_id=$1 AND LOWER(email)=$2 LIMIT 1",
                        company_id,
                        normalized_email,
                    )
                    if existing:
                        resolved_customer_id = str(existing)
            if not resolved_customer_id:
                sender_contact_meta = str((unified_message.metadata or {}).get("sender_contact") or "").strip()
                import re as _re_wc

                digits = _re_wc.sub(r"\D", "", sender_contact_meta)
                last10 = digits[-10:] if len(digits) >= 10 else digits
                if last10:
                    existing = await db.fetchval(
                        "SELECT id FROM customers WHERE company_id=$1 "
                        "AND regexp_replace(phone, '\\D', '', 'g') LIKE $2 LIMIT 1",
                        company_id,
                        f"%{last10}",
                    )
                    if existing:
                        resolved_customer_id = str(existing)

            nid = make_id()
            if resolved_customer_id:
                cust_id = resolved_customer_id
            else:
                cust_id = make_id()
                await db.execute(
                    "INSERT INTO customers(id,company_id,name,email,phone,segment,avatar,lifecycle_stage,lifetime_value,avg_sentiment,recent_tickets,complaint_count,days_since_last_contact,total_conversations,created_at,updated_at) VALUES($1,$2,$3,$4,'','website','','lead',0,0,0,0,0,1,NOW(),NOW())",  # noqa: E501
                    cust_id,
                    company_id,
                    customer_name,
                    customer_email,
                )
                await db.execute(
                    "INSERT INTO customer_tags(customer_id,tag) VALUES($1,'website') ON CONFLICT DO NOTHING",
                    cust_id,
                )
            await db.execute(
                "INSERT INTO conversations(id,company_id,customer_id,customer_name,channel,subject,status,priority,ai_handled,sentiment_score,sentiment_label,message_count,last_message,last_message_at,unread_count,session_id,page_url,created_at,updated_at) VALUES($1,$2,$3,$4,'web_chat','Website Chat','open','medium',TRUE,0,'neutral',0,'',NOW(),1,$5,$6,NOW(),NOW())",  # noqa: E501
                nid,
                company_id,
                cust_id,
                customer_name,
                session_id,
                page_url,
            )
            convo = r(await db.fetchrow("SELECT * FROM conversations WHERE id=$1", nid))
        convo_id = convo["id"]
        customer_id = convo.get("customer_id", "")
        customer = r(await db.fetchrow("SELECT * FROM customers WHERE id=$1", customer_id)) if customer_id else {}
        lead_capture = await _auto_capture_lead(
            db,
            "web_chat",
            customer_name,
            customer_email,
            content or "image upload",
            {
                "company_id": company_id,
                "page_url": page_url,
                "customer_id": customer_id,
                "source": "web_chat",
            },
        )
        if lead_capture and lead_capture.get("customer"):
            customer = lead_capture["customer"]
            customer_id = customer.get("id", customer_id)
        lead = dict((lead_capture or {}).get("lead") or {})
        msg_id = make_id()
        sentiment = {}
        conversation_sentiment = {}
        intent = {}
        sentiment_gate = build_sentiment_gate(content, {})
        sent_score = None
        sent_emotion = None
        sent_conf = None
        intent_type = None
        support_plan: dict = {}
        await db.execute(
            "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,sentiment_score,sentiment_emotion,sentiment_confidence,intent_type,read,created_at) VALUES($1,$2,$3,$4,'customer',$5,$6,$7,$8,$9,$10,FALSE,NOW())",  # noqa: E501
            msg_id,
            company_id,
            convo_id,
            content,
            session_id,
            customer_name,
            sent_score,
            sent_emotion,
            sent_conf,
            intent_type,
        )
        saved_attachments = await save_message_attachments(db, msg_id, attachments)
        await persist_chat_history(
            db,
            convo,
            {
                "id": msg_id,
                "conversation_id": convo_id,
                "content": content,
                "sender_type": "customer",
                "sender_name": customer_name,
                "attachments": saved_attachments,
                "created_at": now_ts(),
            },
        )
        await db.execute(
            "UPDATE conversations SET last_message=$1,last_message_at=NOW(),updated_at=NOW(),message_count=message_count+1,unread_count=unread_count+1 WHERE id=$2",  # noqa: E501
            _message_preview(content, saved_attachments, "customer"),
            convo_id,
        )
        trace_id = _trace_id_from_context(str((unified_message.metadata or {}).get("trace_id") or ""))
        # Ingest into ETL pipeline (fire-and-forget)
        try:
            from data_pipeline.ingestion.raw_store import capture_raw_message as _capture_raw_msg

            create_safe_detached_task(
                db,
                _capture_raw_msg(
                    db,
                    conversation=dict(convo),
                    message={
                        "id": msg_id,
                        "company_id": company_id,
                        "conversation_id": convo_id,
                        "content": content,
                        "sender_type": "customer",
                        "sender_name": customer_name,
                    },
                    source="web_chat",
                    metadata={"action": "webhook_web_chat", "trace_id": trace_id},
                ),
                name=f"etl-webchat-{msg_id}",
                idempotency_key=f"etl:msg:{company_id}:{msg_id}",
                company_id=company_id,
                channel="web_chat",
                trace_id=trace_id,
                event_id=msg_id,
                source_queue="etl",
            )
        except Exception as _etl_exc:
            logger.debug("ETL web chat capture dispatch failed: %s", _etl_exc)
        try:
            msgs_history = await fetch_messages_with_attachments(
                db,
                convo_id,
                limit=webhook_message_history_fetch_limit(),
            )
            msgs_history = _truncate_history_for_token_budget(
                msgs_history,
                company_id=company_id,
                conversation_id=convo_id,
                channel="web_chat",
                trace_id=trace_id,
            )
            workflow = await orchestrate_message_workflow(
                MessageWorkflowRequest(
                    trace_id=trace_id,
                    company_id=company_id,
                    conversation_id=convo_id,
                    customer_id=customer_id,
                    lead_id=str((lead_capture or {}).get("lead_id") or ""),
                    message_id=msg_id,
                    channel="web_chat",
                    source="web_chat",
                    message_text=content,
                    sender_name=customer.get("name", customer_name),
                    sender_contact=customer_email,
                    conversation_context=msgs_history,
                    customer=customer or {},
                    lead=lead,
                    metadata={
                        "source": "webhook_web_chat",
                        "page_url": page_url,
                        "trace_id": trace_id,
                    },
                ),
                db=db,
            )
            capture, _, support_output, _ = _extract_workflow_outputs(
                workflow,
                context_label="web_chat_webhook",
                company_id=company_id,
                conversation_id=convo_id,
                channel="web_chat",
                trace_id=trace_id,
            )
            support_plan = dict(support_output or {})
            sentiment = _coerce_workflow_dict(
                capture.get("sentiment"),
                field_name="capture.sentiment",
                workflow=workflow,
                context_label="web_chat_webhook",
                company_id=company_id,
                conversation_id=convo_id,
                channel="web_chat",
                trace_id=trace_id,
            )
            conversation_sentiment = _coerce_workflow_dict(
                capture.get("conversation_sentiment"),
                field_name="capture.conversation_sentiment",
                workflow=workflow,
                context_label="web_chat_webhook",
                company_id=company_id,
                conversation_id=convo_id,
                channel="web_chat",
                trace_id=trace_id,
            )
            intent = _coerce_workflow_dict(
                capture.get("intent"),
                field_name="capture.intent",
                workflow=workflow,
                context_label="web_chat_webhook",
                company_id=company_id,
                conversation_id=convo_id,
                channel="web_chat",
                trace_id=trace_id,
            )
            sentiment_gate = _coerce_workflow_dict(
                capture.get("sentiment_gate"),
                field_name="capture.sentiment_gate",
                workflow=workflow,
                context_label="web_chat_webhook",
                company_id=company_id,
                conversation_id=convo_id,
                channel="web_chat",
                trace_id=trace_id,
            ) or build_sentiment_gate(content, sentiment)
            sent_score = _float_or_none(sentiment.get("score"))
            sent_emotion = str(sentiment.get("emotion", "neutral"))
            sent_conf = _float_or_none(sentiment.get("confidence"))
            intent_type = str(intent.get("intent", ""))
            await db.execute(
                "UPDATE messages SET sentiment_score=$1,sentiment_emotion=$2,sentiment_confidence=$3,intent_type=$4 WHERE id=$5",  # noqa: E501
                sent_score,
                sent_emotion,
                sent_conf,
                intent_type,
                msg_id,
            )
            conversation_score = _float_or_none((conversation_sentiment or {}).get("score"))
            conversation_label = str(
                (conversation_sentiment or {}).get("sentiment_label")
                or (conversation_sentiment or {}).get("label")
                or (conversation_sentiment or {}).get("emotion")
                or sent_emotion
                or "neutral"
            )
            await db.execute(
                "UPDATE conversations SET sentiment_score=$1,sentiment_label=$2 WHERE id=$3",
                conversation_score,
                conversation_label,
                convo_id,
            )
            if not sentiment_gate.get("ai_response_allowed", True):
                for tag in ["toxic", "escalating"]:
                    await db.execute(
                        "INSERT INTO conversation_tags(conversation_id,tag) VALUES($1,$2) ON CONFLICT DO NOTHING",
                        convo_id,
                        tag,
                    )
                if dict(sentiment_gate.get("risk_flags") or {}).get("possible_hate_speech"):
                    await db.execute(
                        "INSERT INTO conversation_tags(conversation_id,tag) VALUES($1,'possible-hate-speech') ON CONFLICT DO NOTHING",  # noqa: E501
                        convo_id,
                    )
        except Exception as e:
            logger.error(f"Web chat orchestration failed: {e}")
        customer_message = await _load_message_with_attachments(db, msg_id)
        await emit_new_message(convo_id, customer_message)
        ai_response_text = None
        ai_message = None
        is_ai = False
        recent_human_agent_message = await _recent_human_agent_message_within_cooldown(
            db,
            company_id=company_id,
            conversation_id=convo_id,
        )
        if recent_human_agent_message:
            logger.info(
                "Skipping web chat AI reply due to recent human agent activity company_id=%s conversation_id=%s trace_id=%s cooldown_seconds=%s agent_message_id=%s agent_name=%s",
                company_id,
                convo_id,
                trace_id,
                ai_response_cooldown_seconds(),
                recent_human_agent_message.get("id", ""),
                recent_human_agent_message.get("sender_name", ""),
            )
        elif convo.get("ai_handled", True) and await is_company_ai_enabled(db, company_id):
            try:
                support_result = dict(support_plan or {})
                if not support_result:
                    support_result = {
                        "response": "",
                        "confidence": 0.0,
                        "deliver_response": False,
                        "escalate": True,
                        "escalation_reason": "Orchestrator response was unavailable for this inbound web chat message.",
                        "next_action": "manual_review",
                        "api_error": True,
                    }
                if support_result.get("api_error") and not support_result.get("response"):
                    system_id = make_id()
                    await db.execute(
                        "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,is_alert,read,created_at) "  # noqa: E501
                        "VALUES($1,$2,$3,$4,'system','system','System',TRUE,FALSE,NOW())",
                        system_id,
                        company_id,
                        convo_id,
                        "AI service unavailable - Please respond manually",
                    )
                    system_message = r(await db.fetchrow("SELECT * FROM messages WHERE id=$1", system_id))
                    await emit_new_message(convo_id, system_message)
                    return {
                        "status": "ok",
                        "conversation_id": convo_id,
                        "customer_message": customer_message,
                        "ai_message": None,
                        "response": "Thanks for your message! A team member will respond shortly.",
                        "is_ai": False,
                    }
                if support_result.get("escalate"):
                    escalation = await escalate_conversation_to_human(
                        db,
                        convo_id,
                        company_id,
                        customer.get("name", customer_name),
                        "web_chat",
                        reason=str(
                            support_result.get("escalation_reason")
                            or "AI responses paused after detecting a human handoff request or critical issue."
                        ),
                        automatic=True,
                    )
                    await emit_new_message(convo_id, escalation["message"])
                    create_safe_detached_task(
                        db,
                        _notify_agents_handoff(
                            db,
                            None,
                            {},
                            company_id,
                            convo_id,
                            customer.get("name", customer_name),
                            float(support_result.get("confidence", 0.0) or 0.0),
                        ),
                        name=f"notify-handoff-{convo_id}",
                        company_id=company_id,
                        channel="web_chat",
                        trace_id=trace_id,
                        event_id=msg_id,
                    )
                    return {
                        "status": "ok",
                        "conversation_id": convo_id,
                        "customer_message": customer_message,
                        "ai_message": None,
                        "response": "A human agent has been alerted and will reply shortly.",
                        "is_ai": False,
                    }
                if support_result.get("deliver_response") and support_result.get("response"):
                    ai_started_at = time.monotonic()
                    await wait_for_ai_response_timing(content, ai_started_at)
                    ai_id = make_id()
                    await db.execute(
                        "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,ai_confidence,read,created_at) VALUES($1,$2,$3,$4,'ai','ai-assistant','AI Assistant',$5,FALSE,NOW())",  # noqa: E501
                        ai_id,
                        company_id,
                        convo_id,
                        support_result["response"],
                        float(support_result.get("confidence", 0.0) or 0.0),
                    )
                    ai_attachments = await save_message_attachments(
                        db,
                        ai_id,
                        _coerce_workflow_list(
                            support_result.get("attachments"),
                            field_name="support.attachments",
                            workflow=None,
                            context_label="web_chat_webhook",
                            company_id=company_id,
                            conversation_id=convo_id,
                            channel="web_chat",
                            trace_id=trace_id,
                        )
                        or _coerce_workflow_list(
                            support_result.get("product_images"),
                            field_name="support.product_images",
                            workflow=None,
                            context_label="web_chat_webhook",
                            company_id=company_id,
                            conversation_id=convo_id,
                            channel="web_chat",
                            trace_id=trace_id,
                        ),
                    )
                    await persist_chat_history(
                        db,
                        convo,
                        {
                            "id": ai_id,
                            "conversation_id": convo_id,
                            "content": support_result["response"],
                            "sender_type": "ai",
                            "sender_name": "AI Assistant",
                            "attachments": ai_attachments,
                            "created_at": now_ts(),
                        },
                    )
                    await db.execute(
                        "UPDATE conversations SET last_message=$1,last_message_at=NOW(),message_count=message_count+1 WHERE id=$2",  # noqa: E501
                        _message_preview(support_result["response"], ai_attachments, "ai"),
                        convo_id,
                    )
                    create_safe_detached_task(
                        db,
                        persist_ai_session_record(
                            db,
                            company_id,
                            convo_id,
                            content,
                            support_result["response"],
                            {
                                "confidence": float(support_result.get("confidence", 0.0) or 0.0),
                                "source": "webhook_web_chat",
                                "llm_id": support_result.get("llm_id", ""),
                                "agent_id": support_result.get("agent_id", ""),
                                "intent_name": support_result.get("intent_name", ""),
                                "channel": "web_chat",
                            },
                        ),
                        name=f"persist-ai-session-{convo_id}",
                        company_id=company_id,
                        channel="web_chat",
                        trace_id=trace_id,
                        event_id=ai_id,
                    )
                    ai_message = await _load_message_with_attachments(db, ai_id)
                    sent, error = await _send_outbound_response_via_channel_layer(
                        db=db,
                        company_id=company_id,
                        channel="web_chat",
                        recipient_id=session_id,
                        content=support_result["response"],
                        conversation_id=convo_id,
                        attachments=ai_attachments,
                        db_message_id=ai_id,
                        metadata={
                            "source": "webhook_web_chat",
                            "trace_id": trace_id,
                            "conversation_id": convo_id,
                            "message_payload": ai_message,
                        },
                    )
                    if not sent:
                        logger.warning(
                            "Unified web chat outbound send failed conversation=%s error=%s",
                            convo_id,
                            error,
                        )
                        await emit_new_message(convo_id, ai_message)
                    ai_response_text = support_result["response"]
                    is_ai = True
                else:
                    escalation = await escalate_conversation_to_human(
                        db,
                        convo_id,
                        company_id,
                        customer.get("name", customer_name),
                        "web_chat",
                        reason=str(
                            support_result.get("escalation_reason")
                            or f"AI confidence was too low ({float(support_result.get('confidence', 0.0) or 0.0):.0%}). Human review is required."  # noqa: E501
                        ),
                        automatic=True,
                    )
                    await emit_new_message(convo_id, escalation["message"])
                    create_safe_detached_task(
                        db,
                        _notify_agents_handoff(
                            db,
                            None,
                            {},
                            company_id,
                            convo_id,
                            customer.get("name", customer_name),
                            float(support_result.get("confidence", 0.0) or 0.0),
                        ),
                        name=f"notify-handoff-{convo_id}",
                        company_id=company_id,
                        channel="web_chat",
                        trace_id=trace_id,
                        event_id=msg_id,
                    )
            except Exception as e:
                logger.error(f"Web chat AI failed: {e}")
        return {
            "status": "ok",
            "conversation_id": convo_id,
            "customer_message": customer_message,
            "ai_message": ai_message,
            "response": ai_response_text or "Thanks for your message! A team member will respond shortly.",
            "is_ai": is_ai,
        }
    except Exception as e:
        logger.error(f"Web chat webhook error: {e}")
        return {"status": "error", "detail": str(e)}


@router.post("/webhooks/lead-form")
async def lead_form_webhook(request: Request):
    db = _db(request)
    raw_body = await request.body()
    event_id = await _verify_meta_webhook_request(request, db, "lead_form", raw_body)
    payload = _decode_webhook_json(raw_body)
    create_safe_detached_task(
        db,
        _handle_lead_form_webhook_payload(db, payload, event_id=event_id),
        name="webhook-lead-form",
        channel="lead_form",
        event_id=event_id,
        payload={"page_payload": payload},
    )
    return {"status": "received"}


@router.post("/webhook/meta/lead-form")
async def lead_form_meta_webhook_public(request: Request):
    return await lead_form_webhook(request)


@router.get("/webhook/meta/whatsapp")
async def whatsapp_meta_verify_public(
    request: Request,
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    return await whatsapp_verify(
        request=request,
        hub_mode=hub_mode,
        hub_token=hub_token,
        hub_challenge=hub_challenge,
    )


@router.post("/webhook/meta/whatsapp")
async def whatsapp_meta_webhook_public(request: Request):
    return await whatsapp_webhook(request)


@router.get("/webhook/meta/facebook")
async def facebook_meta_verify_public(
    request: Request,
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    return await facebook_verify(
        request=request,
        hub_mode=hub_mode,
        hub_token=hub_token,
        hub_challenge=hub_challenge,
    )


@router.post("/webhook/meta/facebook")
async def facebook_meta_webhook_public(request: Request):
    return await facebook_webhook(request)


@router.get("/webhook/meta/instagram")
async def instagram_meta_verify_public(
    request: Request,
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    return await instagram_verify(
        request=request,
        hub_mode=hub_mode,
        hub_token=hub_token,
        hub_challenge=hub_challenge,
    )


@router.post("/webhook/meta/instagram")
async def instagram_meta_webhook_public(request: Request):
    return await instagram_webhook(request)


@router.post("/webhooks/external/purchases")
async def external_purchase_webhook(request: Request, token: Optional[str] = Query(None)):
    db = _db(request)
    raw_body = await request.body()
    await _verify_external_purchase_auth(request, raw_body, token)
    body = _decode_webhook_json(raw_body)
    phone = (body.get("customer_phone", "") or "").strip()
    if not phone:
        raise HTTPException(400, "customer_phone is required")
    purchase_id = make_id()
    create_safe_detached_task(
        db,
        _handle_external_purchase_webhook_payload(
            db,
            body,
            external_purchase_id=make_id(),
            purchase_id=purchase_id,
        ),
        name="webhook-external-purchase",
        channel="external_purchase",
        event_id=str(body.get("event_id") or purchase_id),
        payload=body,
    )
    return {"status": "received", "purchase_id": purchase_id}


@router.post("/whatsapp/send")
async def api_send_whatsapp(request: Request):
    current_user = await get_current_user_flexible(request)
    db = _db(request)
    body = await request.json()
    to = str(body.get("to", "") or "").strip()
    content = str(body.get("message", "") or "").strip()
    if not to:
        raise HTTPException(400, "Recipient is required")
    if not content:
        raise HTTPException(400, "Message is required")

    outbound = get_outbound_router()
    result = await outbound.send_to_channel(
        tenant_id=str(current_user.get("company_id", "") or "").strip(),
        channel_type=ChannelType.WHATSAPP,
        external_user_id=to,
        content=content,
        db=db,
        attachments=body.get("attachments", []) if isinstance(body.get("attachments", []), list) else [],
        metadata={
            "source": "api_send_whatsapp",
            "trace_id": _trace_id_from_context(),
        },
    )
    return {
        "success": bool(result.success),
        "error": str(result.error or ""),
        "to": to,
        "external_message_id": result.external_message_id,
    }
