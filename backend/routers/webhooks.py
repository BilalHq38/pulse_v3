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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
import time
from agent_orchestrator.agents.adaptive_qualification import _update_qualification_silently
from agent_orchestrator.schemas import LeadWorkflowRequest, MessageWorkflowRequest
from channel_layer.normalizer import MessageNormalizer
from channel_layer.router import get_channel_registry, get_outbound_router
from channel_layer.channel_identity import (
    company_default_phone_region,
    normalize_email as normalize_channel_email,
    normalize_whatsapp_phone,
    resolve_outbound_recipient as resolve_channel_outbound_recipient,
)
from channel_layer.schemas import (
    ChannelType,
    MessageDirection,
    UnifiedAttachment,
    UnifiedMessage,
)
from channel_layer.social_lead_detector import detect as detect_social_lead

from services.ai_service.facade import build_sentiment_gate
from services.ai_service.common import estimate_tokens
from services.agent_orchestrator.facade import (
    orchestrate_lead_workflow,
    orchestrate_message_workflow,
)
from services.conversation_engine import TurnRequest, run_turn as engine_run_turn
from services.conversation_engine_webchat import (
    apply_engine_response_to_support_plan,
)
from services.followup_scheduler.webhook_glue import maybe_handle_followup_reply
from core.socket import emit_message_reaction_updated, emit_new_message
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
from shared.provider_queues import provider_retry_queue_label, provider_webhook_queue_label
from shared.webhook_task_runner import create_safe_detached_task
from services.billing_helpers import relaxed_billing_env
from shared.usage_guard import (
    conversation_limit_completed_message,
    conversation_limit_status,
    inbound_conversation_billing_precheck,
    insert_conversation_usage_relaxed,
    insert_conversation_usage_row,
    reserve_conversation_usage,
)
from services.db_helpers import (
    AI_API_EXHAUSTED_MANUAL_MESSAGE,
    auto_disable_ai_after_failure_fallback,
    conversation_ai_auto_paused,
    convert_lead_to_customer_state,
    disable_company_ai_after_api_exhaustion,
    escalate_conversation_to_human,
    fetch_messages_with_attachments,
    get_current_user_flexible,
    is_company_ai_enabled,
    is_ai_api_exhaustion_payload,
    insert_chat_history_record,
    normalize_attachment_row,
    normalize_customer_contact_phone,
    persist_ai_session_record,
    persist_chat_history,
    r,
    record_webhook_event,
    resolve_customer_by_contact,
    runtime_schema_ready,
    save_message_attachments,
    save_message_reaction,
    upsert_customer_social_profile,
    _notify_agents_handoff,
)
from services.lead_stage_service import apply_message_stage_transition, transition_lead_stage
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
_MESSAGES_IDEMPOTENCY_SCHEMA_READY = False
_MESSAGES_IDEMPOTENCY_SCHEMA_LOCK = asyncio.Lock()
_WHATSAPP_IDENTITY_SCHEMA_READY = False
_WHATSAPP_IDENTITY_SCHEMA_LOCK = asyncio.Lock()
_CHANNEL_NORMALIZER = MessageNormalizer()


def _coerce_reaction_action(raw_action: str = "", *, emoji: str = "") -> str:
    action = str(raw_action or "").strip().lower()
    if action in {"remove", "removed", "delete", "deleted", "unreact"}:
        return "removed"
    if action in {"update", "updated", "edit", "edited"}:
        return "updated"
    if not str(emoji or "").strip():
        return "removed"
    return "added"


def _extract_whatsapp_reaction(msg: dict) -> dict:
    message = msg or {}
    if str(message.get("type") or "").strip().lower() != "reaction":
        return {}
    reaction = message.get("reaction") if isinstance(message.get("reaction"), dict) else {}
    emoji = str(reaction.get("emoji") or reaction.get("reaction") or "").strip()
    target_provider_message_id = str(
        reaction.get("message_id")
        or reaction.get("messageId")
        or reaction.get("msg_id")
        or reaction.get("target_message_id")
        or ""
    ).strip()
    return {
        "provider_message_id": str(message.get("id") or reaction.get("id") or "").strip(),
        "target_provider_message_id": target_provider_message_id,
        "emoji": emoji,
        "action": _coerce_reaction_action(str(reaction.get("action") or ""), emoji=emoji),
        "raw_payload": message,
    }


def _is_whatsapp_reaction_message(msg: dict | None) -> bool:
    message = msg or {}
    return str(message.get("type") or "").strip().lower() == "reaction"


def _is_whatsapp_reaction_only_payload(payload: dict | None) -> bool:
    saw_message = False
    for entry in (payload or {}).get("entry", []) or []:
        for change in (entry or {}).get("changes", []) or []:
            value = (change or {}).get("value", {}) or {}
            if value.get("statuses"):
                return False
            messages = value.get("messages", []) or []
            if not messages:
                continue
            saw_message = True
            if not all(_is_whatsapp_reaction_message(msg) for msg in messages):
                return False
    return saw_message


def _extract_messenger_reaction(evt: dict) -> dict:
    event = evt or {}
    reaction = (
        event.get("reaction")
        if isinstance(event.get("reaction"), dict)
        else event.get("message_reaction")
        if isinstance(event.get("message_reaction"), dict)
        else {}
    )
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    if not reaction and str(message.get("type") or "").lower() not in {"reaction", "message_reaction"}:
        return {}
    payload = reaction or message
    emoji = str(payload.get("emoji") or payload.get("reaction") or "").strip()
    target_provider_message_id = str(
        payload.get("message_id")
        or payload.get("mid")
        or payload.get("target_message_id")
        or payload.get("target_mid")
        or ""
    ).strip()
    provider_message_id = str(
        payload.get("id")
        or payload.get("reaction_id")
        or message.get("mid")
        or event.get("timestamp")
        or ""
    ).strip()
    return {
        "provider_message_id": provider_message_id,
        "target_provider_message_id": target_provider_message_id,
        "emoji": emoji,
        "action": _coerce_reaction_action(str(payload.get("action") or payload.get("verb") or ""), emoji=emoji),
        "raw_payload": event,
    }


async def _store_and_emit_message_reaction(
    db,
    *,
    company_id: str,
    channel: str,
    reaction: dict,
    actor_type: str,
    actor_id: str,
) -> dict:
    if not reaction:
        return {}
    saved = await save_message_reaction(
        db,
        company_id=company_id,
        channel=channel,
        provider_message_id=str(reaction.get("provider_message_id") or ""),
        target_provider_message_id=str(reaction.get("target_provider_message_id") or ""),
        actor_type=actor_type,
        actor_id=actor_id,
        emoji=str(reaction.get("emoji") or ""),
        action=str(reaction.get("action") or "added"),
        raw_payload=reaction.get("raw_payload") if isinstance(reaction.get("raw_payload"), dict) else reaction,
    )
    if saved and saved.get("conversation_id"):
        await emit_message_reaction_updated(str(saved.get("conversation_id") or ""), saved)
    return saved


def _db(req):
    return req.app.state.db


_IMAGE_REQUEST_KEYWORDS = frozenset([
    # English – explicit image/photo requests
    "image", "images", "photo", "photos", "picture", "pictures",
    "pic", "pics", "show me", "show it", "show the",
    "what does it look like", "how does it look",
    "see it", "see the", "can i see", "let me see", "i want to see",
    "could i see", "would like to see", "i'd like to see",
    "send me", "send the", "send a photo", "send an image",
    "visual", "look like", "looks like",
    "view it", "view the", "show a photo", "share a photo",
    "share an image", "share the image",
    # Urdu / Roman Urdu
    "tasveer", "photo bhejo", "image bhejo", "dikha", "dikhao",
    "dekha", "dekhao", "tasver", "pic bhejo",
])

# Phrases that appear in AI responses when the engine decided to share an image.
# If the AI says something like "image is on its way", we treat it as an image
# send even if the user message alone didn't contain an image keyword.
_AI_IMAGE_SEND_PHRASES = (
    "image is on its way",
    "image on its way",
    "sharing an image",
    "sending an image",
    "sending the image",
    "image being shared",
    "image will be sent",
    "here is an image",
    "here's an image",
    "here is the image",
    "here's the image",
    "attached the image",
    "product image",
)


def _is_image_request(text: str, ai_response: str = "") -> bool:
    """Return True when the user asked to see images OR the AI response indicates it is sending one."""
    lower = str(text or "").lower()
    if any(kw in lower for kw in _IMAGE_REQUEST_KEYWORDS):
        return True
    if ai_response:
        ai_lower = str(ai_response).lower()
        if any(phrase in ai_lower for phrase in _AI_IMAGE_SEND_PHRASES):
            return True
    return False


def _make_absolute_image_url(image_url: str) -> str:
    """Convert a relative /api/products/media/... path to an absolute URL."""
    from shared.config import backend_public_url
    url = str(image_url or "").strip()
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url
    return f"{backend_public_url()}{url}"


async def _send_product_images(
    db,
    *,
    company_id: str,
    conversation_id: str,
    channel: str,
    recipient_id: str,
    product_links: list[dict],
    user_message: str,
    outbound_metadata: dict,
    ai_response: str = "",
) -> list[str]:
    """Send product images as individual messages before the text response.

    One DB row + one socket emit per image for ALL channels.
    For outbound channels (WhatsApp / Facebook / Instagram / Email) the same
    message ID is reused for the channel-layer send so there is no duplicate
    row in the messages table.

    Returns the list of absolute image URLs (used by web_chat to include them
    in the HTTP response so the ChatWidget can render them).
    Fires when the user asked for images OR the AI response indicates it is sending one.
    """
    if not _is_image_request(user_message, ai_response=ai_response):
        return []

    from services.db_helpers import save_message_attachments

    # If product_links is empty (legacy agent path uses product_images, not product_links),
    # do a quick catalog lookup for the product mentioned in the user message so we can
    # find its image_url and send it.
    effective_links: list[dict] = list(product_links or [])
    if not effective_links:
        try:
            from services.conversation_engine.retrieval.products import ProductRetriever
            _retriever = ProductRetriever()
            _chunks = await _retriever.fetch(
                db, company_id=company_id,
                query=str(user_message or ai_response or ""),
                top_k=2,
            )
            for _chunk in _chunks:
                _meta = _chunk.metadata or {}
                _img = str(_meta.get("image_url") or "")
                _url = str(_meta.get("public_url") or "")
                if _img:
                    effective_links.append({
                        "product_id": _chunk.source_id,
                        "name": _chunk.title or "",
                        "image_url": _img,
                        "url": _url,
                    })
        except Exception as _lookup_exc:
            logger.debug("image_product_lookup_failed error=%s", _lookup_exc)

    sent_urls: list[str] = []
    for pl in effective_links[:3]:
        raw_url = str(pl.get("image_url") or "").strip()
        if not raw_url:
            continue
        # Keep relative /api/... paths as-is: the bridge resolves them against
        # PYTHON_BACKEND (http://gateway:8000) internally, so converting them to
        # http://localhost:8000/... would cause ECONNREFUSED inside Docker.
        # Only convert to absolute if the URL is already an HTTP(S) URL.
        abs_url = raw_url if raw_url.startswith(("/", "http://", "https://")) else _make_absolute_image_url(raw_url)
        product_name = str(pl.get("name") or "Product")
        attachment_name = product_name

        img_msg_id = make_id()
        # For outbound channels set status to 'sending' so the channel layer
        # can update it to 'sent'/'failed'; web_chat is delivered via socket
        # so it's already 'sent' immediately.
        delivery_status = "sending" if channel != "web_chat" else "sent"
        img_caption = f"Here's an image of {product_name}:"
        await db.execute(
            "INSERT INTO messages("
            "id, company_id, conversation_id, content, sender_type, sender_id, "
            "sender_name, ai_confidence, delivery_status, read, created_at"
            ") VALUES($1,$2,$3,$4,'ai','ai-assistant','AI Assistant',1.0,$5,FALSE,NOW())",
            img_msg_id, company_id, conversation_id, img_caption, delivery_status,
        )
        await save_message_attachments(
            db,
            img_msg_id,
            [{"type": "image", "url": abs_url, "name": attachment_name, "mime_type": "image/jpeg"}],
        )
        await db.execute(
            "UPDATE conversations SET last_message='[Product image]',"
            "last_message_at=NOW(),message_count=message_count+1 WHERE id=$1",
            conversation_id,
        )
        img_msg = await _load_message_with_attachments(db, img_msg_id)
        await emit_new_message(conversation_id, img_msg)

        if channel != "web_chat":
            # Ensure actor_user_id is set so the outbound router can resolve the
            # correct per-user bridge scope (bridge sessions are user-scoped, not
            # company-scoped; without this the scope resolves to company-level which
            # may have no active session).
            bridge_actor = str(
                outbound_metadata.get("actor_user_id")
                or outbound_metadata.get("bridge_user_id")
                or outbound_metadata.get("user_id")
                or ""
            ).strip()
            await _send_outbound_response_via_channel_layer(
                db=db,
                company_id=company_id,
                channel=channel,
                recipient_id=recipient_id,
                content="",
                conversation_id=conversation_id,
                attachments=[{"type": "image", "url": abs_url, "name": attachment_name, "mime_type": "image/jpeg"}],
                db_message_id=img_msg_id,
                metadata={
                    **outbound_metadata,
                    "image_send": "product_image",
                    "actor_user_id": bridge_actor,
                },
            )

        sent_urls.append(abs_url)
    return sent_urls


def _format_msgs_as_dialogue(msgs: list[dict], max_pairs: int = 12) -> list[str]:
    """Convert raw message dicts into ["User: X", "Assistant: Y"] dialogue lines
    for the conversation engine's extra_history field."""
    lines: list[str] = []
    for msg in (msgs or []):
        sender = str(msg.get("sender_type") or "")
        text = str(msg.get("content") or "").strip()
        if not text:
            continue
        if sender == "customer":
            lines.append(f"User: {text}")
        elif sender == "ai":
            lines.append(f"Assistant: {text}")
        elif sender == "agent":
            lines.append(f"Agent: {text}")
    # Keep only the last max_pairs*2 lines to avoid bloating the context.
    return lines[-(max_pairs * 2):]


def _float_or_none(value):
    try:
        return float(value)
    except Exception:
        return None


def _elapsed_ms(started_at: float) -> float:
    return round((time.monotonic() - started_at) * 1000, 2)


def _looks_like_phone(value: str) -> bool:
    digits = re.sub(r"\D", "", str(value or "").strip())
    return len(digits) >= 7


def _safe_provider_avatar_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(("/api/", "data:image/")):
        return text[:2000]
    try:
        parsed = urlsplit(text)
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    private_params = {"access_token", "token", "appsecret_proof", "client_secret", "sig", "signature"}
    query = urlencode(
        [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True) if key.lower() not in private_params]
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))[:2000]


_EMPTY_PROFILE_NAMES = {"", "null", "none", "undefined", "unknown", "unknown contact"}


def _clean_display_name(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return "" if text.lower() in _EMPTY_PROFILE_NAMES else text


def _whatsapp_contact_for_message(contacts: list, msg: dict, fallback_index: int = 0) -> dict:
    if not contacts:
        return {}
    raw_sender = str((msg or {}).get("from") or "").strip()
    sender_identity = normalize_whatsapp_phone(raw_sender)
    sender_digits = _identity_digits(sender_identity.canonical_value or raw_sender)

    for contact in contacts or []:
        if not isinstance(contact, dict):
            continue
        wa_id = str(contact.get("wa_id") or "").strip()
        contact_identity = normalize_whatsapp_phone(wa_id)
        contact_digits = _identity_digits(contact_identity.canonical_value or wa_id)
        if wa_id and raw_sender and wa_id == raw_sender:
            return contact
        if sender_digits and contact_digits and sender_digits == contact_digits:
            return contact

    if 0 <= fallback_index < len(contacts) and isinstance(contacts[fallback_index], dict):
        return contacts[fallback_index]
    first = contacts[0]
    return first if isinstance(first, dict) else {}


def _whatsapp_profile_name(contact: dict | None, metadata_payload: dict | None = None, fallback: str = "") -> str:
    payload = dict(metadata_payload or {})
    profile = ((contact or {}).get("profile") or {}) if isinstance(contact, dict) else {}
    name = (
        _clean_display_name((contact or {}).get("name") if isinstance(contact, dict) else "")
        or _clean_display_name(profile.get("name"))
        or _clean_display_name(profile.get("push_name"))
        or _clean_display_name(profile.get("formatted_name"))
        or _clean_display_name(payload.get("sender_name_saved"))
        or _clean_display_name(payload.get("contact_name_saved"))
        or _clean_display_name(payload.get("sender_pushname"))
        or _clean_display_name(payload.get("group_sender_name"))
        or _clean_display_name(payload.get("profile_name"))
        or _clean_display_name(fallback)
    )
    return name


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
        candidate = (
            (
                str(payload.get("group_sender_phone") or payload.get("group_sender_phone_digits") or "").strip()
                if _is_whatsapp_group_metadata(payload)
                else ""
            )
            or str(payload.get("normalized_sender_id") or "").strip()
            or raw_contact
            or str(payload.get("raw_wa_id") or "").strip()
            or metadata_contact
        )
        identity = normalize_whatsapp_phone(candidate)
        if identity.is_valid:
            phone = identity.canonical_value
            channel_id = identity.canonical_value
        else:
            logger.warning(
                "Suspicious WhatsApp contact rejected before customer write raw_sender_id=%s raw_wa_id=%s candidate=%s reason=%s trace_id=%s",
                payload.get("raw_sender_id", ""),
                payload.get("raw_wa_id", ""),
                candidate,
                identity.reason,
                payload.get("trace_id", ""),
            )
            channel_id = str(
                raw_contact
                or metadata_contact
                or payload.get("provider_sender_id")
                or payload.get("raw_sender_id")
                or payload.get("raw_wa_id")
                or ""
            ).strip()
    elif normalized_channel == "email":
        email_identity = normalize_channel_email(raw_contact or metadata_contact)
        email = email_identity.canonical_value
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


def _identity_digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _customer_matches_contact_identity(customer: dict | None, contact_fields: dict[str, str]) -> bool:
    if not customer:
        return False
    email = str(contact_fields.get("email") or "").strip().lower()
    if email and str((customer or {}).get("email") or "").strip().lower() == email:
        return True

    incoming_digits = _identity_digits(contact_fields.get("phone"))
    customer_digits = _identity_digits((customer or {}).get("phone"))
    return bool(incoming_digits and customer_digits and incoming_digits == customer_digits)


def _contact_lookup_values(email: str = "", phone: str = "") -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(value: str, digits: str = "") -> None:
        cleaned = str(value or "").strip()
        normalized_digits = _identity_digits(digits) if digits else ""
        key = (cleaned.lower(), normalized_digits)
        if cleaned and key not in seen:
            seen.add(key)
            values.append((cleaned, normalized_digits))

    if email:
        add(email, "")
    if phone:
        digits = _identity_digits(phone)
        add(phone, digits)
        if digits:
            add(digits, digits)
            add(f"+{digits}", digits)
    return values


def _metadata_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _whatsapp_provider_from_metadata(metadata_payload: dict | None) -> str:
    payload = dict(metadata_payload or {})
    source = str(payload.get("source") or payload.get("bridge_source") or "").strip().lower()
    if source == "whatsapp_web_bridge" or str(payload.get("bridge_scope") or "").strip():
        return "qr"
    return "meta"


def _is_whatsapp_group_metadata(metadata_payload: dict | None) -> bool:
    payload = dict(metadata_payload or {})
    group_id = str(payload.get("group_id") or payload.get("group_chat_id") or "").strip()
    raw_from = str(payload.get("raw_from") or payload.get("msg_from") or payload.get("channel_id") or "").strip()
    return bool(group_id or _metadata_truthy(payload.get("is_group_message")) or raw_from.lower().endswith("@g.us"))


def _whatsapp_group_id(metadata_payload: dict | None) -> str:
    payload = dict(metadata_payload or {})
    for key in ("group_id", "group_chat_id", "channel_id", "msg_id_remote", "raw_from", "msg_from"):
        value = str(payload.get(key) or "").strip()
        if value.lower().endswith("@g.us") or key in {"group_id", "group_chat_id"}:
            if value:
                return value
    return ""


def _preferred_sender_name(channel: str, sender_name: str, metadata_payload: dict | None, existing_name: str = "") -> str:
    payload = dict(metadata_payload or {})
    normalized_channel = str(channel or "").strip().lower()
    incoming = str(sender_name or "").strip()
    existing = str(existing_name or "").strip()
    if normalized_channel != "whatsapp":
        return incoming or existing
    saved = str(
        payload.get("sender_name_saved")
        or payload.get("contact_name_saved")
        or payload.get("name_saved")
        or ""
    ).strip()
    push = str(payload.get("sender_pushname") or payload.get("contact_pushname") or "").strip()
    if saved:
        return saved
    generic_existing = not existing or existing.lower() in {"unknown", "unknown contact"} or existing.startswith("WhatsApp ")
    if existing and not generic_existing and incoming in {push, f"WhatsApp {payload.get('normalized_sender_id', '')}".strip()}:
        return existing
    return incoming or push or existing


def _should_update_sender_name(channel: str, incoming_name: str, metadata_payload: dict | None, existing_name: str) -> bool:
    incoming = str(incoming_name or "").strip()
    existing = str(existing_name or "").strip()
    if not incoming or incoming == existing:
        return False
    if str(channel or "").strip().lower() != "whatsapp":
        return True
    payload = dict(metadata_payload or {})
    saved = str(payload.get("sender_name_saved") or payload.get("contact_name_saved") or "").strip()
    if saved:
        return True
    generic_existing = not existing or existing.lower() in {"unknown", "unknown contact"} or existing.startswith("WhatsApp ")
    return generic_existing


def _format_channel_title(channel: str) -> str:
    return str(channel or "channel").replace("_", " ").strip().title()


def _is_generic_conversation_subject(subject: str, channel: str) -> bool:
    text = re.sub(r"\s+", " ", str(subject or "").strip()).lower()
    if not text:
        return True
    normalized_channel = str(channel or "").strip().lower().replace("_", " ")
    return text in {
        f"new {normalized_channel} conversation",
        f"new {_format_channel_title(channel).lower()} conversation",
        "new conversation",
        "unknown contact",
    }


def _conversation_subject_from_identity(
    channel: str,
    display_name: str,
    metadata_payload: dict | None,
    *,
    is_group_message: bool = False,
) -> str:
    payload = dict(metadata_payload or {})
    if is_group_message:
        group_name = _clean_display_name(payload.get("group_name"))
        if group_name:
            return group_name
    name = (
        _clean_display_name(display_name)
        or _clean_display_name(payload.get("profile_name"))
        or _clean_display_name(payload.get("sender_name_saved"))
        or _clean_display_name(payload.get("contact_name_saved"))
        or _clean_display_name(payload.get("sender_pushname"))
        or _clean_display_name(payload.get("group_sender_name"))
    )
    if name:
        return name
    return f"New {_format_channel_title(channel)} conversation"


async def _get_or_create_whatsapp_group_customer(db, company_id: str, group_id: str, group_name: str = "") -> dict:
    scoped_company_id = str(company_id or "").strip()
    scoped_group_id = str(group_id or "").strip()
    if not scoped_company_id or not scoped_group_id:
        return {}
    existing = r(
        await db.fetchrow(
            "SELECT c.* FROM customers c "
            "JOIN customer_social_profiles csp ON csp.customer_id=c.id "
            "WHERE c.company_id=$1 AND csp.platform='whatsapp_group' AND csp.profile_id=$2 "
            "ORDER BY c.updated_at DESC LIMIT 1",
            scoped_company_id,
            scoped_group_id,
        )
    )
    display_name = str(group_name or "").strip() or "WhatsApp Group"
    if existing:
        if display_name and display_name != existing.get("name"):
            await db.execute(
                "UPDATE customers SET name=$1,updated_at=NOW() WHERE id=$2 AND company_id=$3",
                display_name,
                existing.get("id", ""),
                scoped_company_id,
            )
            existing["name"] = display_name
        return existing

    customer_id = make_id()
    await db.execute(
        "INSERT INTO customers(id,company_id,name,email,phone,segment,avatar,lifecycle_stage,lifetime_value,avg_sentiment,"
        "recent_tickets,complaint_count,days_since_last_contact,total_conversations,created_at,updated_at) "
        "VALUES($1,$2,$3,'','','group','', 'customer',0,0,0,0,0,0,NOW(),NOW())",
        customer_id,
        scoped_company_id,
        display_name,
    )
    await db.execute(
        "INSERT INTO customer_social_profiles(customer_id,platform,profile_id) VALUES($1,'whatsapp_group',$2) "
        "ON CONFLICT (customer_id,platform) DO UPDATE SET profile_id=EXCLUDED.profile_id,updated_at=NOW()",
        customer_id,
        scoped_group_id,
    )
    await db.execute(
        "INSERT INTO customer_channels(customer_id,channel) VALUES($1,'whatsapp') ON CONFLICT DO NOTHING",
        customer_id,
    )
    await db.execute(
        "INSERT INTO customer_tags(customer_id,tag) VALUES($1,'whatsapp-group') ON CONFLICT DO NOTHING",
        customer_id,
    )
    return r(await db.fetchrow("SELECT * FROM customers WHERE id=$1 AND company_id=$2", customer_id, scoped_company_id))


async def _get_or_create_whatsapp_pending_customer(
    db,
    company_id: str,
    raw_identity: str,
    display_name: str = "",
    avatar_url: str = "",
) -> dict:
    scoped_company_id = str(company_id or "").strip()
    scoped_identity = str(raw_identity or "").strip()
    if not scoped_company_id or not scoped_identity:
        return {}
    existing = r(
        await db.fetchrow(
            "SELECT c.* FROM customers c "
            "JOIN customer_social_profiles csp ON csp.customer_id=c.id "
            "WHERE c.company_id=$1 AND csp.platform='whatsapp_pending' AND csp.profile_id=$2 "
            "ORDER BY c.updated_at DESC LIMIT 1",
            scoped_company_id,
            scoped_identity,
        )
    )
    safe_avatar = _safe_provider_avatar_url(avatar_url)
    safe_name = str(display_name or "").strip() or f"WhatsApp pending {scoped_identity[:16]}"
    if existing:
        updates: list[str] = []
        args: list[Any] = []
        if safe_name and _should_update_sender_name("whatsapp", safe_name, {}, existing.get("name", "")):
            updates.append(f"name=${len(args) + 1}")
            args.append(safe_name)
            existing["name"] = safe_name
        if safe_avatar and not existing.get("avatar"):
            updates.append(f"avatar=${len(args) + 1}")
            args.append(safe_avatar)
            existing["avatar"] = safe_avatar
        if updates:
            args.extend([existing.get("id", ""), scoped_company_id])
            await db.execute(
                f"UPDATE customers SET {', '.join(updates)},updated_at=NOW() "
                f"WHERE id=${len(args) - 1} AND company_id=${len(args)}",
                *args,
            )
        return existing

    customer_id = make_id()
    await db.execute(
        "INSERT INTO customers(id,company_id,name,email,phone,segment,avatar,lifecycle_stage,lifetime_value,avg_sentiment,"
        "recent_tickets,complaint_count,days_since_last_contact,total_conversations,created_at,updated_at) "
        "VALUES($1,$2,$3,'','','unknown',$4,'lead',0,0,0,0,0,0,NOW(),NOW())",
        customer_id,
        scoped_company_id,
        safe_name,
        safe_avatar,
    )
    await db.execute(
        "INSERT INTO customer_social_profiles(customer_id,platform,profile_id) VALUES($1,'whatsapp_pending',$2) "
        "ON CONFLICT (customer_id,platform) DO UPDATE SET profile_id=EXCLUDED.profile_id,updated_at=NOW()",
        customer_id,
        scoped_identity,
    )
    await db.execute(
        "INSERT INTO customer_channels(customer_id,channel) VALUES($1,'whatsapp') ON CONFLICT DO NOTHING",
        customer_id,
    )
    await db.execute(
        "INSERT INTO customer_tags(customer_id,tag) VALUES($1,'whatsapp-pending') ON CONFLICT DO NOTHING",
        customer_id,
    )
    return r(await db.fetchrow("SELECT * FROM customers WHERE id=$1 AND company_id=$2", customer_id, scoped_company_id))


def _whatsapp_account_id(metadata_payload: dict | None) -> str:
    payload = dict(metadata_payload or {})
    return str(
        payload.get("business_account_id")
        or payload.get("phone_number_id")
        or payload.get("bridge_scope")
        or ""
    ).strip()


def _normalize_whatsapp_identity_value(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    lower = raw.lower()
    if "@" not in lower:
        digits = _identity_digits(raw)
        return digits or lower
    if lower.endswith("@c.us") or lower.endswith("@s.whatsapp.net"):
        user = lower.split("@", 1)[0]
        digits = _identity_digits(user)
        return f"{digits}@s.whatsapp.net" if digits else lower
    return lower


def _whatsapp_identity_type(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if raw.endswith("@lid") or "@lid:" in raw:
        return "lid_jid"
    if raw.endswith("@g.us"):
        return "group_jid"
    if raw.endswith("@c.us") or raw.endswith("@s.whatsapp.net"):
        return "jid"
    if _identity_digits(raw) == raw.replace("+", "") and len(_identity_digits(raw)) >= 7:
        return "phone"
    if "@" in raw:
        return "provider_jid"
    return "raw"


def _append_whatsapp_alias_candidate(candidates: list[dict], seen: set[tuple[str, str]], value: Any, source: str) -> None:
    raw = str(value or "").strip()
    if not raw:
        return
    normalized = _normalize_whatsapp_identity_value(raw)
    identity_type = _whatsapp_identity_type(raw)
    if not normalized or not identity_type:
        return
    key = (identity_type, normalized)
    if key in seen:
        return
    seen.add(key)
    candidates.append(
        {
            "identity_type": identity_type,
            "identity_value": raw,
            "identity_value_normalized": normalized,
            "source": source,
        }
    )
    digits = _identity_digits(raw)
    if digits and identity_type in {"jid", "phone"}:
        phone_key = ("phone", digits)
        if phone_key not in seen:
            seen.add(phone_key)
            candidates.append(
                {
                    "identity_type": "phone",
                    "identity_value": digits,
                    "identity_value_normalized": digits,
                    "source": f"{source}:digits",
                }
            )


def _whatsapp_alias_candidates(
    metadata_payload: dict | None,
    *,
    sender_contact: str = "",
    channel_binding: str = "",
) -> list[dict]:
    payload = dict(metadata_payload or {})
    candidates: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for key in (
        "normalized_sender_id",
        "sender_contact",
        "sender_phone",
        "sender_phone_digits",
        "target_phone",
        "target_phone_digits",
        "target_raw_id",
        "target_lid_jid",
        "raw_from",
        "msg_from",
        "msg_to",
        "msg_author",
        "msg_id_remote",
        "raw_sender_id",
        "provider_sender_id",
        "raw_wa_id",
        "contact_id",
        "chat_id",
        "group_id",
    ):
        _append_whatsapp_alias_candidate(candidates, seen, payload.get(key), key)
    _append_whatsapp_alias_candidate(candidates, seen, sender_contact, "sender_contact_arg")
    _append_whatsapp_alias_candidate(candidates, seen, channel_binding, "channel_binding")
    return candidates


def _is_whatsapp_web_bridge_payload(payload: dict) -> bool:
    for entry in payload.get("entry", []) or []:
        for change in (entry or {}).get("changes", []) or []:
            value = (change or {}).get("value", {}) or {}
            metadata = value.get("metadata", {}) or {}
            if str(metadata.get("source") or "").strip() == "whatsapp_web_bridge":
                return True
            for msg in value.get("messages", []) or []:
                if isinstance((msg or {}).get("web_bridge"), dict):
                    return True
    return False


def _is_whatsapp_bridge_outbound_message(msg: dict, metadata: dict | None = None) -> bool:
    web_bridge = (msg or {}).get("web_bridge") if isinstance((msg or {}).get("web_bridge"), dict) else {}
    direction = str(web_bridge.get("direction") or (metadata or {}).get("direction") or "").strip().lower()
    return direction == "outbound" or bool(web_bridge.get("from_me") is True)


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
    if not bridge_trusted:
        await _enforce_meta_webhook_rate_limit(request, channel)
    else:
        logger.debug("Skipping external Meta webhook rate limit for trusted WhatsApp bridge request")
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
        allow_unsigned = _is_truthy(os.environ.get("ALLOW_UNSIGNED_WEB_CHAT_WIDGET", "false"))
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
        await runtime_schema_ready(
            db,
            "unprocessed_events",
            required_relations=("unprocessed_events",),
            required_indexes=("uq_unprocessed_events_channel_event", "idx_unprocessed_events_status_retry"),
            raise_on_missing=True,
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
    provider: str = "",
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
            _schedule_unprocessed_retry(db, channel, provider=provider, delay_seconds=delay_seconds)
        return

    for row in rows:
        row_data = dict(row or {})
        event_id = str(row_data.get("event_id") or "").strip()
        retry_count = int(row_data.get("retry_count") or 0)
        raw_payload = str(row_data.get("raw_payload") or "").strip()
        payload = _decode_webhook_json(raw_payload.encode("utf-8"))
        if channel == "whatsapp" and _is_whatsapp_reaction_only_payload(payload):
            logger.info(
                "Dropping queued WhatsApp reaction event without retry event_id=%s",
                event_id,
            )
            await db.execute(
                "UPDATE unprocessed_events SET status='resolved', last_error='whatsapp_reactions_disabled', "
                "resolved_at=NOW(), updated_at=NOW() WHERE channel=$1 AND event_id=$2",
                channel,
                event_id,
            )
            continue
        try:
            if channel == "whatsapp":
                retry_is_bridge = provider == "qr" or _is_whatsapp_web_bridge_payload(payload)
                resolved = await _handle_whatsapp_webhook_payload(
                    db,
                    payload,
                    event_id=event_id,
                    store_unresolved=False,
                    allow_direct_company_id=retry_is_bridge,
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
        _schedule_unprocessed_retry(db, channel, provider=provider, delay_seconds=delay_seconds + 1)


async def _retry_unprocessed_events_after_delay(
    db,
    *,
    channel: str,
    provider: str = "",
    delay_seconds: float,
) -> None:
    delay = max(0.0, float(delay_seconds or 0.0))
    if delay > 0:
        await asyncio.sleep(delay)
    await _retry_unprocessed_events(db, channel=channel, provider=provider)


async def _enqueue_delayed_unprocessed_retry(
    db,
    *,
    channel: str,
    provider: str = "",
    delay_seconds: float,
) -> None:
    delay = max(0.0, float(delay_seconds or 0.0))
    if delay <= 0:
        await _retry_unprocessed_events(db, channel=channel, provider=provider)
        return

    queue = get_background_queue(service_label=provider_retry_queue_label(provider or channel))
    if queue is None or not queue.enabled:
        await _retry_unprocessed_events_after_delay(
            db,
            channel=channel,
            provider=provider,
            delay_seconds=delay,
        )
        return

    retry_job_name = f"retry-unprocessed-{channel}"
    retry_job_id = f"{retry_job_name}:{make_id()}"
    coro = _retry_unprocessed_events(db, channel=channel, provider=provider)
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
            provider=provider,
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
            provider=provider,
            delay_seconds=delay,
        )
    finally:
        try:
            coro.close()
        except Exception:
            pass


def _schedule_unprocessed_retry(db, channel: str, *, provider: str = "", delay_seconds: float = 0.0) -> None:
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
            provider=provider,
            delay_seconds=delay_seconds,
        ),
        name=retry_job_name,
        job_id=retry_job_id,
        timeout_seconds=timeout_seconds,
        service_label=provider_retry_queue_label(provider or channel),
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
        avatar_url = _safe_provider_avatar_url(str((profile or {}).get("profile_picture") or "").strip())
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
            if avatar_url:
                await db.execute(
                    "UPDATE conversations SET customer_avatar=$1,updated_at=NOW() WHERE company_id=$2 AND customer_id=$3",
                    avatar_url,
                    scoped_company_id,
                    cid,
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
    return_results: bool = False,
):
    resolved_company_id = ""
    dedup_cache = get_cache_client(namespace="inbound_dedup")
    processed_results: list[dict] = []
    handler_started_at = time.monotonic()
    try:
        if _is_whatsapp_reaction_only_payload(payload):
            logger.info(
                "Ignoring WhatsApp reaction-only webhook event_id=%s elapsed_ms=%s",
                event_id,
                _elapsed_ms(handler_started_at),
            )
            return (
                {
                    "processed": True,
                    "status": "ignored_reaction",
                    "results": [
                        {
                            "message_id": str((msg or {}).get("id") or ""),
                            "conversation_id": "",
                            "ignored": True,
                            "reason": "whatsapp_reactions_disabled",
                        }
                        for entry in payload.get("entry", []) or []
                        for change in (entry or {}).get("changes", []) or []
                        for msg in (((change or {}).get("value", {}) or {}).get("messages", []) or [])
                    ],
                }
                if return_results
                else True
            )
        registry = get_channel_registry()
        adapter = registry.get_or_none(ChannelType.WHATSAPP)
        if adapter is None:
            raise RuntimeError("WhatsApp adapter is not registered")

        processed_any = False
        processed_customer_message = False
        tenant_resolved_any = False
        skipped_reasons: list[str] = []
        unresolved_metadata: dict = {}
        for entry in payload.get("entry", []) or []:
            for change in (entry or {}).get("changes", []) or []:
                value = (change or {}).get("value", {}) or {}
                value_metadata = value.get("metadata", {}) or {}
                source = str(value_metadata.get("source") or "").strip()
                is_web_bridge = source == "whatsapp_web_bridge"
                messages = value.get("messages", []) or []
                contacts = value.get("contacts", []) or []
                raw_statuses = value.get("statuses", []) or []
                inbound_metadata = {
                    "phone_number_id": value_metadata.get("phone_number_id", ""),
                    "recipient_phone_number": value_metadata.get("display_phone_number", ""),
                    "business_account_id": (value.get("business_account_id", "") or (entry or {}).get("id", "")),
                    "company_id": value_metadata.get("company_id", ""),
                    "source": source,
                    "bridge_scope": value_metadata.get("bridge_scope", ""),
                    "bridge_user_id": value_metadata.get("bridge_user_id", ""),
                    "profile_picture_url": value_metadata.get("profile_picture_url", ""),
                }
                unresolved_metadata = {k: v for k, v in inbound_metadata.items() if v}
                if messages and not raw_statuses and all(_is_whatsapp_reaction_message(msg) for msg in messages):
                    logger.info(
                        "Ignoring WhatsApp reaction-only webhook before tenant resolution event_id=%s reaction_count=%s source=%s",
                        event_id,
                        len(messages),
                        source or "meta_cloud",
                    )
                    processed_results.extend(
                        {
                            "message_id": str((msg or {}).get("id") or ""),
                            "conversation_id": "",
                            "ignored": True,
                            "reason": "whatsapp_reactions_disabled",
                        }
                        for msg in messages
                    )
                    processed_any = True
                    continue
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
                        _schedule_unprocessed_retry(
                            db,
                            "whatsapp",
                            provider="qr" if is_web_bridge else "meta",
                        )
                    continue
                tenant_resolved_any = True
                await _ensure_whatsapp_identity_schema(db)

                create_safe_detached_task(
                    db,
                    _record_webhook_event_safe(
                        db,
                        "whatsapp",
                        payload,
                        metadata=inbound_metadata,
                        resolved_company_id=resolved_company_id,
                        allow_direct_company_id=False,
                    ),
                    name="webhook-whatsapp-audit",
                    company_id=resolved_company_id,
                    channel="whatsapp",
                    event_id=event_id,
                    service_label=provider_webhook_queue_label("qr" if is_web_bridge else "meta"),
                )

                if raw_statuses:
                    create_safe_detached_task(
                        db,
                        process_delivery_status_webhook(
                            db,
                            company_id=resolved_company_id,
                            channel="whatsapp",
                            statuses=raw_statuses,
                        ),
                        name="webhook-whatsapp-status",
                        company_id=resolved_company_id,
                        channel="whatsapp",
                        event_id=event_id,
                        service_label=provider_webhook_queue_label("qr" if is_web_bridge else "meta"),
                    )
                    processed_any = True
                    if not messages:
                        continue

                for i, msg in enumerate(messages):
                    if _is_whatsapp_reaction_message(msg):
                        provider_reaction_id = str((msg or {}).get("id") or "").strip()
                        logger.info(
                            "Ignoring WhatsApp reaction event company_id=%s event_id=%s provider_event_id=%s source=%s",
                            resolved_company_id,
                            event_id,
                            provider_reaction_id,
                            source or "meta_cloud",
                        )
                        processed_results.append(
                            {
                                "message_id": provider_reaction_id,
                                "conversation_id": "",
                                "ignored": True,
                                "reason": "whatsapp_reactions_disabled",
                            }
                        )
                        processed_any = True
                        continue
                    contact = _whatsapp_contact_for_message(contacts, msg or {}, i)
                    web_bridge = (msg or {}).get("web_bridge") if isinstance((msg or {}).get("web_bridge"), dict) else {}
                    is_bridge_outbound = is_web_bridge and _is_whatsapp_bridge_outbound_message(msg or {}, value_metadata)
                    is_group_message = bool(
                        web_bridge.get("is_group_message")
                        or value_metadata.get("is_group_message")
                        or str((msg or {}).get("from") or "").strip().lower().endswith("@g.us")
                    )
                    raw_sender_phone = str((msg or {}).get("from") or "").strip()
                    raw_wa_id = str((contact or {}).get("wa_id") or "").strip()
                    bridge_sender_phone = str(
                        web_bridge.get("target_phone")
                        or web_bridge.get("target_phone_digits")
                        or web_bridge.get("target_raw_id")
                        or web_bridge.get("target_lid_jid")
                        or web_bridge.get("sender_phone")
                        or web_bridge.get("sender_phone_digits")
                        or ""
                    ).strip()
                    if is_bridge_outbound:
                        sender_phone = bridge_sender_phone or raw_sender_phone or raw_wa_id or str(web_bridge.get("group_id") or "").strip()
                    else:
                        sender_phone = raw_sender_phone or raw_wa_id or bridge_sender_phone
                    if not sender_phone and not is_group_message:
                        logger.warning(
                            "Skipping WhatsApp inbound without sender identity company_id=%s event_id=%s trace_id=%s",
                            resolved_company_id,
                            event_id,
                            _trace_id_from_context(),
                        )
                        skipped_reasons.append("invalid_sender_identity")
                        continue
                    provider_event_id = str(
                        (msg or {}).get("provider_event_id")
                        or (msg or {}).get("id")
                        or ""
                    ).strip()
                    idempotency_key = str(
                        (msg or {}).get("idempotency_key")
                        or web_bridge.get("idempotency_key")
                        or (
                            f"whatsapp:{resolved_company_id}:message:{provider_event_id}"
                            if provider_event_id
                            else ""
                        )
                    ).strip()
                    dedup_result = await _record_whatsapp_event_dedup(
                        db,
                        resolved_company_id,
                        {**inbound_metadata, **value_metadata, **web_bridge},
                        event_type=str(value_metadata.get("bridge_event_type") or "message"),
                        provider_event_id=provider_event_id,
                        idempotency_key=idempotency_key,
                        payload=msg or {},
                    )
                    if dedup_result.get("duplicate"):
                        processed_results.append(
                            {
                                "message_id": provider_event_id,
                                "conversation_id": "",
                                "duplicate": True,
                                "dedup_stage": "database",
                            }
                        )
                        processed_any = True
                        continue
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
                            "source": source or "whatsapp_webhook",
                            "bridge_source": source,
                            "direction": "outbound" if is_bridge_outbound else "inbound",
                            "provider_sender_id": str(web_bridge.get("provider_sender_id") or ""),
                            "profile_picture_url": str(
                                (unified_message.metadata or {}).get("profile_picture_url")
                                or web_bridge.get("profile_picture_url")
                                or value_metadata.get("profile_picture_url")
                                or ""
                            ),
                            "group_profile_picture_url": str(
                                web_bridge.get("group_profile_picture_url")
                                or value_metadata.get("group_profile_picture_url")
                                or ""
                            ),
                            "raw_from": str(web_bridge.get("raw_from") or raw_sender_phone or ""),
                            "msg_from": str(web_bridge.get("msg_from") or raw_sender_phone or ""),
                            "msg_to": str(web_bridge.get("msg_to") or ""),
                            "msg_author": str(web_bridge.get("msg_author") or ""),
                            "msg_id_remote": str(web_bridge.get("msg_id_remote") or ""),
                            "target_raw_id": str(web_bridge.get("target_raw_id") or ""),
                            "target_lid_jid": str(web_bridge.get("target_lid_jid") or ""),
                            "target_phone": str(web_bridge.get("target_phone") or ""),
                            "target_phone_digits": str(web_bridge.get("target_phone_digits") or ""),
                            "identity_unresolved": bool(web_bridge.get("identity_unresolved")),
                            "pending_identity": bool(web_bridge.get("pending_identity")),
                            "is_group_message": is_group_message,
                            "suppress_ai": _metadata_truthy(web_bridge.get("suppress_ai"))
                            or _metadata_truthy(value_metadata.get("suppress_ai")),
                            "group_id": str(web_bridge.get("group_id") or value_metadata.get("group_id") or ""),
                            "group_name": str(web_bridge.get("group_name") or value_metadata.get("group_name") or ""),
                            "group_sender_phone": str(
                                web_bridge.get("group_sender_phone")
                                or web_bridge.get("group_sender_phone_digits")
                                or ""
                            ),
                            "group_sender_name": str(web_bridge.get("group_sender_name") or ""),
                            "sender_name_saved": str(web_bridge.get("sender_name_saved") or ""),
                            "sender_pushname": str(web_bridge.get("sender_pushname") or ""),
                            "message_type": str((msg or {}).get("type") or "text"),
                            "event_id": event_id,
                            "provider_event_id": provider_event_id,
                            "idempotency_key": idempotency_key,
                            "trace_id": unified_message.trace_id,
                            "external_message_id": str((msg or {}).get("id") or unified_message.message_id or ""),
                            "outbound_external_message_id": (
                                str((msg or {}).get("id") or unified_message.message_id or "")
                                if is_bridge_outbound
                                else ""
                            ),
                        }
                    )
                    if is_bridge_outbound:
                        unified_message.direction = MessageDirection.OUTBOUND
                    if is_bridge_outbound and not unified_message.external_user_id:
                        unified_message.external_user_id = (
                            str(unified_message.metadata.get("target_phone") or "").strip()
                            or str(unified_message.metadata.get("target_phone_digits") or "").strip()
                            or str(unified_message.metadata.get("target_raw_id") or "").strip()
                            or str(unified_message.metadata.get("target_lid_jid") or "").strip()
                            or sender_phone
                        )
                    if not unified_message.external_user_id and is_group_message:
                        unified_message.external_user_id = (
                            str(unified_message.metadata.get("group_sender_phone") or "").strip()
                            or str(unified_message.metadata.get("group_id") or "").strip()
                            or sender_phone
                        )
                    if not unified_message.external_user_id:
                        logger.warning(
                            "Skipping WhatsApp inbound with invalid sender identity company_id=%s user_id=%s source=%s event_id=%s raw_from=%s msg_from=%s msg_author=%s msg_id_remote=%s raw_sender_id=%s raw_wa_id=%s provider_sender_id=%s reason=%s trace_id=%s",
                            resolved_company_id,
                            inbound_metadata.get("bridge_user_id", ""),
                            source or "meta_cloud",
                            event_id,
                            web_bridge.get("raw_from", ""),
                            web_bridge.get("msg_from", ""),
                            web_bridge.get("msg_author", ""),
                            web_bridge.get("msg_id_remote", ""),
                            raw_sender_phone,
                            raw_wa_id,
                            web_bridge.get("provider_sender_id", ""),
                            (unified_message.metadata or {}).get("identity_reason", ""),
                            unified_message.trace_id,
                        )
                        continue
                    logger.info(
                        "WhatsApp inbound identity resolved company_id=%s user_id=%s source=%s raw_from=%s msg_from=%s msg_author=%s msg_id_remote=%s raw_sender_id=%s raw_wa_id=%s provider_sender_id=%s normalized_sender=%s trace_id=%s",
                        resolved_company_id,
                        inbound_metadata.get("bridge_user_id", ""),
                        source or "meta_cloud",
                        web_bridge.get("raw_from", ""),
                        web_bridge.get("msg_from", ""),
                        web_bridge.get("msg_author", ""),
                        web_bridge.get("msg_id_remote", ""),
                        (unified_message.metadata or {}).get("raw_sender_id", raw_sender_phone),
                        (unified_message.metadata or {}).get("raw_wa_id", raw_wa_id),
                        (unified_message.metadata or {}).get("provider_sender_id", web_bridge.get("provider_sender_id", "")),
                        unified_message.external_user_id,
                        unified_message.trace_id,
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
                                processed_results.append(
                                    {
                                        "message_id": dedup_message_id,
                                        "conversation_id": "",
                                        "duplicate": True,
                                        "dedup_stage": "cache",
                                    }
                                )
                                processed_any = True
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
                        _whatsapp_profile_name(
                            contact,
                            {
                                **(unified_message.metadata or {}),
                                **web_bridge,
                                "group_sender_name": web_bridge.get("group_sender_name") or "",
                            },
                        )
                        or _clean_display_name(web_bridge.get("group_sender_name"))
                        or f"WhatsApp {unified_message.external_user_id}"
                    )
                    if not (str(unified_message.content or "").strip() or unified_message.attachments):
                        continue

                    if is_bridge_outbound:
                        processed = await _process_unified_outbound_bridge_message(
                            db,
                            unified_message,
                            sender_name=sender_name,
                            sender_contact=unified_message.external_user_id,
                        )
                    else:
                        processed = await _process_unified_incoming_message(
                            db,
                            unified_message,
                            sender_name=sender_name,
                            sender_contact=unified_message.external_user_id,
                        )
                    if processed:
                        processed_results.append(processed)
                        processed_any = True
                        if not is_bridge_outbound:
                            processed_customer_message = True

        if not processed_any:
            status = "skipped"
            if skipped_reasons:
                status = ",".join(sorted(set(skipped_reasons)))
            if store_unresolved and event_id and not tenant_resolved_any:
                await _store_unprocessed_event(
                    db,
                    channel="whatsapp",
                    event_id=event_id,
                    payload=payload,
                    metadata=unresolved_metadata,
                    reason="tenant_unresolved",
                )
                _schedule_unprocessed_retry(
                    db,
                    "whatsapp",
                    provider=_whatsapp_provider_from_metadata(unresolved_metadata),
                )
            return {"processed": False, "status": status, "results": processed_results} if return_results else False

        if event_id and resolved_company_id:
            await _mark_unprocessed_event_resolved(
                db,
                channel="whatsapp",
                event_id=event_id,
                company_id=resolved_company_id,
            )
        logger.info(
            "WhatsApp webhook handled event_id=%s processed_customer_message=%s result_count=%s elapsed_ms=%s",
            event_id,
            processed_customer_message,
            len(processed_results),
            _elapsed_ms(handler_started_at),
        )
        return {"processed": True, "status": "processed", "results": processed_results} if return_results else True
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
        return {"processed": False, "status": "error", "error": str(exc), "results": processed_results} if return_results else False


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
                    _schedule_unprocessed_retry(db, "facebook", provider="meta")
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
                reaction_payload = _extract_messenger_reaction(evt or {})
                if reaction_payload:
                    await _store_and_emit_message_reaction(
                        db,
                        company_id=company_id,
                        channel="facebook",
                        reaction=reaction_payload,
                        actor_type="customer",
                        actor_id=sid,
                    )
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
        _schedule_unprocessed_retry(db, "facebook", provider="meta")
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
                    _schedule_unprocessed_retry(db, "instagram", provider="meta")
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
                reaction_payload = _extract_messenger_reaction(evt or {})
                if reaction_payload:
                    await _store_and_emit_message_reaction(
                        db,
                        company_id=company_id,
                        channel="instagram",
                        reaction=reaction_payload,
                        actor_type="customer",
                        actor_id=sid,
                    )
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
        _schedule_unprocessed_retry(db, "instagram", provider="meta")
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
                    _schedule_unprocessed_retry(db, "lead_form", provider="meta")
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
        _schedule_unprocessed_retry(db, "lead_form", provider="meta")
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
            try:
                await transition_lead_stage(
                    db,
                    lead,
                    "won",
                    reason="Successful external purchase recorded",
                    source="payment",
                    confidence=1.0,
                    event_id=f"external_purchase:{external_purchase_id}",
                    automatic=True,
                )
            except Exception as exc:
                logger.warning(
                    "external purchase lead stage transition failed lead_id=%s purchase_id=%s: %s",
                    lead.get("id", ""),
                    external_purchase_id,
                    exc,
                )
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
    has_video = any(str(item.get("type") or item.get("file_type") or "").lower() == "video" for item in files)
    if has_video:
        return "Received video" if sender_type == "customer" else "Sent video"
    return "Received attachment" if sender_type == "customer" else "Sent attachment"


def _coerce_provider_message_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        ts = int(value)
        if ts > 10_000_000_000:
            ts = ts // 1000
        if ts > 0:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
    text = str(value or "").strip()
    if text:
        if text.isdigit():
            return _coerce_provider_message_timestamp(int(text))
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


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
    msg["attachments"] = [normalize_attachment_row(dict(row)) for row in rows]
    return msg


async def _persist_whatsapp_inbound_context_background(
    db,
    *,
    company_id: str,
    customer_id: str,
    conversation_id: str,
    message_id: str,
    metadata_payload: dict,
    conversation_channel_binding: str,
    sender_contact: str,
    display_name: str,
    profile_picture_url: str,
    is_group_message: bool,
) -> None:
    try:
        identity_id = await _upsert_whatsapp_identity_aliases(
            db,
            company_id,
            metadata_payload,
            customer_id=customer_id,
            conversation_id=conversation_id,
            channel_binding=conversation_channel_binding,
            sender_contact=sender_contact,
            status="resolved",
            display_name=display_name,
            profile_picture_url=profile_picture_url,
        )
        await _persist_whatsapp_message_context(
            db,
            company_id=company_id,
            message_id=message_id,
            conversation_id=conversation_id,
            metadata_payload=metadata_payload,
            direction="inbound",
            source=str(metadata_payload.get("source") or "whatsapp_webhook"),
            identity_id=identity_id,
        )
        if is_group_message:
            await _upsert_whatsapp_group_participant(
                db,
                company_id=company_id,
                metadata_payload=metadata_payload,
                participant_customer_id=customer_id,
            )
    except Exception as exc:
        logger.warning(
            "WhatsApp inbound context background persist failed company_id=%s conversation_id=%s message_id=%s error=%s",
            company_id,
            conversation_id,
            message_id,
            exc,
        )


async def _capture_raw_message_background(
    db,
    *,
    conversation: dict,
    message: dict,
    source: str,
    metadata: dict,
) -> None:
    try:
        from data_pipeline.ingestion.raw_store import capture_raw_message

        await capture_raw_message(
            db,
            conversation=conversation,
            message=message,
            source=source,
            metadata=metadata,
        )
    except Exception as exc:
        logger.warning(
            "raw message capture failed company_id=%s conversation_id=%s message_id=%s error=%s",
            metadata.get("company_id", ""),
            message.get("conversation_id", ""),
            message.get("id", ""),
            exc,
        )


async def _apply_inbound_stage_transition_background(
    db,
    *,
    company_id: str,
    lead_id: str,
    customer_id: str,
    message_text: str,
    event_id: str,
) -> None:
    try:
        await apply_message_stage_transition(
            db,
            company_id=company_id,
            lead_id=lead_id,
            customer_id=customer_id,
            message_text=message_text,
            direction="inbound",
            source="customer_reply",
            event_id=event_id,
        )
    except Exception as exc:
        logger.warning(
            "inbound lead stage transition failed company_id=%s lead_id=%s event_id=%s: %s",
            company_id,
            lead_id,
            event_id,
            exc,
        )


async def _persist_workflow_annotations_background(
    db,
    *,
    company_id: str,
    conversation_id: str,
    message_id: str,
    sentiment_score,
    sentiment_emotion: str,
    sentiment_confidence,
    intent_type: str,
    conversation_score,
    conversation_label: str,
    sentiment_gate: dict,
) -> None:
    try:
        await db.execute(
            "UPDATE messages SET sentiment_score=$1,sentiment_emotion=$2,sentiment_confidence=$3,intent_type=$4 WHERE id=$5",
            sentiment_score,
            sentiment_emotion,
            sentiment_confidence,
            intent_type,
            message_id,
        )
        await db.execute(
            "UPDATE conversations SET sentiment_score=$1,sentiment_label=$2 WHERE id=$3",
            conversation_score,
            conversation_label,
            conversation_id,
        )
        if not sentiment_gate.get("ai_response_allowed", True):
            for tag in ["toxic", "escalating"]:
                await db.execute(
                    "INSERT INTO conversation_tags(conversation_id,tag) VALUES($1,$2) ON CONFLICT DO NOTHING",
                    conversation_id,
                    tag,
                )
            if dict(sentiment_gate.get("risk_flags") or {}).get("possible_hate_speech"):
                await db.execute(
                    "INSERT INTO conversation_tags(conversation_id,tag) VALUES($1,'possible-hate-speech') ON CONFLICT DO NOTHING",
                    conversation_id,
                )
    except Exception as exc:
        logger.warning(
            "workflow annotation persist failed company_id=%s conversation_id=%s message_id=%s error=%s",
            company_id,
            conversation_id,
            message_id,
            exc,
        )


async def _persist_ai_chat_history_background(
    db,
    *,
    conversation: dict,
    message: dict,
) -> None:
    try:
        await persist_chat_history(db, conversation, message)
    except Exception as exc:
        logger.warning(
            "AI chat history persist failed company_id=%s conversation_id=%s message_id=%s error=%s",
            message.get("company_id", ""),
            message.get("conversation_id", ""),
            message.get("id", ""),
            exc,
        )


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


def _outbound_failure_code(error: str) -> str:
    text = str(error or "").strip()
    lowered = text.lower()
    if "whatsapp_session_not_ready" in lowered or "session is not ready" in lowered:
        return "WHATSAPP_SESSION_NOT_READY"
    if "session is not connected" in lowered or "scan the qr" in lowered or "please scan" in lowered:
        return "WHATSAPP_SESSION_NOT_READY"
    if "still starting" in lowered or "initializing" in lowered or "authenticating" in lowered:
        return "WHATSAPP_SESSION_NOT_READY"
    if "invalid whatsapp phone" in lowered or "invalid phone" in lowered:
        return "INVALID_WHATSAPP_PHONE"
    if "missing outbound recipient" in lowered:
        return "MISSING_OUTBOUND_RECIPIENT"
    return "OUTBOUND_DELIVERY_FAILED"


def _is_non_retryable_outbound_error(error: str) -> bool:
    return _outbound_failure_code(error) in {
        "WHATSAPP_SESSION_NOT_READY",
        "INVALID_WHATSAPP_PHONE",
        "MISSING_OUTBOUND_RECIPIENT",
    }


def _build_ai_response_idempotency_key(
    *,
    company_id: str,
    conversation_id: str,
    inbound_message_id: str,
    inbound_external_message_id: str,
    channel: str,
    workflow_type: str = "auto_response",
) -> str:
    material = str(inbound_external_message_id or inbound_message_id or "").strip()
    if not material:
        material = hashlib.sha256(
            f"{company_id}:{conversation_id}:{channel}:{workflow_type}:{now_ts().timestamp()}".encode("utf-8")
        ).hexdigest()
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"ai:{workflow_type}:{company_id}:{conversation_id}:{channel}:{digest}"


def _is_unique_constraint_violation(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return (
        str(getattr(exc, "sqlstate", "") or "") == "23505"
        or exc.__class__.__name__ == "UniqueViolationError"
        or (exc.__class__.__name__ == "IntegrityError" and ("duplicate key" in text or "unique constraint" in text))
        or "duplicate key" in text
        or "unique constraint" in text
    )


def _is_whatsapp_ai_auto_response(metadata: dict | None) -> bool:
    key = str((metadata or {}).get("idempotency_key") or "").strip()
    return key.startswith("ai:auto_response:")


def _bridge_scope_label(company_id: str, user_id: str = "") -> str:
    scoped_company = str(company_id or "").strip()
    scoped_user = str(user_id or "").strip()
    if scoped_user:
        return f"user-{scoped_company or 'global'}-{scoped_user}"
    if scoped_company:
        return f"company-{scoped_company}"
    return "default"


async def _ensure_messages_idempotency_schema(db) -> None:
    global _MESSAGES_IDEMPOTENCY_SCHEMA_READY
    if _MESSAGES_IDEMPOTENCY_SCHEMA_READY or not db:
        return
    async with _MESSAGES_IDEMPOTENCY_SCHEMA_LOCK:
        if _MESSAGES_IDEMPOTENCY_SCHEMA_READY:
            return
        await runtime_schema_ready(
            db,
            "messages_idempotency",
            required_columns=(("messages", "idempotency_key"),),
            required_indexes=("uq_messages_company_idempotency_key_nonempty",),
            raise_on_missing=True,
        )
        _MESSAGES_IDEMPOTENCY_SCHEMA_READY = True


async def _ensure_whatsapp_identity_schema(db) -> None:
    global _WHATSAPP_IDENTITY_SCHEMA_READY
    if _WHATSAPP_IDENTITY_SCHEMA_READY or not db:
        return
    async with _WHATSAPP_IDENTITY_SCHEMA_LOCK:
        if _WHATSAPP_IDENTITY_SCHEMA_READY:
            return
        await runtime_schema_ready(
            db,
            "whatsapp_identity",
            required_relations=(
                "whatsapp_identity_mappings",
                "whatsapp_event_dedup",
                "whatsapp_pending_messages",
                "whatsapp_group_participants",
            ),
            required_columns=(
                ("conversations", "is_group"),
                ("conversations", "group_id"),
                ("conversations", "whatsapp_account_id"),
                ("conversations", "identity_key"),
                ("messages", "provider_event_id"),
                ("messages", "message_direction"),
                ("messages", "source"),
                ("messages", "whatsapp_identity_id"),
                ("messages", "whatsapp_group_id"),
                ("messages", "whatsapp_group_name"),
                ("messages", "whatsapp_participant_id"),
                ("messages", "whatsapp_participant_name"),
                ("messages", "raw_metadata"),
            ),
            required_indexes=(
                "uq_messages_company_provider_event_nonempty",
                "uq_whatsapp_identity_alias",
                "idx_whatsapp_identity_customer",
                "idx_whatsapp_identity_conversation",
                "idx_whatsapp_identity_lid",
                "idx_whatsapp_identity_phone",
                "uq_whatsapp_event_provider",
                "uq_whatsapp_event_idempotency",
                "uq_whatsapp_pending_provider",
                "uq_whatsapp_group_participant",
            ),
            raise_on_missing=True,
        )
        _WHATSAPP_IDENTITY_SCHEMA_READY = True


async def _record_whatsapp_event_dedup(
    db,
    company_id: str,
    metadata_payload: dict | None,
    *,
    event_type: str,
    provider_event_id: str = "",
    idempotency_key: str = "",
    payload: dict | None = None,
) -> dict:
    scoped_company_id = str(company_id or "").strip()
    provider_id = str(provider_event_id or "").strip()
    idempotency = str(idempotency_key or "").strip()
    if not db or not scoped_company_id or (not provider_id and not idempotency):
        return {"duplicate": False}
    await _ensure_whatsapp_identity_schema(db)
    account_id = _whatsapp_account_id(metadata_payload)
    payload_hash = hashlib.sha256(
        json.dumps(payload or {}, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    try:
        existing = None
        if provider_id:
            existing = r(
                await db.fetchrow(
                    "SELECT id,attempts FROM whatsapp_event_dedup WHERE company_id=$1 AND channel='whatsapp' "
                    "AND account_id=$2 AND event_type=$3 AND provider_event_id=$4 LIMIT 1",
                    scoped_company_id,
                    account_id,
                    event_type,
                    provider_id,
                )
            )
        if not existing and idempotency:
            existing = r(
                await db.fetchrow(
                    "SELECT id,attempts FROM whatsapp_event_dedup WHERE company_id=$1 AND idempotency_key=$2 LIMIT 1",
                    scoped_company_id,
                    idempotency,
                )
            )
        if existing:
            await db.execute(
                "UPDATE whatsapp_event_dedup SET attempts=attempts+1,last_seen_at=NOW(),updated_at=NOW() WHERE id=$1",
                existing.get("id", ""),
            )
            logger.info(
                "duplicate event skipped channel=whatsapp company_id=%s event_type=%s provider_event_id=%s idempotency_key=%s",
                scoped_company_id,
                event_type,
                provider_id,
                idempotency,
            )
            return {"duplicate": True, "id": existing.get("id", "")}
        row_id = make_id()
        await db.execute(
            "INSERT INTO whatsapp_event_dedup(id,company_id,channel,account_id,event_type,provider_event_id,idempotency_key,payload_hash,metadata,created_at,updated_at) "
            "VALUES($1,$2,'whatsapp',$3,$4,$5,$6,$7,$8::jsonb,NOW(),NOW())",
            row_id,
            scoped_company_id,
            account_id,
            event_type,
            provider_id,
            idempotency,
            payload_hash,
            json.dumps(dict(metadata_payload or {}), ensure_ascii=True, default=str),
        )
        return {"duplicate": False, "id": row_id}
    except Exception as exc:
        if _is_unique_constraint_violation(exc):
            logger.info(
                "duplicate event skipped after dedup insert race channel=whatsapp company_id=%s event_type=%s provider_event_id=%s idempotency_key=%s",
                scoped_company_id,
                event_type,
                provider_id,
                idempotency,
            )
            return {"duplicate": True}
        logger.warning("WhatsApp event dedup failed company_id=%s event_type=%s provider_event_id=%s: %s", scoped_company_id, event_type, provider_id, exc)
        return {"duplicate": False}


async def _upsert_whatsapp_identity_aliases(
    db,
    company_id: str,
    metadata_payload: dict | None,
    *,
    customer_id: str = "",
    conversation_id: str = "",
    channel_binding: str = "",
    sender_contact: str = "",
    status: str = "resolved",
    display_name: str = "",
    profile_picture_url: str = "",
) -> str:
    scoped_company_id = str(company_id or "").strip()
    if not db or not scoped_company_id:
        return ""
    await _ensure_whatsapp_identity_schema(db)
    payload = dict(metadata_payload or {})
    account_id = _whatsapp_account_id(payload)
    bridge_scope = str(payload.get("bridge_scope") or "").strip()
    safe_avatar = _safe_provider_avatar_url(
        profile_picture_url
        or payload.get("profile_picture_url")
        or payload.get("group_profile_picture_url")
        or ""
    )
    aliases = _whatsapp_alias_candidates(payload, sender_contact=sender_contact, channel_binding=channel_binding)
    first_identity_id = ""
    for alias in aliases:
        identity_type = alias["identity_type"]
        identity_value = alias["identity_value"]
        normalized = alias["identity_value_normalized"]
        if not normalized:
            continue
        digits = _identity_digits(identity_value)
        canonical_phone = f"+{digits}" if digits and identity_type in {"phone", "jid"} else ""
        remote_jid = identity_value if identity_type in {"jid", "provider_jid"} else ""
        lid_jid = identity_value if identity_type == "lid_jid" else ""
        chat_id = identity_value if identity_type in {"group_jid", "raw"} and str(alias.get("source") or "") == "chat_id" else str(payload.get("chat_id") or "")
        group_id = str(payload.get("group_id") or "") if _is_whatsapp_group_metadata(payload) else ""
        try:
            row = r(
                await db.fetchrow(
                    "INSERT INTO whatsapp_identity_mappings("
                    "id,company_id,channel,account_id,bridge_scope,identity_type,identity_value,identity_value_normalized,"
                    "canonical_phone,remote_jid,lid_jid,chat_id,contact_id,customer_id,conversation_id,group_id,display_name,profile_picture_url,status,metadata,created_at,updated_at"
                    ") VALUES($1,$2,'whatsapp',$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19::jsonb,NOW(),NOW()) "
                    "ON CONFLICT (company_id,channel,account_id,identity_type,identity_value_normalized) "
                    "WHERE BTRIM(identity_value_normalized) <> '' DO UPDATE SET "
                    "customer_id=COALESCE(NULLIF(EXCLUDED.customer_id,''), whatsapp_identity_mappings.customer_id), "
                    "conversation_id=COALESCE(NULLIF(EXCLUDED.conversation_id,''), whatsapp_identity_mappings.conversation_id), "
                    "canonical_phone=COALESCE(NULLIF(EXCLUDED.canonical_phone,''), whatsapp_identity_mappings.canonical_phone), "
                    "remote_jid=COALESCE(NULLIF(EXCLUDED.remote_jid,''), whatsapp_identity_mappings.remote_jid), "
                    "lid_jid=COALESCE(NULLIF(EXCLUDED.lid_jid,''), whatsapp_identity_mappings.lid_jid), "
                    "chat_id=COALESCE(NULLIF(EXCLUDED.chat_id,''), whatsapp_identity_mappings.chat_id), "
                    "contact_id=COALESCE(NULLIF(EXCLUDED.contact_id,''), whatsapp_identity_mappings.contact_id), "
                    "group_id=COALESCE(NULLIF(EXCLUDED.group_id,''), whatsapp_identity_mappings.group_id), "
                    "display_name=COALESCE(NULLIF(EXCLUDED.display_name,''), whatsapp_identity_mappings.display_name), "
                    "profile_picture_url=COALESCE(NULLIF(EXCLUDED.profile_picture_url,''), whatsapp_identity_mappings.profile_picture_url), "
                    "status=CASE WHEN whatsapp_identity_mappings.status='pending' AND EXCLUDED.status='resolved' THEN 'resolved' ELSE whatsapp_identity_mappings.status END, "
                    "metadata=whatsapp_identity_mappings.metadata || EXCLUDED.metadata, last_seen_at=NOW(), updated_at=NOW() "
                    "RETURNING id",
                    make_id(),
                    scoped_company_id,
                    account_id,
                    bridge_scope,
                    identity_type,
                    identity_value,
                    normalized,
                    canonical_phone,
                    remote_jid,
                    lid_jid,
                    chat_id,
                    str(payload.get("contact_id") or ""),
                    str(customer_id or ""),
                    str(conversation_id or ""),
                    group_id,
                    str(display_name or payload.get("profile_name") or payload.get("group_name") or ""),
                    safe_avatar,
                    str(status or "resolved"),
                    json.dumps({"source": alias.get("source", ""), **payload}, ensure_ascii=True, default=str),
                )
            )
            if row and row.get("id") and not first_identity_id:
                first_identity_id = str(row.get("id") or "")
            if identity_type == "lid_jid":
                logger.info(
                    "LID alias mapped company_id=%s account_id=%s lid_jid=%s customer_id=%s conversation_id=%s status=%s",
                    scoped_company_id,
                    account_id,
                    identity_value,
                    customer_id,
                    conversation_id,
                    status,
                )
        except Exception as exc:
            logger.warning(
                "WhatsApp identity alias upsert failed company_id=%s identity_type=%s identity_value=%s: %s",
                scoped_company_id,
                identity_type,
                identity_value,
                exc,
            )
    if safe_avatar and customer_id:
        try:
            await db.execute(
                "UPDATE customers SET avatar=COALESCE(NULLIF(avatar,''), $1),updated_at=NOW() WHERE id=$2 AND company_id=$3",
                safe_avatar,
                customer_id,
                scoped_company_id,
            )
        except Exception as exc:
            logger.debug("WhatsApp profile picture customer cache skipped customer_id=%s: %s", customer_id, exc)
    if safe_avatar:
        logger.info(
            "profile picture fetched channel=whatsapp company_id=%s customer_id=%s conversation_id=%s",
            scoped_company_id,
            customer_id,
            conversation_id,
        )
    if str(status or "").strip().lower() == "resolved" and customer_id:
        for alias in aliases:
            await _reconcile_pending_whatsapp_identity(
                db,
                company_id=scoped_company_id,
                account_id=account_id,
                raw_identity=str(alias.get("identity_value") or ""),
                normalized_identity=str(alias.get("identity_value_normalized") or ""),
                customer_id=customer_id,
                conversation_id=conversation_id,
            )
    return first_identity_id


async def _reconcile_pending_whatsapp_identity(
    db,
    *,
    company_id: str,
    account_id: str,
    raw_identity: str,
    normalized_identity: str = "",
    customer_id: str = "",
    conversation_id: str = "",
) -> None:
    scoped_company_id = str(company_id or "").strip()
    raw = str(raw_identity or "").strip()
    normalized = str(normalized_identity or "").strip()
    if not db or not scoped_company_id or not customer_id or not (raw or normalized):
        return
    try:
        rows = await db.fetch(
            "SELECT id,message_id,conversation_id FROM whatsapp_pending_messages "
            "WHERE company_id=$1 AND channel='whatsapp' AND account_id=$2 AND status='pending' "
            "AND (raw_identity=$3 OR raw_identity=$4)",
            scoped_company_id,
            str(account_id or ""),
            raw,
            normalized,
        )
    except Exception as exc:
        logger.debug("WhatsApp pending reconciliation lookup failed company_id=%s raw_identity=%s: %s", scoped_company_id, raw, exc)
        return
    for row in rows or []:
        pending = r(row)
        pending_id = str(pending.get("id") or "")
        pending_message_id = str(pending.get("message_id") or "")
        try:
            if pending_message_id and conversation_id:
                await db.execute(
                    "UPDATE messages SET conversation_id=$1,updated_at=NOW() WHERE id=$2 AND company_id=$3",
                    conversation_id,
                    pending_message_id,
                    scoped_company_id,
                )
            await db.execute(
                "UPDATE whatsapp_pending_messages SET status='resolved',customer_id=$1,conversation_id=COALESCE(NULLIF($2,''),conversation_id),"
                "last_seen_at=NOW(),updated_at=NOW() WHERE id=$3 AND company_id=$4",
                customer_id,
                conversation_id,
                pending_id,
                scoped_company_id,
            )
            logger.info(
                "identity resolved channel=whatsapp company_id=%s raw_identity=%s customer_id=%s conversation_id=%s message_id=%s",
                scoped_company_id,
                raw or normalized,
                customer_id,
                conversation_id,
                pending_message_id,
            )
        except Exception as exc:
            logger.warning(
                "WhatsApp pending reconciliation failed company_id=%s pending_id=%s raw_identity=%s: %s",
                scoped_company_id,
                pending_id,
                raw or normalized,
                exc,
            )


async def _persist_whatsapp_message_context(
    db,
    *,
    company_id: str,
    message_id: str,
    conversation_id: str,
    metadata_payload: dict | None,
    direction: str,
    source: str,
    identity_id: str = "",
) -> None:
    payload = dict(metadata_payload or {})
    if not db or not company_id or not message_id:
        return
    await _ensure_whatsapp_identity_schema(db)
    provider_event_id = str(
        payload.get("external_message_id")
        or payload.get("outbound_external_message_id")
        or payload.get("inbound_external_message_id")
        or payload.get("provider_event_id")
        or ""
    ).strip()
    group_id = _whatsapp_group_id(payload)
    group_name = str(payload.get("group_name") or "").strip()
    participant_id = str(payload.get("group_sender_phone") or payload.get("msg_author") or payload.get("raw_from") or "").strip()
    participant_name = str(payload.get("group_sender_name") or payload.get("group_participant_name") or "").strip()
    try:
        await db.execute(
            "UPDATE messages SET provider_event_id=$1,message_direction=$2,source=$3,whatsapp_identity_id=$4,"
            "whatsapp_group_id=$5,whatsapp_group_name=$6,whatsapp_participant_id=$7,whatsapp_participant_name=$8,"
            "raw_metadata=$9::jsonb,updated_at=NOW() WHERE id=$10 AND company_id=$11",
            provider_event_id,
            direction,
            source,
            identity_id,
            group_id,
            group_name,
            participant_id,
            participant_name,
            json.dumps(payload, ensure_ascii=True, default=str),
            message_id,
            company_id,
        )
        if conversation_id:
            direct_group_capture = _metadata_truthy(payload.get("group_direct_lead_capture"))
            conversation_group_id = "" if direct_group_capture else group_id
            identity_key = str(
                payload.get("normalized_sender_id")
                or payload.get("group_sender_phone")
                or payload.get("sender_contact")
                or ""
            ).strip()
            await db.execute(
                "UPDATE conversations SET is_group=$1,group_id=$2,whatsapp_account_id=$3,identity_key=$4,updated_at=NOW() "
                "WHERE id=$5 AND company_id=$6",
                bool(conversation_group_id),
                conversation_group_id,
                _whatsapp_account_id(payload),
                identity_key or conversation_group_id,
                conversation_id,
                company_id,
            )
    except Exception as exc:
        logger.warning("WhatsApp message context persist failed company_id=%s message_id=%s: %s", company_id, message_id, exc)


async def _upsert_whatsapp_group_participant(
    db,
    *,
    company_id: str,
    metadata_payload: dict | None,
    participant_customer_id: str = "",
) -> None:
    payload = dict(metadata_payload or {})
    group_id = _whatsapp_group_id(payload)
    if not db or not company_id or not group_id:
        return
    await _ensure_whatsapp_identity_schema(db)
    participant_jid = str(payload.get("msg_author") or payload.get("raw_from") or payload.get("group_sender_phone") or "").strip()
    participant_phone = str(payload.get("group_sender_phone") or "").strip()
    if not participant_jid and not participant_phone:
        return
    try:
        await db.execute(
            "INSERT INTO whatsapp_group_participants(id,company_id,account_id,group_id,participant_jid,participant_phone,participant_customer_id,display_name,profile_picture_url,metadata,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,NOW(),NOW()) "
            "ON CONFLICT (company_id,account_id,group_id,participant_jid) "
            "WHERE BTRIM(group_id) <> '' AND BTRIM(participant_jid) <> '' DO UPDATE SET "
            "participant_phone=COALESCE(NULLIF(EXCLUDED.participant_phone,''), whatsapp_group_participants.participant_phone), "
            "participant_customer_id=COALESCE(NULLIF(EXCLUDED.participant_customer_id,''), whatsapp_group_participants.participant_customer_id), "
            "display_name=COALESCE(NULLIF(EXCLUDED.display_name,''), whatsapp_group_participants.display_name), "
            "profile_picture_url=COALESCE(NULLIF(EXCLUDED.profile_picture_url,''), whatsapp_group_participants.profile_picture_url), "
            "metadata=whatsapp_group_participants.metadata || EXCLUDED.metadata,last_seen_at=NOW(),updated_at=NOW()",
            make_id(),
            company_id,
            _whatsapp_account_id(payload),
            group_id,
            participant_jid or participant_phone,
            participant_phone,
            participant_customer_id,
            str(payload.get("group_sender_name") or payload.get("group_participant_name") or ""),
            _safe_provider_avatar_url(str(payload.get("profile_picture_url") or "")),
            json.dumps(payload, ensure_ascii=True, default=str),
        )
        logger.info(
            "group message received channel=whatsapp company_id=%s group_id=%s group_name=%s participant=%s",
            company_id,
            group_id,
            str(payload.get("group_name") or ""),
            participant_jid or participant_phone,
        )
    except Exception as exc:
        logger.warning("WhatsApp group participant upsert failed company_id=%s group_id=%s: %s", company_id, group_id, exc)


async def _store_pending_whatsapp_message(
    db,
    *,
    company_id: str,
    metadata_payload: dict | None,
    direction: str,
    provider_event_id: str = "",
    raw_identity: str = "",
    payload: dict | None = None,
    customer_id: str = "",
    conversation_id: str = "",
    message_id: str = "",
) -> dict:
    scoped_company_id = str(company_id or "").strip()
    raw = str(raw_identity or "").strip()
    if not db or not scoped_company_id or not raw:
        return {}
    await _ensure_whatsapp_identity_schema(db)
    meta = dict(metadata_payload or {})
    account_id = _whatsapp_account_id(meta)
    provider_id = str(provider_event_id or meta.get("external_message_id") or "").strip()
    row_id = make_id()
    try:
        await db.execute(
            "INSERT INTO whatsapp_pending_messages(id,company_id,channel,account_id,direction,provider_event_id,raw_identity,identity_type,payload,metadata,status,customer_id,conversation_id,message_id,created_at,updated_at) "
            "VALUES($1,$2,'whatsapp',$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,'pending',$10,$11,$12,NOW(),NOW()) "
            "ON CONFLICT (company_id,channel,account_id,provider_event_id) WHERE BTRIM(provider_event_id) <> '' DO UPDATE SET "
            "raw_identity=EXCLUDED.raw_identity,identity_type=EXCLUDED.identity_type,payload=EXCLUDED.payload,metadata=whatsapp_pending_messages.metadata || EXCLUDED.metadata,"
            "customer_id=COALESCE(NULLIF(EXCLUDED.customer_id,''), whatsapp_pending_messages.customer_id), "
            "conversation_id=COALESCE(NULLIF(EXCLUDED.conversation_id,''), whatsapp_pending_messages.conversation_id), "
            "message_id=COALESCE(NULLIF(EXCLUDED.message_id,''), whatsapp_pending_messages.message_id), "
            "attempts=whatsapp_pending_messages.attempts+1,last_seen_at=NOW(),updated_at=NOW()",
            row_id,
            scoped_company_id,
            account_id,
            direction,
            provider_id,
            raw,
            _whatsapp_identity_type(raw) or "raw",
            json.dumps(payload or {}, ensure_ascii=True, default=str),
            json.dumps(meta, ensure_ascii=True, default=str),
            customer_id,
            conversation_id,
            message_id,
        )
        await _upsert_whatsapp_identity_aliases(
            db,
            scoped_company_id,
            meta,
            customer_id=customer_id,
            conversation_id=conversation_id,
            channel_binding=raw,
            sender_contact=raw,
            status="pending",
            display_name=str(meta.get("profile_name") or meta.get("sender_name") or ""),
        )
        logger.warning(
            "identity unresolved channel=whatsapp company_id=%s direction=%s provider_event_id=%s raw_identity=%s",
            scoped_company_id,
            direction,
            provider_id,
            raw,
        )
        return {"pending_identity": True, "provider_event_id": provider_id, "raw_identity": raw}
    except Exception as exc:
        logger.warning("Pending WhatsApp message persist failed company_id=%s provider_event_id=%s raw_identity=%s: %s", scoped_company_id, provider_id, raw, exc)
        return {}


async def _load_ai_message_by_idempotency(db, *, company_id: str, idempotency_key: str) -> dict:
    key = str(idempotency_key or "").strip()
    if not db or not company_id or not key:
        return {}
    await _ensure_messages_idempotency_schema(db)
    try:
        row = await db.fetchrow(
            "SELECT id FROM messages WHERE company_id=$1 AND idempotency_key=$2 ORDER BY created_at DESC LIMIT 1",
            company_id,
            key,
        )
    except Exception:
        logger.exception("AI idempotency lookup failed company_id=%s idempotency_key=%s", company_id, key)
        return {}
    if not row:
        return {}
    return await _load_message_with_attachments(db, str(dict(row).get("id") or row["id"]))


async def _escalate_conversation_to_human_once(
    db,
    *,
    company_id: str,
    conversation_id: str,
    customer_name: str,
    channel: str,
    reason: str,
    automatic: bool,
    idempotency_key: str,
    trace_id: str = "",
):
    key = str(idempotency_key or "").strip()
    if not key:
        return await escalate_conversation_to_human(
            db,
            conversation_id,
            company_id,
            customer_name,
            channel,
            reason=reason,
            automatic=automatic,
        )

    await _ensure_messages_idempotency_schema(db)
    try:
        async with db.transaction() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", key)
            existing_message = await _load_ai_message_by_idempotency(
                conn,
                company_id=company_id,
                idempotency_key=key,
            )
            if existing_message:
                logger.info(
                    "Skipping duplicate AI shutdown escalation company_id=%s conversation_id=%s channel=%s message_id=%s idempotency_key=%s trace_id=%s",
                    company_id,
                    conversation_id,
                    channel,
                    existing_message.get("id", ""),
                    key,
                    trace_id,
                )
                conversation = r(await conn.fetchrow("SELECT * FROM conversations WHERE id=$1", conversation_id)) or {}
                return {
                    "conversation": conversation,
                    "message": existing_message,
                    "escalation_notice": conversation.get("escalation_notice", "Conversation escalated"),
                    "duplicate": True,
                }

            escalation = await escalate_conversation_to_human(
                conn,
                conversation_id,
                company_id,
                customer_name,
                channel,
                reason=reason,
                automatic=automatic,
            )
            message = dict((escalation or {}).get("message") or {})
            message_id = str(message.get("id") or "").strip()
            if message_id:
                try:
                    await conn.execute(
                        "UPDATE messages SET idempotency_key=$1,updated_at=NOW() "
                        "WHERE id=$2 AND company_id=$3 AND BTRIM(idempotency_key)=''",
                        key,
                        message_id,
                        company_id,
                    )
                    message["idempotency_key"] = key
                    escalation["message"] = message
                except Exception as exc:
                    if _is_unique_constraint_violation(exc):
                        existing_message = await _load_ai_message_by_idempotency(
                            conn,
                            company_id=company_id,
                            idempotency_key=key,
                        )
                        if existing_message:
                            logger.info(
                                "Skipping duplicate AI shutdown escalation after idempotency race company_id=%s conversation_id=%s channel=%s message_id=%s idempotency_key=%s trace_id=%s",
                                company_id,
                                conversation_id,
                                channel,
                                existing_message.get("id", ""),
                                key,
                                trace_id,
                            )
                            escalation["message"] = existing_message
                            escalation["duplicate"] = True
                            return escalation
                    raise
            return escalation
    except Exception:
        logger.exception(
            "AI shutdown escalation idempotency guard failed company_id=%s conversation_id=%s channel=%s idempotency_key=%s trace_id=%s",
            company_id,
            conversation_id,
            channel,
            key,
            trace_id,
        )
        return await escalate_conversation_to_human(
            db,
            conversation_id,
            company_id,
            customer_name,
            channel,
            reason=reason,
            automatic=automatic,
        )


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
    failure_code = _outbound_failure_code(error)
    dedupe_minutes = 15
    try:
        existing_notice = await db.fetchrow(
            "SELECT id FROM messages WHERE company_id=$1 AND conversation_id=$2 AND sender_type='system' "
            "AND is_alert=TRUE AND content ILIKE $3 "
            "AND created_at >= NOW() - ($4 * INTERVAL '1 minute') "
            "ORDER BY created_at DESC LIMIT 1",
            company_id,
            conversation_id,
            f"%Failure code: {failure_code}%",
            dedupe_minutes,
        )
        if existing_notice:
            logger.info(
                "Skipping duplicate outbound failure notice company_id=%s conversation_id=%s channel=%s message_id=%s trace_id=%s failure_code=%s",
                company_id,
                conversation_id,
                channel,
                failed_message_id,
                trace_id,
                failure_code,
            )
            return
    except Exception as exc:
        logger.warning(
            "Outbound failure notice dedupe lookup failed company_id=%s conversation_id=%s failure_code=%s: %s",
            company_id,
            conversation_id,
            failure_code,
            exc,
        )
    logger.warning(
        "Emitting outbound failure notice company_id=%s conversation_id=%s channel=%s message_id=%s trace_id=%s failure_code=%s error=%s",
        company_id,
        conversation_id,
        channel,
        failed_message_id,
        trace_id,
        failure_code,
        error,
    )
    notice_id = make_id()
    if failure_code == "WHATSAPP_SESSION_NOT_READY":
        notice_text = (
            f"Delivery failed on {channel}. Message {failed_message_id or 'unknown'} was not sent. "
            "WhatsApp is not ready for the selected account; scan the QR for this exact workspace/account, "
            "then retry the failed message. "
            f"Failure code: {failure_code}."
        )
    else:
        notice_text = (
            f"Delivery failed on {channel}. Message {failed_message_id or 'unknown'} was not sent. "
            f"Error: {str(error or 'Unknown delivery error').strip()} "
            f"Failure code: {failure_code}."
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


async def _emit_conversation_limit_notice(
    db,
    *,
    company_id: str,
    conversation_id: str,
    limit_state: dict[str, Any],
) -> dict:
    if not db or not company_id or not conversation_id:
        return {}
    notice_text = conversation_limit_completed_message(
        int(limit_state.get("limit") or 0),
        limit_state.get("used"),
    )
    try:
        existing_notice = r(
            await db.fetchrow(
                "SELECT * FROM messages WHERE company_id=$1 AND conversation_id=$2 AND sender_type='system' "
                "AND is_alert=TRUE AND content=$3 AND created_at >= NOW() - INTERVAL '15 minutes' "
                "ORDER BY created_at DESC LIMIT 1",
                company_id,
                conversation_id,
                notice_text,
            )
        )
        if existing_notice:
            return existing_notice
    except Exception as exc:
        logger.warning(
            "Conversation limit notice dedupe failed company_id=%s conversation_id=%s: %s",
            company_id,
            conversation_id,
            exc,
        )
    notice_id = make_id()
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
    return notice_message


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
    metadata_payload = dict(metadata or {})
    if str(metadata_payload.get("source") or "").startswith("webhook_"):
        logger.info(
            "lead_workflow_skipped_for_message_hot_path company_id=%s lead_id=%s source=%s",
            company_id,
            str(lead.get("id") or ""),
            str(metadata_payload.get("source") or source or ""),
        )
        score = int(lead.get("score") or 0)
        if score <= 0:
            score = 40
            if lead.get("email") and lead.get("phone"):
                score += 20
            elif lead.get("email") or lead.get("phone"):
                score += 10
        grade = str(lead.get("grade") or "").strip().lower()
        if grade not in {"hot", "warm", "cold"}:
            grade = "hot" if score >= 80 else "warm" if score >= 60 else "cold"
        phase = str(lead.get("phase") or "awareness")
        await db.execute(
            "UPDATE leads SET score=$1,grade=$2,phase=$3,scoring_reason=$4,next_action=$5,updated_at=NOW() WHERE id=$6",
            max(0, min(100, score)),
            grade,
            phase,
            "Deferred LLM scoring for inbound message hot path.",
            "Review lead after conversation",
            str(lead.get("id") or ""),
        )
        return r(await db.fetchrow("SELECT * FROM leads WHERE id=$1", str(lead.get("id") or "")))

    workflow = await orchestrate_lead_workflow(
        LeadWorkflowRequest(
            company_id=company_id,
            lead_id=str(lead.get("id") or ""),
            customer_id=str(customer.get("id") or ""),
            source=source,
            raw_message=raw_message,
            lead=lead,
            customer=customer,
            metadata=metadata_payload,
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
        normalized_channel = str(channel or "").strip().lower()
        existing = None
        contact_fields = _extract_sender_contact_fields(channel, sender_contact, metadata_payload)
        avatar_url = _safe_provider_avatar_url(
            metadata_payload.get("profile_picture_url")
            or metadata_payload.get("profile_picture")
            or metadata_payload.get("avatar")
            or ""
        )
        explicit_customer_id = str(metadata_payload.get("customer_id") or "").strip()
        trusted_explicit_customer_id = explicit_customer_id
        if explicit_customer_id:
            explicit_customer = r(
                await db.fetchrow(
                    "SELECT * FROM customers WHERE id=$1 AND company_id=$2 LIMIT 1",
                    explicit_customer_id,
                    company_id,
                )
            )
            if explicit_customer:
                if normalized_channel == "whatsapp":
                    if _customer_matches_contact_identity(explicit_customer, contact_fields):
                        existing = explicit_customer
                    else:
                        trusted_explicit_customer_id = ""
                        logger.warning(
                            "Ignoring WhatsApp customer_id override because remote identity does not match company_id=%s customer_id=%s sender_contact=%s normalized_phone=%s trace_id=%s",
                            company_id,
                            explicit_customer_id,
                            sender_contact,
                            contact_fields.get("phone", ""),
                            metadata_payload.get("trace_id", ""),
                        )
                else:
                    existing = explicit_customer
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
        if (contact_fields["phone"] or contact_fields["email"]) and not existing:
            # Exact match first (cheap, indexed), accepting both +E.164 and
            # legacy digits-only phone rows before falling back to suffix match.
            exact_contact = contact_fields["email"] or contact_fields["phone"]
            for lookup_value, lookup_digits in _contact_lookup_values(
                contact_fields["email"],
                contact_fields["phone"],
            ):
                existing = r(
                    await db.fetchrow(
                        "SELECT * FROM customers WHERE company_id=$1 AND "
                        "(phone=$2 OR LOWER(email)=LOWER($2) OR "
                        "($3<>'' AND regexp_replace(phone, '\\D', '', 'g')=$3)) "
                        "ORDER BY updated_at DESC LIMIT 1",
                        company_id,
                        lookup_value,
                        lookup_digits,
                    )
                )
                if existing:
                    break
            # Identity fallback — case-insensitive email + last-10-digits phone.
            if not existing:
                contact_str = str(exact_contact).strip()
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
                        rows = await db.fetch(
                            "SELECT * FROM customers WHERE company_id=$1 "
                            "AND regexp_replace(phone, '\\D', '', 'g') LIKE $2 "
                            "ORDER BY updated_at DESC LIMIT 2",
                            company_id,
                            f"%{last10}",
                        )
                        if len(rows or []) == 1:
                            existing = r(rows[0])
        social_profile_id = contact_fields["social_profile_id"] or social_profile_id
        if not existing:
            existing = await resolve_customer_by_contact(
                db,
                company_id,
                phone=contact_fields["phone"],
                email=contact_fields["email"],
                channel=channel,
                channel_profile_id=social_profile_id,
                explicit_customer_id=trusted_explicit_customer_id,
            )
        hint_customer_id = str(metadata_payload.get("resolved_customer_id_hint") or "").strip()
        if not existing and hint_customer_id and normalized_channel == "whatsapp":
            hint_customer = r(
                await db.fetchrow(
                    "SELECT * FROM customers WHERE id=$1 AND company_id=$2 LIMIT 1",
                    hint_customer_id,
                    company_id,
                )
            )
            if _customer_matches_contact_identity(hint_customer, contact_fields):
                existing = hint_customer
            elif hint_customer:
                logger.warning(
                    "Ignoring WhatsApp resolved_customer_id hint because remote identity does not match company_id=%s customer_id=%s sender_contact=%s normalized_phone=%s trace_id=%s",
                    company_id,
                    hint_customer_id,
                    sender_contact,
                    contact_fields.get("phone", ""),
                    metadata_payload.get("trace_id", ""),
                )
        if not existing:
            if normalized_channel == "whatsapp" and not contact_fields["phone"] and not social_profile_id:
                logger.warning(
                    "Refusing to create WhatsApp customer without stable remote identity sender_contact=%s channel_id=%s company_id=%s trace_id=%s",
                    sender_contact,
                    contact_fields.get("channel_id", ""),
                    company_id,
                    metadata_payload.get("trace_id", ""),
                )
                return None
            nid = make_id()
            normalized_phone = (
                await normalize_customer_contact_phone(db, company_id, contact_fields["phone"])
                if contact_fields["phone"]
                else ""
            )
            display_sender_name = (
                _clean_display_name(_preferred_sender_name(channel, sender_name, metadata_payload))
                or (
                    f"WhatsApp {contact_fields['phone'] or contact_fields['channel_id']}"
                    if normalized_channel == "whatsapp"
                    else ""
                )
            )
            await db.execute(
                "INSERT INTO customers(id,company_id,name,email,phone,segment,avatar,lifecycle_stage,lifetime_value,avg_sentiment,"  # noqa: E501
                "recent_tickets,complaint_count,days_since_last_contact,total_conversations,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,$5,'general',$6,'lead',0,0,0,0,0,0,NOW(),NOW())",
                nid,
                company_id,
                display_sender_name or "Unknown Contact",
                contact_fields["email"],
                normalized_phone,
                avatar_url,
            )
            existing = r(await db.fetchrow("SELECT * FROM customers WHERE id=$1", nid))
        else:
            customer_updates = []
            args = []
            if company_id and not existing.get("company_id"):
                customer_updates.append(f"company_id=${len(args) + 1}")
                args.append(company_id)
                existing["company_id"] = company_id
            display_sender_name = _clean_display_name(
                _preferred_sender_name(channel, sender_name, metadata_payload, existing.get("name", ""))
            )
            if _should_update_sender_name(channel, display_sender_name, metadata_payload, existing.get("name", "")):
                customer_updates.append(f"name=${len(args) + 1}")
                args.append(display_sender_name)
                existing["name"] = display_sender_name
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
            if avatar_url and not existing.get("avatar"):
                customer_updates.append(f"avatar=${len(args) + 1}")
                args.append(avatar_url)
                existing["avatar"] = avatar_url
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
            display_lead_name = (
                _clean_display_name(
                    _preferred_sender_name(channel, sender_name, metadata_payload, existing.get("name", ""))
                )
                or existing.get("name", "")
            )
            await db.execute(
                "INSERT INTO leads(id,company_id,name,email,phone,source,status,score,grade,phase,notes,assigned_to,assigned_name,created_at,updated_at) "  # noqa: E501
                "VALUES($1,$2,$3,$4,$5,$6,'new',0,'cold','awareness',$7,'','',NOW(),NOW())",
                lid,
                company_id,
                display_lead_name or "Unknown",
                existing.get("email", ""),
                existing.get("phone", ""),
                channel,
                f"Auto-captured from {channel}: {message_text[:200]}",
            )
            if avatar_url:
                try:
                    await db.execute(
                        "UPDATE leads SET metadata=jsonb_set(COALESCE(metadata,'{}'::jsonb), '{profile_picture_url}', to_jsonb($1::text), true),updated_at=NOW() WHERE id=$2 AND company_id=$3",
                        avatar_url,
                        lid,
                        company_id,
                    )
                except Exception as exc:
                    logger.debug("Lead avatar metadata update skipped lead_id=%s: %s", lid, exc)
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
        display_lead_name = _clean_display_name(
            _preferred_sender_name(channel, sender_name, metadata_payload, existing_lead.get("name", ""))
        )
        if _should_update_sender_name(channel, display_lead_name, metadata_payload, existing_lead.get("name", "")):
            lead_updates.append(f"name=${len(args) + 1}")
            args.append(display_lead_name)
            existing_lead["name"] = display_lead_name
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
                "provider_media_id": str(attachment.get("provider_media_id") or "").strip(),
                "raw_metadata": dict(attachment.get("raw_metadata") or {}),
            }
        )
    return normalized


def _resolve_outbound_recipient(channel: str, convo: dict, customer: dict, sender_contact: str = "") -> str:
    normalized_channel = str(channel or "").strip().lower()
    identity = resolve_channel_outbound_recipient(
        normalized_channel,
        convo,
        customer,
        sender_contact=sender_contact,
    )
    if not identity.is_valid:
        logger.warning(
            "Failed to resolve outbound recipient channel=%s conversation_id=%s customer_id=%s raw_recipient=%s reason=%s",
            normalized_channel,
            convo.get("id", ""),
            customer.get("id", ""),
            identity.raw_value,
            identity.reason,
        )
        if normalized_channel == "whatsapp":
            return str(convo.get("channel_id") or customer.get("phone") or sender_contact or "").strip()
        return ""
    return identity.canonical_value


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
    if channel_type == ChannelType.WHATSAPP:
        default_region = await company_default_phone_region(db, company_id)
        whatsapp_identity = normalize_whatsapp_phone(recipient, default_region=default_region)
        if not whatsapp_identity.is_valid:
            error = (
                "Invalid WhatsApp phone number. Save the contact number in full international format "
                "or set the tenant default phone region in Company Settings."
            )
            logger.warning(
                "Invalid WhatsApp outbound recipient trace_id=%s company_id=%s conversation_id=%s customer_id=%s channel=%s raw_sender_id=%s normalized_sender_id=%s selected_recipient_id=%s reason=%s",
                str((metadata or {}).get("trace_id") or ""),
                company_id,
                conversation_id,
                str((metadata or {}).get("customer_id") or ""),
                channel,
                str((metadata or {}).get("raw_sender_id") or ""),
                str((metadata or {}).get("normalized_sender_id") or ""),
                recipient,
                whatsapp_identity.reason,
            )
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
                        "Failed to emit outbound failure notice for invalid WhatsApp recipient company_id=%s conversation_id=%s message_id=%s",
                        company_id,
                        conversation_id,
                        db_message_id,
                    )
            return False, error
        recipient = whatsapp_identity.canonical_value
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
    message_metadata.setdefault("conversation_id", conversation_id)
    message_metadata.setdefault("company_id", company_id)
    if channel_type == ChannelType.WHATSAPP:
        actor_user = str(message_metadata.get("actor_user_id") or message_metadata.get("user_id") or "").strip()
        message_metadata.setdefault("selected_whatsapp_scope", _bridge_scope_label(company_id, actor_user))
    trace_id = str(message_metadata.get("trace_id") or "").strip() or _trace_id_from_context()
    message_metadata["trace_id"] = trace_id

    single_dispatch_ai = _is_whatsapp_ai_auto_response(message_metadata)
    max_attempts = 1 if single_dispatch_ai else 3
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
            failure_code = _outbound_failure_code(last_error)
            logger.warning(
                "Outbound send attempt failed company_id=%s user_id=%s conversation_id=%s customer_id=%s message_id=%s ai_message_id=%s bridge_scope=%s selected_whatsapp_scope=%s bridge_state=%s bridge_connected_phone=%s recipient_id=%s send_attempt=%s idempotency_key=%s delivery_status=failed failure_code=%s trace_id=%s channel=%s error=%s",
                company_id,
                str(message_metadata.get("actor_user_id") or message_metadata.get("user_id") or ""),
                conversation_id,
                str(message_metadata.get("customer_id") or ""),
                db_message_id,
                db_message_id,
                str(message_metadata.get("bridge_scope") or ""),
                str(message_metadata.get("selected_whatsapp_scope") or ""),
                str(message_metadata.get("bridge_state") or ""),
                str(message_metadata.get("bridge_connected_phone") or ""),
                recipient,
                attempt,
                str(message_metadata.get("idempotency_key") or ""),
                failure_code,
                trace_id,
                channel,
                last_error or "unknown",
            )
            if _is_non_retryable_outbound_error(last_error):
                break
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
        if attempt < max_attempts and not _is_non_retryable_outbound_error(last_error):
            await asyncio.sleep(base_delay * (2 ** (attempt - 1)))

    if single_dispatch_ai and last_error and not _is_non_retryable_outbound_error(last_error):
        logger.warning(
            "AI auto-response retry suppressed to preserve idempotency company_id=%s conversation_id=%s channel=%s message_id=%s idempotency_key=%s trace_id=%s error=%s",
            company_id,
            conversation_id,
            channel,
            db_message_id,
            str(message_metadata.get("idempotency_key") or ""),
            trace_id,
            last_error,
        )

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


async def _retry_existing_ai_outbound_if_needed(
    db,
    *,
    company_id: str,
    channel: str,
    conversation_id: str,
    conversation: dict,
    customer: dict,
    sender_contact: str,
    ai_message: dict,
    idempotency_key: str,
    trace_id: str,
    metadata_payload: dict | None = None,
) -> bool:
    message_id = str((ai_message or {}).get("id") or "").strip()
    delivery_status = str((ai_message or {}).get("delivery_status") or "").strip().lower()
    if delivery_status in {"sent", "delivered", "read", "sending"}:
        logger.info(
            "ai_outbound_send_skipped company_id=%s conversation_id=%s channel=%s provider=%s auto_send=true workflow_id=%s message_id=%s skip_reason=already_%s",
            company_id,
            conversation_id,
            channel,
            channel,
            f"webhook_{channel}",
            message_id,
            delivery_status or "processed",
        )
        return False
    content = str((ai_message or {}).get("content") or "").strip()
    if not message_id or not content:
        logger.warning(
            "ai_outbound_send_skipped company_id=%s conversation_id=%s channel=%s provider=%s auto_send=true workflow_id=%s message_id=%s skip_reason=missing_existing_message_content",
            company_id,
            conversation_id,
            channel,
            channel,
            f"webhook_{channel}",
            message_id,
        )
        return False
    recipient_id = _resolve_outbound_recipient(channel, conversation, customer, sender_contact)
    if not recipient_id:
        await _persist_outbound_message_state(
            db,
            company_id=company_id,
            db_message_id=message_id,
            delivery_status="failed",
        )
        logger.warning(
            "ai_outbound_send_skipped company_id=%s conversation_id=%s channel=%s provider=%s auto_send=true workflow_id=%s message_id=%s skip_reason=missing_outbound_recipient",
            company_id,
            conversation_id,
            channel,
            channel,
            f"webhook_{channel}",
            message_id,
        )
        return False
    attachments = list((ai_message or {}).get("attachments") or [])
    started_at = time.monotonic()
    logger.info(
        "ai_outbound_send_attempt company_id=%s conversation_id=%s channel=%s provider=%s auto_send=true workflow_id=%s message_id=%s",
        company_id,
        conversation_id,
        channel,
        channel,
        f"webhook_{channel}",
        message_id,
    )
    sent, error = await _send_outbound_response_via_channel_layer(
        db=db,
        company_id=company_id,
        channel=channel,
        recipient_id=recipient_id,
        content=content,
        conversation_id=conversation_id,
        attachments=attachments,
        db_message_id=message_id,
        metadata={
            **dict(metadata_payload or {}),
            "source": f"webhook_{channel}",
            "trace_id": trace_id,
            "customer_id": str((customer or {}).get("id") or ""),
            "conversation_id": conversation_id,
            "idempotency_key": idempotency_key,
            "raw_sender_id": str(sender_contact or ""),
            "normalized_sender_id": recipient_id,
            "selected_outbound_recipient": recipient_id,
        },
    )
    logger.info(
        "ai_latency_stage stage=outbound_send duration_ms=%s conversation_id=%s company_id=%s request_id=%s trace_id=%s",
        _elapsed_ms(started_at),
        conversation_id,
        company_id,
        "",
        trace_id,
    )
    if sent:
        logger.info(
            "ai_outbound_send_success company_id=%s conversation_id=%s channel=%s provider=%s auto_send=true workflow_id=%s message_id=%s",
            company_id,
            conversation_id,
            channel,
            channel,
            f"webhook_{channel}",
            message_id,
        )
        return True
    logger.warning(
        "ai_outbound_send_failed company_id=%s conversation_id=%s channel=%s provider=%s auto_send=true workflow_id=%s message_id=%s error=%s",
        company_id,
        conversation_id,
        channel,
        channel,
        f"webhook_{channel}",
        message_id,
        error,
    )
    return False


async def _process_unified_outbound_bridge_message(
    db,
    message: UnifiedMessage,
    *,
    sender_name: str = "",
    sender_contact: str = "",
    metadata: dict | None = None,
):
    channel = message.channel_type.value
    metadata_payload = dict(message.metadata or {})
    metadata_payload.update(dict(metadata or {}))
    company_id = str(metadata_payload.get("company_id") or message.tenant_id or "").strip()
    company_id = await _validate_resolved_company_id(
        db,
        company_id,
        channel=channel,
        descriptor="process_outbound_bridge_message",
    )
    if not company_id:
        return None

    external_message_id = str(
        metadata_payload.get("outbound_external_message_id")
        or metadata_payload.get("inbound_external_message_id")
        or metadata_payload.get("external_message_id")
        or message.message_id
        or ""
    ).strip()
    if external_message_id:
        existing = r(
            await db.fetchrow(
                "SELECT id,conversation_id FROM messages "
                "WHERE company_id=$1 AND external_message_id=$2 "
                "ORDER BY created_at DESC LIMIT 1",
                company_id,
                external_message_id,
            )
        )
        if existing:
            logger.info(
                "Duplicate outbound bridge event ignored company_id=%s channel=%s external_message_id=%s message_id=%s",
                company_id,
                channel,
                external_message_id,
                existing.get("id", ""),
            )
            return {
                "conversation_id": existing.get("conversation_id", ""),
                "message_id": existing.get("id", ""),
                "customer_id": "",
                "customer_message": await _load_message_with_attachments(db, existing.get("id", "")),
                "duplicate": True,
            }

    target_contact = str(sender_contact or message.external_user_id or "").strip()
    metadata_payload.setdefault("company_id", company_id)
    metadata_payload.setdefault("external_message_id", external_message_id)
    metadata_payload.setdefault("outbound_external_message_id", external_message_id)
    metadata_payload.setdefault("normalized_sender_id", target_contact)
    metadata_payload.setdefault("sender_contact", target_contact)
    metadata_payload["direction"] = "outbound"

    is_group_message = channel == "whatsapp" and _is_whatsapp_group_metadata(metadata_payload)
    if is_group_message:
        group_id = _whatsapp_group_id(metadata_payload)
        customer = await _get_or_create_whatsapp_group_customer(
            db,
            company_id,
            group_id,
            str(metadata_payload.get("group_name") or sender_name or "WhatsApp Group"),
        )
        if not customer:
            return None
        result = {"lead_id": None, "lead": None, "customer": customer, "company_id": company_id}
        cid = str(customer.get("id") or "")
        channel_binding = group_id
    else:
        contact_fields = _extract_sender_contact_fields(channel, target_contact, metadata_payload)
        result = await _auto_capture_lead(
            db,
            channel,
            sender_name or f"WhatsApp {target_contact}",
            target_contact,
            str(message.content or "").strip(),
            metadata_payload,
        )
        if not result:
            pending_identity = str(
                metadata_payload.get("target_raw_id")
                or metadata_payload.get("target_lid_jid")
                or metadata_payload.get("provider_sender_id")
                or target_contact
                or ""
            ).strip()
            if channel == "whatsapp" and pending_identity:
                customer = await _get_or_create_whatsapp_pending_customer(
                    db,
                    company_id,
                    pending_identity,
                    sender_name or str(metadata_payload.get("profile_name") or ""),
                    str(metadata_payload.get("profile_picture_url") or ""),
                )
                if not customer:
                    await _store_pending_whatsapp_message(
                        db,
                        company_id=company_id,
                        metadata_payload=metadata_payload,
                        direction="outbound",
                        provider_event_id=external_message_id,
                        raw_identity=pending_identity,
                        payload={"content": str(message.content or ""), "attachments": [a.__dict__ for a in (message.attachments or [])]},
                    )
                    return {"pending_identity": True, "provider_event_id": external_message_id, "raw_identity": pending_identity}
                result = {"lead_id": None, "lead": None, "customer": customer, "company_id": company_id, "pending_identity": True}
                logger.warning(
                    "identity unresolved channel=whatsapp company_id=%s direction=outbound provider_event_id=%s raw_identity=%s stored_under_pending_customer=%s",
                    company_id,
                    external_message_id,
                    pending_identity,
                    customer.get("id", ""),
                )
            else:
                return None

        customer = dict(result.get("customer") or {})
        cid = str(customer.get("id") or "")
        channel_binding = contact_fields["channel_id"] or str(metadata_payload.get("target_raw_id") or metadata_payload.get("target_lid_jid") or "").strip()
    attachments = _legacy_attachments_from_unified(list(message.attachments or []))
    content = str(message.content or "").strip()
    created_at = message.timestamp or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    msg_id = make_id()
    idempotency_key = (
        f"out:{company_id}:{channel}:{external_message_id}"
        if external_message_id
        else f"out:{company_id}:{channel}:msg:{msg_id}"
    )

    await _ensure_messages_idempotency_schema(db)
    saved_attachments: list[dict] = []
    convo: dict = {}
    convo_id = ""
    try:
        async with db.transaction() as conn:
            if external_message_id:
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", idempotency_key)
                existing_after_lock = r(
                    await conn.fetchrow(
                        "SELECT id,conversation_id FROM messages "
                        "WHERE company_id=$1 AND external_message_id=$2 "
                        "ORDER BY created_at DESC LIMIT 1",
                        company_id,
                        external_message_id,
                    )
                )
                if existing_after_lock:
                    return {
                        "conversation_id": existing_after_lock.get("conversation_id", ""),
                        "message_id": existing_after_lock.get("id", ""),
                        "customer_id": cid,
                        "customer_message": await _load_message_with_attachments(db, existing_after_lock.get("id", "")),
                        "duplicate": True,
                    }

            convo = r(
                await conn.fetchrow(
                    "SELECT * FROM conversations WHERE customer_id=$1 AND channel=$2 AND status=ANY($3) AND company_id=$4 "
                    "AND ($5='' OR channel_id=$5) "
                    "ORDER BY updated_at DESC LIMIT 1",
                    cid,
                    channel,
                    ["open", "pending", "escalated"],
                    company_id,
                    channel_binding if is_group_message else "",
                )
            )
            if not convo:
                convo_id = make_id()
                conversation_subject = _conversation_subject_from_identity(
                    channel,
                    str(customer.get("name") or sender_name or ""),
                    metadata_payload,
                    is_group_message=is_group_message,
                )
                await conn.execute(
                    "INSERT INTO conversations(id,company_id,customer_id,customer_name,customer_avatar,channel,subject,status,priority,assigned_to,assigned_name,ai_handled,agent_type,"
                    "channel_id,sentiment_score,sentiment_label,message_count,last_message,last_message_at,unread_count,session_id,page_url,created_at,updated_at) "
                    "VALUES($1,$2,$3,$4,$5,$6,$7,'open','medium',$8,$9,TRUE,'generic',$10,$11::numeric,$12,0,'',$13,0,'','',NOW(),NOW())",
                    convo_id,
                    company_id,
                    cid,
                    customer.get("name", sender_name or "WhatsApp Contact"),
                    customer.get("avatar", ""),
                    channel,
                    conversation_subject,
                    "",
                    "",
                    channel_binding,
                    0,
                    "neutral",
                    created_at,
                )
                convo = r(await conn.fetchrow("SELECT * FROM conversations WHERE id=$1", convo_id))
            else:
                convo_id = str(convo.get("id") or "")
                if channel_binding and convo.get("channel_id") != channel_binding:
                    await conn.execute(
                        "UPDATE conversations SET channel_id=$1,updated_at=NOW() WHERE id=$2 AND company_id=$3",
                        channel_binding,
                        convo_id,
                        company_id,
                    )
                    convo["channel_id"] = channel_binding

            await conn.execute(
                "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,"
                "external_message_id,idempotency_key,delivery_status,sent_at,read,created_at) "
                "VALUES($1,$2,$3,$4,'agent',$5,$6,$7,$8,'sent',$9,TRUE,$9)",
                msg_id,
                company_id,
                convo_id,
                content,
                str(metadata_payload.get("bridge_user_id") or metadata_payload.get("actor_user_id") or "whatsapp-linked-device"),
                str(metadata_payload.get("agent_name") or "WhatsApp Linked Device"),
                external_message_id,
                idempotency_key,
                created_at,
            )
            saved_attachments = await save_message_attachments(conn, msg_id, attachments)
            preview = _message_preview(content, saved_attachments, "agent")
            await insert_chat_history_record(
                conn,
                convo,
                {
                    "id": msg_id,
                    "conversation_id": convo_id,
                    "content": content,
                    "sender_type": "agent",
                    "sender_name": "WhatsApp Linked Device",
                    "sender_id": str(metadata_payload.get("bridge_user_id") or ""),
                    "external_message_id": external_message_id,
                    "attachments": saved_attachments,
                    "created_at": created_at,
                    "company_id": company_id,
                },
            )
            await conn.execute(
                "UPDATE conversations SET last_message=$1,last_message_at=$2,updated_at=NOW(),message_count=message_count+1 "
                "WHERE id=$3 AND company_id=$4",
                preview,
                created_at,
                convo_id,
                company_id,
            )
            logger.info(
                "ai_auto_disable_skipped company_id=%s conversation_id=%s trigger=manual_message reason=manual_message_not_escalation actor_type=agent trigger_message_id=%s message_type=%s media_type=%s",
                company_id,
                convo_id,
                msg_id,
                "media" if saved_attachments else "text",
                str((saved_attachments[0] or {}).get("file_type") or (saved_attachments[0] or {}).get("type") or "").strip()
                if saved_attachments
                else "",
            )
    except Exception:
        logger.exception(
            "Outbound bridge message transaction failed company_id=%s channel=%s external_message_id=%s",
            company_id,
            channel,
            external_message_id,
        )
        return None

    identity_id = ""
    if channel == "whatsapp":
        identity_id = await _upsert_whatsapp_identity_aliases(
            db,
            company_id,
            metadata_payload,
            customer_id=cid,
            conversation_id=convo_id,
            channel_binding=channel_binding,
            sender_contact=target_contact,
            status="pending" if result.get("pending_identity") else "resolved",
            display_name=customer.get("name", sender_name or ""),
            profile_picture_url=str(metadata_payload.get("profile_picture_url") or customer.get("avatar") or ""),
        )
        await _persist_whatsapp_message_context(
            db,
            company_id=company_id,
            message_id=msg_id,
            conversation_id=convo_id,
            metadata_payload=metadata_payload,
            direction="outbound",
            source=str(metadata_payload.get("source") or "whatsapp_web_bridge"),
            identity_id=identity_id,
        )
        if result.get("pending_identity"):
            await _store_pending_whatsapp_message(
                db,
                company_id=company_id,
                metadata_payload=metadata_payload,
                direction="outbound",
                provider_event_id=external_message_id,
                raw_identity=channel_binding or target_contact,
                payload={"content": content, "attachments": saved_attachments},
                customer_id=cid,
                conversation_id=convo_id,
                message_id=msg_id,
            )

    if not is_group_message:
        try:
            await apply_message_stage_transition(
                db,
                company_id=company_id,
                customer_id=cid,
                message_text=content,
                direction="outbound",
                source="message_sent",
                event_id=external_message_id or msg_id,
                changed_by_user_id=str(metadata_payload.get("bridge_user_id") or ""),
            )
        except Exception as exc:
            logger.warning(
                "outbound bridge lead stage transition failed company_id=%s customer_id=%s message_id=%s: %s",
                company_id,
                cid,
                msg_id,
                exc,
            )

    try:
        from data_pipeline.ingestion.raw_store import capture_raw_message

        await capture_raw_message(
            db,
            conversation=dict(convo),
            message={
                "id": msg_id,
                "content": content,
                "sender_type": "agent",
                "company_id": company_id,
                "conversation_id": convo_id,
                "external_message_id": external_message_id,
                "attachments": saved_attachments,
            },
            source=channel,
            metadata={**metadata_payload, "source": metadata_payload.get("source") or "whatsapp_web_bridge"},
        )
    except Exception as exc:
        logger.warning(
            "outbound bridge raw message capture failed company_id=%s conversation_id=%s message_id=%s error=%s",
            company_id,
            convo_id,
            msg_id,
            exc,
        )

    outbound_message = await _load_message_with_attachments(db, msg_id)
    await emit_new_message(convo_id, outbound_message)
    return {
        "conversation_id": convo_id,
        "message_id": msg_id,
        "customer_id": cid,
        "customer_message": outbound_message,
        "duplicate": False,
    }


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
    if message.resolved_customer_id and not payload_metadata.get("resolved_customer_id_hint"):
        payload_metadata["resolved_customer_id_hint"] = str(message.resolved_customer_id or "").strip()
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
    if not payload_metadata.get("message_timestamp"):
        payload_metadata["message_timestamp"] = message.timestamp.isoformat()

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
    request_started_at = time.monotonic()
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
    is_group_message = channel == "whatsapp" and _is_whatsapp_group_metadata(metadata_payload)
    conversation_customer = dict(customer)
    conversation_customer_id = cid
    conversation_channel_binding = channel_binding
    message_sender_id = cid
    message_sender_name = (
        _clean_display_name(customer.get("name"))
        or _clean_display_name(sender_name)
        or "Unknown Contact"
    )
    if is_group_message:
        metadata_payload["group_participant_customer_id"] = cid
        metadata_payload["group_participant_name"] = message_sender_name
        metadata_payload["group_direct_lead_capture"] = True

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
    message_created_at = _coerce_provider_message_timestamp(
        metadata_payload.get("message_timestamp") or metadata_payload.get("timestamp")
    )
    support_plan: dict = {}
    history_message = {
        "id": msg_id,
        "conversation_id": "",
        "content": message_text,
        "sender_type": "customer",
        "sender_name": message_sender_name,
        "sender_id": message_sender_id,
        "external_message_id": inbound_external_message_id,
        "attachments": [],
        "created_at": message_created_at,
        "company_id": company_id,
    }
    await _ensure_messages_idempotency_schema(db)
    if channel == "whatsapp":
        await _ensure_whatsapp_identity_schema(db)
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
                logger.info(
                    "Inbound message accepted without usage increment because monthly conversation quota is exhausted company_id=%s channel=%s",
                    company_id,
                    channel,
                )
                metadata_payload["conversation_limit_exhausted"] = True
            if inbound_external_message_id and company_id:
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    usage_idempotency_key,
                )
                existing_inbound = r(
                    await conn.fetchrow(
                        "SELECT id,conversation_id FROM messages "
                        "WHERE company_id=$1 AND external_message_id=$2 "
                        "ORDER BY created_at DESC LIMIT 1",
                        company_id,
                        inbound_external_message_id,
                    )
                )
                if existing_inbound:
                    logger.info(
                        "Duplicate inbound event ignored after lock company_id=%s channel=%s external_message_id=%s message_id=%s",
                        company_id,
                        channel,
                        inbound_external_message_id,
                        existing_inbound.get("id", ""),
                    )
                    return {
                        "conversation_id": existing_inbound.get("conversation_id", ""),
                        "message_id": existing_inbound.get("id", ""),
                        "customer_id": cid,
                        "lead_id": result.get("lead_id", ""),
                        "customer_message": await _load_message_with_attachments(db, existing_inbound.get("id", "")),
                        "ai_message": None,
                        "sentiment_analysis": build_sentiment_gate(message_text, {}),
                        "duplicate": True,
                    }

            convo = r(
                await conn.fetchrow(
                    "SELECT * FROM conversations WHERE customer_id=$1 AND channel=$2 AND status=ANY($3) AND company_id=$4 "
                    "AND ($5='' OR channel_id=$5) "
                    "ORDER BY updated_at DESC LIMIT 1",
                    conversation_customer_id,
                    channel,
                    ["open", "pending", "escalated"],
                    company_id,
                    conversation_channel_binding if is_group_message else "",
                )
            )
            if not convo:
                convo_id = make_id()
                conversation_subject = _conversation_subject_from_identity(
                    channel,
                    _clean_display_name(conversation_customer.get("name")) or message_sender_name,
                    metadata_payload,
                    is_group_message=is_group_message,
                )
                # Match db_helpers get_or_create_contact_conversation: list columns through agent_type, then NOT NULL sentiment fields as bound parameters.
                await conn.execute(
                    "INSERT INTO conversations(id,company_id,customer_id,customer_name,customer_avatar,channel,subject,status,priority,assigned_to,assigned_name,ai_handled,agent_type,"  # noqa: E501
                    "channel_id,sentiment_score,sentiment_label,message_count,last_message,last_message_at,unread_count,session_id,page_url,created_at,updated_at) "  # noqa: E501
                    "VALUES($1,$2,$3,$4,$5,$6,$7,'open','medium',$8,$9,TRUE,'generic',$10,$11::numeric,$12,0,'',NOW(),0,'','',NOW(),NOW())",
                    convo_id,
                    company_id,
                    conversation_customer_id,
                    _clean_display_name(conversation_customer.get("name")) or message_sender_name,
                    conversation_customer.get("avatar", ""),
                    channel,
                    conversation_subject,
                    "",
                    "",
                    conversation_channel_binding,
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
            if conversation_channel_binding and convo.get("channel_id") != conversation_channel_binding:
                await conn.execute(
                    "UPDATE conversations SET channel_id=$1,updated_at=NOW() WHERE id=$2 AND company_id=$3",
                    conversation_channel_binding,
                    convo["id"],
                    company_id,
                )
                convo["channel_id"] = conversation_channel_binding
            if conversation_customer.get("avatar") and convo.get("customer_avatar") != conversation_customer.get("avatar"):
                await conn.execute(
                    "UPDATE conversations SET customer_avatar=$1,updated_at=NOW() WHERE id=$2 AND company_id=$3",
                    conversation_customer.get("avatar", ""),
                    convo["id"],
                    company_id,
                )
                convo["customer_avatar"] = conversation_customer.get("avatar", "")
            preferred_conversation_name = _clean_display_name(conversation_customer.get("name")) or message_sender_name
            preferred_subject = _conversation_subject_from_identity(
                channel,
                preferred_conversation_name,
                metadata_payload,
                is_group_message=is_group_message,
            )
            if preferred_conversation_name and _should_update_sender_name(
                channel,
                preferred_conversation_name,
                metadata_payload,
                str(convo.get("customer_name") or ""),
            ):
                await conn.execute(
                    "UPDATE conversations SET customer_name=$1,updated_at=NOW() WHERE id=$2 AND company_id=$3",
                    preferred_conversation_name,
                    convo["id"],
                    company_id,
                )
                convo["customer_name"] = preferred_conversation_name
            if preferred_subject and _is_generic_conversation_subject(str(convo.get("subject") or ""), channel):
                await conn.execute(
                    "UPDATE conversations SET subject=$1,updated_at=NOW() WHERE id=$2 AND company_id=$3",
                    preferred_subject,
                    convo["id"],
                    company_id,
                )
                convo["subject"] = preferred_subject

            convo_id = str(convo["id"])
            history_message["conversation_id"] = convo_id
            await conn.execute(
                "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,sentiment_score,"
                "sentiment_emotion,sentiment_confidence,intent_type,external_message_id,idempotency_key,read,created_at) "
                "VALUES($1,$2,$3,$4,'customer',$5,$6,$7,$8,$9,$10,$11,$12,FALSE,$13)",
                msg_id,
                company_id,
                convo_id,
                message_text,
                message_sender_id,
                message_sender_name,
                sent_score,
                sent_emotion,
                sent_conf,
                intent_type,
                inbound_external_message_id,
                usage_idempotency_key,
                message_created_at,
            )
            if billing_gate != "denied":
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
                "UPDATE conversations SET last_message=$1,last_message_at=$2,updated_at=NOW(),message_count=message_count+1,unread_count=unread_count+1 "  # noqa: E501
                "WHERE id=$3",
                _message_preview(message_text, saved_attachments, "customer"),
                message_created_at,
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

    logger.info(
        "Inbound message persisted company_id=%s user_id=%s source=%s conversation_id=%s customer_id=%s message_id=%s provider_message_id=%s raw_sender=%s normalized_sender=%s selected_recipient=%s saved=true",
        company_id,
        metadata_payload.get("bridge_user_id", ""),
        metadata_payload.get("source", channel),
        convo_id,
        cid,
        msg_id,
        inbound_external_message_id,
        metadata_payload.get("raw_sender_id", sender_contact),
        sender_contact,
        channel_binding,
    )
    logger.info(
        "ai_auto_disable_skipped company_id=%s conversation_id=%s trigger=customer_message reason=normal_customer_message actor_type=customer trigger_message_id=%s message_type=%s media_type=%s",
        company_id,
        convo_id,
        msg_id,
        "media" if saved_attachments else "text",
        str((saved_attachments[0] or {}).get("file_type") or (saved_attachments[0] or {}).get("type") or "").strip()
        if saved_attachments
        else "",
    )
    trace_id = _trace_id_from_context(str(metadata_payload.get("trace_id") or ""))
    if trace_id:
        metadata_payload["trace_id"] = trace_id
    customer_message = await _load_message_with_attachments(db, msg_id)
    await emit_new_message(convo_id, customer_message)

    if channel == "whatsapp":
        create_safe_detached_task(
            db,
            _persist_whatsapp_inbound_context_background(
                db,
                company_id=company_id,
                customer_id=cid,
                conversation_id=convo_id,
                message_id=msg_id,
                metadata_payload={
                    **metadata_payload,
                    "external_message_id": inbound_external_message_id,
                },
                conversation_channel_binding=conversation_channel_binding,
                sender_contact=sender_contact,
                display_name=_clean_display_name(conversation_customer.get("name")) or message_sender_name,
                profile_picture_url=str(metadata_payload.get("profile_picture_url") or conversation_customer.get("avatar") or ""),
                is_group_message=is_group_message,
            ),
            name=f"whatsapp-inbound-context-{msg_id}",
            company_id=company_id,
            channel=channel,
            trace_id=trace_id,
            event_id=inbound_external_message_id or msg_id,
        )

    create_safe_detached_task(
        db,
        _capture_raw_message_background(
            db,
            conversation=dict(convo),
            message=dict(history_message),
            source=str(convo.get("channel") or "message"),
            metadata={
                **metadata_payload,
                "normalized_identity": {
                    "channel": channel,
                    "sender_contact": sender_contact,
                    "channel_id": conversation_channel_binding,
                    "conversation_customer_id": conversation_customer_id,
                    "group_participant_customer_id": metadata_payload.get("group_participant_customer_id", ""),
                    "customer_phone": customer.get("phone", ""),
                    "customer_email": customer.get("email", ""),
                },
            },
        ),
        name=f"raw-message-capture-{msg_id}",
        company_id=company_id,
        channel=channel,
        trace_id=trace_id,
        event_id=inbound_external_message_id or msg_id,
    )

    create_safe_detached_task(
        db,
        _apply_inbound_stage_transition_background(
            db,
            company_id=company_id,
            lead_id=str(result.get("lead_id") or ""),
            customer_id=cid,
            message_text=message_text,
            event_id=inbound_external_message_id or msg_id,
        ),
        name=f"inbound-stage-transition-{msg_id}",
        company_id=company_id,
        channel=channel,
        trace_id=trace_id,
        event_id=inbound_external_message_id or msg_id,
    )

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

    if _metadata_truthy(metadata_payload.get("suppress_ai")):
        return {
            "conversation_id": convo_id,
            "message_id": msg_id,
            "customer_id": cid,
            "conversation_customer_id": conversation_customer_id,
            "lead_id": result.get("lead_id", ""),
            "customer_message": customer_message,
            "ai_message": None,
            "sentiment_analysis": sentiment_gate,
            "group_message": is_group_message,
        }

    limit_state = await conversation_limit_status(db, company_id)
    if not bool(limit_state.get("allowed", True)):
        notice_message = await _emit_conversation_limit_notice(
            db,
            company_id=company_id,
            conversation_id=convo_id,
            limit_state=limit_state,
        )
        return {
            "conversation_id": convo_id,
            "message_id": msg_id,
            "customer_id": cid,
            "conversation_customer_id": conversation_customer_id,
            "lead_id": result.get("lead_id", ""),
            "customer_message": customer_message,
            "ai_message": None,
            "notice_message": notice_message,
            "outgoing_blocked": True,
            "sentiment_analysis": {
                **sentiment_gate,
                "ai_response_allowed": False,
                "rate_limit_exhausted": True,
                "message": conversation_limit_completed_message(
                    int(limit_state.get("limit") or 0),
                    limit_state.get("used"),
                ),
            },
            "group_message": is_group_message,
        }

    try:
        context_started_at = time.monotonic()
        msgs_history = await fetch_messages_with_attachments(
            db,
            convo_id,
            limit=max(5, min(webhook_message_history_fetch_limit(), 12)),
            since_days=3,
            company_id=company_id,
            customer_id=cid,
            include_linked_profiles=True,
        )
        msgs_history = _truncate_history_for_token_budget(
            msgs_history,
            company_id=company_id,
            conversation_id=convo_id,
            channel=channel,
            trace_id=trace_id,
        )
        logger.info(
            "ai_latency_stage stage=context_build duration_ms=%s conversation_id=%s company_id=%s request_id=%s trace_id=%s",
            _elapsed_ms(context_started_at),
            convo_id,
            company_id,
            "",
            trace_id,
        )
        ai_generation_started_at = time.monotonic()
        import asyncio as _asyncio
        _workflow_coro_in = orchestrate_message_workflow(
            MessageWorkflowRequest(
                trace_id=trace_id,
                company_id=company_id,
                conversation_id=convo_id,
                customer_id=cid,
                lead_id=str(result.get("lead_id") or ""),
                message_id=msg_id,
                external_message_id=inbound_external_message_id,
                provider_event_id=inbound_external_message_id,
                idempotency_key=usage_idempotency_key,
                channel=channel,
                source=str(metadata_payload.get("source") or channel),
                message_text=message_text,
                sender_name=customer.get("name", "Unknown"),
                sender_contact=sender_contact,
                actor_user_id=str(metadata_payload.get("bridge_user_id") or metadata_payload.get("actor_user_id") or ""),
                conversation_context=msgs_history,
                customer=customer,
                lead=lead,
                metadata={
                    **metadata_payload,
                    "source": metadata_payload.get("source") or f"webhook_{channel}",
                    "trace_id": trace_id,
                    "message_id": msg_id,
                    "external_message_id": inbound_external_message_id,
                    "provider_event_id": inbound_external_message_id,
                    "idempotency_key": usage_idempotency_key,
                },
                suppress_response_generation=True,
            ),
            db=db,
        )
        _engine_coro_in = engine_run_turn(
            db,
            TurnRequest(
                session_id=convo_id,
                company_id=company_id,
                user_message=message_text,
                customer_id=cid,
                mode="reactive",
                extra_history=_format_msgs_as_dialogue(msgs_history),
            ),
        )
        _par = await _asyncio.gather(_workflow_coro_in, _engine_coro_in, return_exceptions=True)
        workflow = _par[0] if not isinstance(_par[0], BaseException) else None
        _engine_exc_in = _par[1] if isinstance(_par[1], BaseException) else None
        engine_result_in = _par[1] if not isinstance(_par[1], BaseException) else None
        logger.info(
            "ai_latency_stage stage=parallel_ai_call duration_ms=%s conversation_id=%s company_id=%s channel=%s trace_id=%s",
            _elapsed_ms(ai_generation_started_at), convo_id, company_id, channel, trace_id,
        )
        capture, _, support_output, _ = _extract_workflow_outputs(
            workflow or type("_W", (), {"agent_outputs": type("_O", (), {"capture": {}, "support": {}, "qualification": {}, "analytics": {}})()})(),
            context_label="process_incoming_message",
            company_id=company_id,
            conversation_id=convo_id,
            channel=channel,
            trace_id=trace_id,
        )
        support_plan = dict(support_output or {})
        try:
            if _engine_exc_in is not None:
                raise _engine_exc_in
            engine_result = engine_result_in
            support_plan = apply_engine_response_to_support_plan(
                support_plan=support_plan,
                capture=capture,
                engine_answer=engine_result.answer,
                engine_confidence=engine_result.confidence,
                engine_turn_id=engine_result.turn_id,
                engine_product_links=engine_result.product_links,
                engine_sources_used=engine_result.sources_used,
            )
        except Exception as engine_exc:
            logger.warning(
                "conversation_engine_failed company_id=%s conversation_id=%s "
                "channel=%s trace_id=%s error=%s",
                company_id, convo_id, channel, trace_id, engine_exc,
            )
            support_plan["api_error"] = True
            support_plan["error_reason"] = "conversation_engine_exception"
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
        conversation_score = _float_or_none((conversation_sentiment or {}).get("score"))
        conversation_label = str(
            (conversation_sentiment or {}).get("sentiment_label")
            or (conversation_sentiment or {}).get("label")
            or (conversation_sentiment or {}).get("emotion")
            or sent_emotion
            or "neutral"
        )
        create_safe_detached_task(
            db,
            _persist_workflow_annotations_background(
                db,
                company_id=company_id,
                conversation_id=convo_id,
                message_id=msg_id,
                sentiment_score=sent_score,
                sentiment_emotion=sent_emotion,
                sentiment_confidence=sent_conf,
                intent_type=intent_type,
                conversation_score=conversation_score,
                conversation_label=conversation_label,
                sentiment_gate=sentiment_gate,
            ),
            name=f"workflow-annotations-{msg_id}",
            company_id=company_id,
            channel=channel,
            trace_id=trace_id,
            event_id=inbound_external_message_id or msg_id,
        )
    except Exception as e:
        logger.error(f"Message orchestration failed: {e}")

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
        logger.info(
            "ai_auto_disable_skipped company_id=%s conversation_id=%s trigger=recent_manual_message reason=manual_message_not_escalation actor_type=agent trigger_message_id=%s message_type=%s media_type=%s",
            company_id,
            convo_id,
            recent_human_agent_message.get("id", ""),
            str(recent_human_agent_message.get("message_type") or "text"),
            str(recent_human_agent_message.get("media_type") or ""),
        )
    elif (
        convo.get("ai_handled", True)
        and not conversation_ai_auto_paused(convo)
        and await is_company_ai_enabled(db, company_id)
    ):
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
            if (
                support_result.get("api_error")
                and not support_result.get("static_fallback_served")
                and (is_ai_api_exhaustion_payload(support_result) or not support_result.get("response"))
            ):
                shutdown_idempotency_key = _build_ai_response_idempotency_key(
                    company_id=company_id,
                    conversation_id=convo_id,
                    inbound_message_id=msg_id,
                    inbound_external_message_id=inbound_external_message_id,
                    channel=channel,
                    workflow_type="shutdown",
                )
                await disable_company_ai_after_api_exhaustion(
                    db,
                    company_id,
                    conversation_id=convo_id,
                    reason=str(support_result.get("error_reason") or support_result.get("error_type") or "AI service unavailable"),
                    error_type=str(support_result.get("error_type") or ""),
                    provider=str(support_result.get("provider") or ""),
                    model=str(support_result.get("model_name") or ""),
                )
                escalation = await _escalate_conversation_to_human_once(
                    db,
                    company_id=company_id,
                    conversation_id=convo_id,
                    customer_name=customer.get("name", "Customer"),
                    channel=channel,
                    reason=AI_API_EXHAUSTED_MANUAL_MESSAGE,
                    automatic=True,
                    idempotency_key=shutdown_idempotency_key,
                    trace_id=trace_id,
                )
                if not escalation.get("duplicate"):
                    await emit_new_message(convo_id, escalation["message"])
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
                logger.info(
                    "ai_response_generated company_id=%s conversation_id=%s channel=%s auto_send=true workflow_id=%s message_id=%s",
                    company_id,
                    convo_id,
                    channel,
                    f"webhook_{channel}",
                    msg_id,
                )
                ai_id = make_id()
                ai_idempotency_key = _build_ai_response_idempotency_key(
                    company_id=company_id,
                    conversation_id=convo_id,
                    inbound_message_id=msg_id,
                    inbound_external_message_id=inbound_external_message_id,
                    channel=channel,
                    workflow_type="auto_response",
                )
                existing_ai_message = await _load_ai_message_by_idempotency(
                    db,
                    company_id=company_id,
                    idempotency_key=ai_idempotency_key,
                )
                if existing_ai_message:
                    logger.info(
                        "Skipping duplicate AI auto-response company_id=%s conversation_id=%s customer_id=%s inbound_message_id=%s external_message_id=%s ai_message_id=%s idempotency_key=%s",
                        company_id,
                        convo_id,
                        cid,
                        msg_id,
                        inbound_external_message_id,
                        existing_ai_message.get("id", ""),
                        ai_idempotency_key,
                    )
                    await auto_disable_ai_after_failure_fallback(
                        db,
                        company_id,
                        convo_id,
                        support_result,
                        channel=channel,
                        trace_id=trace_id,
                    )
                    ai_message = existing_ai_message
                    await _retry_existing_ai_outbound_if_needed(
                        db,
                        company_id=company_id,
                        channel=channel,
                        conversation_id=convo_id,
                        conversation=convo,
                        customer=customer,
                        sender_contact=sender_contact,
                        ai_message=ai_message,
                        idempotency_key=ai_idempotency_key,
                        trace_id=trace_id,
                        metadata_payload=metadata_payload,
                    )
                    return {
                        "conversation_id": convo_id,
                        "message_id": msg_id,
                        "customer_id": cid,
                        "lead_id": result["lead_id"],
                        "customer_message": customer_message,
                        "ai_message": ai_message,
                        "sentiment_analysis": sentiment_gate,
                    }
                # Send product images FIRST (before the text) when the user
                # explicitly asked to see images OR the AI response indicates
                # it is sending one. For outbound channels the image is sent
                # via the channel layer so WhatsApp/FB/IG delivers it natively.
                await _send_product_images(
                    db,
                    company_id=company_id,
                    conversation_id=convo_id,
                    channel=channel,
                    recipient_id=sender_contact,
                    product_links=list(support_result.get("product_links") or []),
                    user_message=message_text,
                    ai_response=str(support_result.get("response") or ""),
                    outbound_metadata={
                        **metadata_payload,
                        "source": f"webhook_{channel}_image",
                        "trace_id": trace_id,
                        "customer_id": str(cid or ""),
                    },
                )
                await _ensure_messages_idempotency_schema(db)
                persist_started_at = time.monotonic()
                logger.info(
                    "ai_response_persist_attempt company_id=%s conversation_id=%s channel=%s auto_send=true workflow_id=%s message_id=%s",
                    company_id,
                    convo_id,
                    channel,
                    f"webhook_{channel}",
                    ai_id,
                )
                try:
                    await db.execute(
                        "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,ai_confidence,idempotency_key,delivery_status,read,created_at) "  # noqa: E501
                        "VALUES($1,$2,$3,$4,'ai','ai-assistant','AI Assistant',$5,$6,'sending',FALSE,NOW())",
                        ai_id,
                        company_id,
                        convo_id,
                        support_result["response"],
                        float(support_result.get("confidence", 0.0) or 0.0),
                        ai_idempotency_key,
                    )
                except Exception:
                    existing_ai_message = await _load_ai_message_by_idempotency(
                        db,
                        company_id=company_id,
                        idempotency_key=ai_idempotency_key,
                    )
                    if existing_ai_message:
                        logger.info(
                            "AI auto-response insert raced with existing row company_id=%s conversation_id=%s ai_message_id=%s idempotency_key=%s",
                            company_id,
                            convo_id,
                            existing_ai_message.get("id", ""),
                            ai_idempotency_key,
                        )
                        await auto_disable_ai_after_failure_fallback(
                            db,
                            company_id,
                            convo_id,
                            support_result,
                            channel=channel,
                            trace_id=trace_id,
                        )
                        ai_message = existing_ai_message
                        await _retry_existing_ai_outbound_if_needed(
                            db,
                            company_id=company_id,
                            channel=channel,
                            conversation_id=convo_id,
                            conversation=convo,
                            customer=customer,
                            sender_contact=sender_contact,
                            ai_message=ai_message,
                            idempotency_key=ai_idempotency_key,
                            trace_id=trace_id,
                            metadata_payload=metadata_payload,
                        )
                        return {
                            "conversation_id": convo_id,
                            "message_id": msg_id,
                            "customer_id": cid,
                            "lead_id": result["lead_id"],
                            "customer_message": customer_message,
                            "ai_message": ai_message,
                            "sentiment_analysis": sentiment_gate,
                        }
                    raise
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
                ai_history_message = {
                    "id": ai_id,
                    "company_id": company_id,
                    "conversation_id": convo_id,
                    "content": support_result["response"],
                    "sender_type": "ai",
                    "sender_name": "AI Assistant",
                    "attachments": ai_attachments,
                    "created_at": now_ts(),
                }
                create_safe_detached_task(
                    db,
                    _persist_ai_chat_history_background(
                        db,
                        conversation=dict(convo),
                        message=ai_history_message,
                    ),
                    name=f"persist-ai-chat-history-{ai_id}",
                    company_id=company_id,
                    channel=channel,
                    trace_id=trace_id,
                    event_id=ai_id,
                )
                await db.execute(
                    "UPDATE conversations SET last_message=$1,last_message_at=NOW(),message_count=message_count+1 WHERE id=$2",  # noqa: E501
                    _message_preview(support_result["response"], ai_attachments, "ai"),
                    convo_id,
                )
                logger.info(
                    "ai_response_persisted company_id=%s conversation_id=%s channel=%s auto_send=true workflow_id=%s message_id=%s",
                    company_id,
                    convo_id,
                    channel,
                    f"webhook_{channel}",
                    ai_id,
                )
                logger.info(
                    "ai_latency_stage stage=persistence duration_ms=%s conversation_id=%s company_id=%s request_id=%s trace_id=%s",
                    _elapsed_ms(persist_started_at),
                    convo_id,
                    company_id,
                    "",
                    trace_id,
                )
                await auto_disable_ai_after_failure_fallback(
                    db,
                    company_id,
                    convo_id,
                    support_result,
                    channel=channel,
                    trace_id=trace_id,
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
                recipient_id = _resolve_outbound_recipient(
                    channel,
                    convo,
                    customer,
                    sender_contact,
                )
                if recipient_id:
                    outbound_started_at = time.monotonic()
                    logger.info(
                        "ai_outbound_send_attempt company_id=%s conversation_id=%s channel=%s provider=%s auto_send=true workflow_id=%s message_id=%s",
                        company_id,
                        convo_id,
                        channel,
                        channel,
                        f"webhook_{channel}",
                        ai_id,
                    )
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
                            "customer_id": str(cid or ""),
                            "conversation_id": convo_id,
                            "actor_user_id": str(
                                metadata_payload.get("bridge_user_id")
                                or metadata_payload.get("actor_user_id")
                                or metadata_payload.get("user_id")
                                or ""
                            ),
                            "bridge_scope": str(metadata_payload.get("bridge_scope") or ""),
                            "bridge_user_id": str(metadata_payload.get("bridge_user_id") or ""),
                            "idempotency_key": ai_idempotency_key,
                            "raw_sender_id": str(sender_contact or ""),
                            "normalized_sender_id": recipient_id,
                            "selected_outbound_recipient": recipient_id,
                        },
                    )
                    logger.info(
                        "Outbound AI send completed company_id=%s conversation_id=%s channel=%s message_id=%s trace_id=%s sent=%s elapsed_ms=%s",
                        company_id,
                        convo_id,
                        channel,
                        ai_id,
                        trace_id,
                        sent,
                        _elapsed_ms(outbound_started_at),
                    )
                    if not sent:
                        logger.warning(
                            "ai_outbound_send_failed company_id=%s conversation_id=%s channel=%s provider=%s auto_send=true workflow_id=%s message_id=%s error=%s",
                            company_id,
                            convo_id,
                            channel,
                            channel,
                            f"webhook_{channel}",
                            ai_id,
                            error,
                        )
                    else:
                        logger.info(
                            "ai_outbound_send_success company_id=%s conversation_id=%s channel=%s provider=%s auto_send=true workflow_id=%s message_id=%s",
                            company_id,
                            convo_id,
                            channel,
                            channel,
                            f"webhook_{channel}",
                            ai_id,
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
                        "ai_outbound_send_skipped company_id=%s conversation_id=%s channel=%s provider=%s auto_send=true workflow_id=%s message_id=%s skip_reason=missing_outbound_recipient trace_id=%s",
                        company_id,
                        convo_id,
                        channel,
                        channel,
                        f"webhook_{channel}",
                        ai_id,
                        trace_id,
                    )
                ai_message = await _load_message_with_attachments(db, ai_id)
                await emit_new_message(convo_id, ai_message)
                create_safe_detached_task(
                    db,
                    _update_qualification_silently(
                        db,
                        company_id,
                        lead,
                        message_text,
                        intent,
                    ),
                    name=f"qualification-update-{convo_id}",
                    company_id=company_id,
                    channel=channel,
                    trace_id=trace_id,
                    event_id=ai_id,
                )
                logger.info(
                    "ai_auto_disable_skipped company_id=%s conversation_id=%s trigger=ai_success reason=ai_success actor_type=ai trigger_message_id=%s message_type=%s media_type=%s",
                    company_id,
                    convo_id,
                    ai_id,
                    "media" if ai_attachments else "text",
                    str((ai_attachments[0] or {}).get("file_type") or (ai_attachments[0] or {}).get("type") or "").strip()
                    if ai_attachments
                    else "",
                )
                logger.info(
                    "ai_latency_stage stage=total duration_ms=%s conversation_id=%s company_id=%s request_id=%s trace_id=%s",
                    _elapsed_ms(request_started_at),
                    convo_id,
                    company_id,
                    "",
                    trace_id,
                )
            else:
                skip_reason = (
                    "no_response_text"
                    if not str(support_result.get("response") or "").strip()
                    else "ai_disabled"
                    if support_result.get("escalate")
                    else "outbound_disabled"
                )
                logger.warning(
                    "ai_outbound_send_skipped company_id=%s conversation_id=%s channel=%s provider=%s auto_send=true workflow_id=%s message_id=%s skip_reason=%s",
                    company_id,
                    convo_id,
                    channel,
                    channel,
                    f"webhook_{channel}",
                    msg_id,
                    skip_reason,
                )
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
    received_at = time.monotonic()
    raw_body = await request.body()
    event_id = await _verify_meta_webhook_request(request, db, "whatsapp", raw_body)
    payload = _decode_webhook_json(raw_body)
    bridge_secret = (os.environ.get("WHATSAPP_BRIDGE_SECRET") or os.environ.get("BRIDGE_SECRET") or "").strip()
    provided_bridge_secret = (request.headers.get("X-Bridge-Secret") or "").strip()
    trusted_bridge = bool(
        bridge_secret and provided_bridge_secret and hmac.compare_digest(provided_bridge_secret, bridge_secret)
    )
    logger.info(
        "WhatsApp webhook received event_id=%s bytes=%s trusted_bridge=%s receive_ms=%s",
        event_id,
        len(raw_body or b""),
        trusted_bridge,
        _elapsed_ms(received_at),
    )
    if trusted_bridge and _is_whatsapp_web_bridge_payload(payload):
        return_results = _is_truthy(request.headers.get("X-Bridge-Return-Results"))
        if return_results:
            result = await _handle_whatsapp_webhook_payload(
                db,
                payload,
                event_id=event_id,
                allow_direct_company_id=True,
                return_results=True,
            )
            return {"status": "processed", "source": "whatsapp_web_bridge", **dict(result or {})}
        create_safe_detached_task(
            db,
            _handle_whatsapp_webhook_payload(
                db,
                payload,
                event_id=event_id,
                allow_direct_company_id=True,
            ),
            name="webhook-whatsapp-bridge",
            channel="whatsapp",
            event_id=event_id,
            payload=payload,
            service_label=provider_webhook_queue_label("qr"),
        )
        logger.info(
            "WhatsApp bridge webhook queued company_id=%s user_id=%s event_id=%s",
            request.headers.get("X-Bridge-Company-Id", ""),
            request.headers.get("X-Bridge-User-Id", ""),
            event_id,
        )
        return {"status": "received", "source": "whatsapp_web_bridge", "processed": False, "queued": True}
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
        service_label=provider_webhook_queue_label("meta"),
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
        service_label=provider_webhook_queue_label("meta"),
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
        service_label=provider_webhook_queue_label("meta"),
    )
    return {"status": "received"}


@router.post("/webhooks/web-chat")
async def web_chat_webhook(request: Request):
    db = _db(request)
    request_started_at = time.monotonic()
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
            await apply_message_stage_transition(
                db,
                company_id=company_id,
                lead_id=str((lead_capture or {}).get("lead_id") or ""),
                customer_id=customer_id,
                message_text=content,
                direction="inbound",
                source="customer_reply",
                event_id=msg_id,
            )
        except Exception as exc:
            logger.warning(
                "web chat lead stage transition failed company_id=%s lead_id=%s message_id=%s: %s",
                company_id,
                (lead_capture or {}).get("lead_id", ""),
                msg_id,
                exc,
            )
        usage_key_seed = str(payload.get("client_message_id") or "").strip() or msg_id
        usage_result = await reserve_conversation_usage(
            db,
            company_id,
            channel="web_chat",
            idempotency_key=f"webchat:{company_id}:{session_id}:{usage_key_seed}",
        )
        limit_state = await conversation_limit_status(db, company_id)
        if usage_result == "denied" or not bool(limit_state.get("allowed", True)):
            customer_message = await _load_message_with_attachments(db, msg_id)
            await emit_new_message(convo_id, customer_message)
            notice_message = await _emit_conversation_limit_notice(
                db,
                company_id=company_id,
                conversation_id=convo_id,
                limit_state=limit_state,
            )
            return {
                "status": "received_outgoing_blocked",
                "conversation_id": convo_id,
                "customer_message": customer_message,
                "ai_message": None,
                "notice_message": notice_message,
                "outgoing_blocked": True,
                "rate_limit_message": conversation_limit_completed_message(
                    int(limit_state.get("limit") or 0),
                    limit_state.get("used"),
                ),
                "is_ai": False,
            }
        try:
            context_started_at = time.monotonic()
            msgs_history = await fetch_messages_with_attachments(
                db,
                convo_id,
                limit=max(5, min(webhook_message_history_fetch_limit(), 12)),
                since_days=3,
                company_id=company_id,
                customer_id=customer_id,
                include_linked_profiles=True,
            )
            msgs_history = _truncate_history_for_token_budget(
                msgs_history,
                company_id=company_id,
                conversation_id=convo_id,
                channel="web_chat",
                trace_id=trace_id,
            )
            logger.info(
                "ai_latency_stage stage=context_build duration_ms=%s conversation_id=%s company_id=%s request_id=%s trace_id=%s",
                _elapsed_ms(context_started_at),
                convo_id,
                company_id,
                "",
                trace_id,
            )
            ai_generation_started_at = time.monotonic()
            # ── FAST PATH ─────────────────────────────────────────────────────
            # Run the conversation engine FIRST (blocks until the AI reply is
            # ready), then fire the legacy orchestrator as a background task so
            # sentiment/intent metadata is captured without blocking the response.
            # This cuts the critical path to ~engine time only (~1-1.5 s on
            # gemini-2.5-flash-lite) instead of max(orchestrator, engine).
            import asyncio as _asyncio

            async def _fire_orchestrator_background():
                """Run sentiment/intent capture asynchronously after reply is sent."""
                try:
                    await _asyncio.wait_for(
                        orchestrate_message_workflow(
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
                                suppress_response_generation=True,
                            ),
                            db=db,
                        ),
                        timeout=3.0,
                    )
                except Exception:
                    pass

            # Immediately start the engine — the AI reply is ready when this awaits
            engine_result = None
            _engine_exc = None
            try:
                engine_result = await engine_run_turn(
                    db,
                    TurnRequest(
                        session_id=convo_id,
                        company_id=company_id,
                        user_message=content,
                        customer_id=customer_id,
                        mode="reactive",
                        extra_history=_format_msgs_as_dialogue(msgs_history),
                    ),
                )
            except Exception as _exc:
                _engine_exc = _exc

            # Fire orchestrator in the background — does not block the response
            workflow = None
            create_safe_detached_task(
                db,
                _fire_orchestrator_background(),
                name=f"orchestrator-bg-{convo_id}",
                company_id=company_id,
                channel="web_chat",
                trace_id=trace_id,
                event_id=msg_id,
            )
            logger.info(
                "ai_latency_stage stage=engine_only duration_ms=%s conversation_id=%s company_id=%s trace_id=%s",
                _elapsed_ms(ai_generation_started_at),
                convo_id,
                company_id,
                trace_id,
            )
            capture, _, support_output, _ = _extract_workflow_outputs(
                workflow or type("_W", (), {"agent_outputs": type("_O", (), {"capture": {}, "support": {}, "qualification": {}, "analytics": {}})()})(),
                context_label="web_chat_webhook",
                company_id=company_id,
                conversation_id=convo_id,
                channel="web_chat",
                trace_id=trace_id,
            )
            support_plan = dict(support_output or {})
            try:
                if _engine_exc is not None:
                    raise _engine_exc
                support_plan = apply_engine_response_to_support_plan(
                    support_plan=support_plan,
                    capture=capture,
                    engine_answer=engine_result.answer,
                    engine_confidence=engine_result.confidence,
                    engine_turn_id=engine_result.turn_id,
                    engine_product_links=engine_result.product_links,
                    engine_sources_used=engine_result.sources_used,
                )
                logger.info(
                    "ENGINE_RESPONSE company_id=%s conversation_id=%s turn_id=%s "
                    "confidence=%.2f answer_len=%d sources=%s product_links=%d trace_id=%s",
                    company_id,
                    convo_id,
                    engine_result.turn_id,
                    engine_result.confidence,
                    len(engine_result.answer or ""),
                    list(engine_result.sources_used or []),
                    len(list(engine_result.product_links or [])),
                    trace_id,
                )
                logger.info(
                    "SUPPORT_PLAN_PRODUCTS company_id=%s conversation_id=%s "
                    "deliver=%s product_links=%s trace_id=%s",
                    company_id,
                    convo_id,
                    support_plan.get("deliver_response"),
                    support_plan.get("product_links"),
                    trace_id,
                )
                logger.info(
                    "ai_latency_stage stage=engine_call duration_ms=%s conversation_id=%s "
                    "company_id=%s turn_id=%s trace_id=%s",
                    _elapsed_ms(ai_generation_started_at),
                    convo_id,
                    company_id,
                    engine_result.turn_id,
                    trace_id,
                )
            except Exception as engine_exc:
                # Engine failure should never silently drop the reply. Mark
                # the support_plan as an API error so the existing fallback
                # path (api_error -> escalate, AI shutdown) takes over.
                logger.warning(
                    "conversation_engine_failed company_id=%s conversation_id=%s "
                    "trace_id=%s error=%s",
                    company_id, convo_id, trace_id, engine_exc,
                )
                support_plan["api_error"] = True
                support_plan["error_reason"] = "conversation_engine_exception"
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
        sent_image_urls: list[str] = []

        # If this session has a proactive follow-up in flight, route the reply
        # through on_customer_reply. The handler persists feedback / opt-out /
        # upsell scheduling and we send back a short canned ack instead of
        # invoking the full AI generation path.
        followup_outcome = await maybe_handle_followup_reply(
            db, company_id=company_id, session_id=session_id, text=content
        )
        if followup_outcome is not None:
            ack_text = str(followup_outcome.get("ack_message") or "")
            ai_id = make_id()
            await db.execute(
                "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,ai_confidence,delivery_status,read,created_at) "  # noqa: E501
                "VALUES($1,$2,$3,$4,'ai','ai-assistant','AI Assistant',$5,'sent',FALSE,NOW())",
                ai_id, company_id, convo_id, ack_text, 1.0,
            )
            await db.execute(
                "UPDATE conversations SET last_message=$1,last_message_at=NOW(),message_count=message_count+1 WHERE id=$2",  # noqa: E501
                _message_preview(ack_text, [], "ai"),
                convo_id,
            )
            ai_message = await _load_message_with_attachments(db, ai_id)
            await emit_new_message(convo_id, ai_message)
            logger.info(
                "followup_reply_handled company_id=%s session_id=%s followup_id=%s action=%s sentiment=%s schedule_upsell=%s",
                company_id, session_id,
                followup_outcome.get("followup_id", ""),
                followup_outcome.get("action", ""),
                followup_outcome.get("sentiment", ""),
                followup_outcome.get("schedule_upsell", False),
            )
            return {
                "status": "ok",
                "conversation_id": convo_id,
                "customer_message": customer_message,
                "ai_message": ai_message,
                "response": ack_text,
                "is_ai": True,
                "followup_action": followup_outcome.get("action"),
            }

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
            logger.info(
                "ai_auto_disable_skipped company_id=%s conversation_id=%s trigger=recent_manual_message reason=manual_message_not_escalation actor_type=agent trigger_message_id=%s message_type=%s media_type=%s",
                company_id,
                convo_id,
                recent_human_agent_message.get("id", ""),
                str(recent_human_agent_message.get("message_type") or "text"),
                str(recent_human_agent_message.get("media_type") or ""),
            )
        elif (
            convo.get("ai_handled", True)
            and not conversation_ai_auto_paused(convo)
            and await is_company_ai_enabled(db, company_id)
        ):
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
                if (
                    support_result.get("api_error")
                    and not support_result.get("static_fallback_served")
                    and (is_ai_api_exhaustion_payload(support_result) or not support_result.get("response"))
                ):
                    shutdown_idempotency_key = _build_ai_response_idempotency_key(
                        company_id=company_id,
                        conversation_id=convo_id,
                        inbound_message_id=msg_id,
                        inbound_external_message_id="",
                        channel="web_chat",
                        workflow_type="shutdown",
                    )
                    await disable_company_ai_after_api_exhaustion(
                        db,
                        company_id,
                        conversation_id=convo_id,
                        reason=str(support_result.get("error_reason") or support_result.get("error_type") or "AI service unavailable"),
                        error_type=str(support_result.get("error_type") or ""),
                        provider=str(support_result.get("provider") or ""),
                        model=str(support_result.get("model_name") or ""),
                    )
                    escalation = await _escalate_conversation_to_human_once(
                        db,
                        company_id=company_id,
                        conversation_id=convo_id,
                        customer_name=customer.get("name", "Customer"),
                        channel="web_chat",
                        reason=AI_API_EXHAUSTED_MANUAL_MESSAGE,
                        automatic=True,
                        idempotency_key=shutdown_idempotency_key,
                        trace_id=trace_id,
                    )
                    if not escalation.get("duplicate"):
                        await emit_new_message(convo_id, escalation["message"])
                    return {
                        "status": "ok",
                        "conversation_id": convo_id,
                        "customer_message": customer_message,
                        "ai_message": None,
                        "response": AI_API_EXHAUSTED_MANUAL_MESSAGE,
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
                    # Send product images FIRST (before the text response) when
                    # the user asked for images OR the AI response says so.
                    sent_image_urls = await _send_product_images(
                        db,
                        company_id=company_id,
                        conversation_id=convo_id,
                        channel="web_chat",
                        recipient_id="",  # web_chat has no external recipient_id
                        product_links=list(support_result.get("product_links") or []),
                        user_message=content,
                        ai_response=str(support_result.get("response") or ""),
                        outbound_metadata={"trace_id": trace_id},
                    )
                    logger.info(
                        "OUTBOUND_RENDER company_id=%s conversation_id=%s channel=web_chat "
                        "response_source=%s response_len=%d product_links=%d image_sends=%d trace_id=%s",
                        company_id,
                        convo_id,
                        "conversation_engine" if support_result.get("engine_turn_id") else "legacy",
                        len(str(support_result.get("response") or "")),
                        len(list(support_result.get("product_links") or [])),
                        len(sent_image_urls),
                        trace_id,
                    )
                    logger.info(
                        "ai_response_generated company_id=%s conversation_id=%s channel=web_chat auto_send=true workflow_id=webhook_web_chat message_id=%s",
                        company_id,
                        convo_id,
                        msg_id,
                    )
                    ai_id = make_id()
                    persist_started_at = time.monotonic()
                    logger.info(
                        "ai_response_persist_attempt company_id=%s conversation_id=%s channel=web_chat auto_send=true workflow_id=webhook_web_chat message_id=%s",
                        company_id,
                        convo_id,
                        ai_id,
                    )
                    # When the engine produced the response, surface its
                    # sources / turn_id / product_links into raw_metadata so
                    # the InboxPage can render a "sources used" footer
                    # without joining ai_conversation_turns at read time.
                    engine_metadata = {
                        "engine_turn_id": str(support_result.get("engine_turn_id") or ""),
                        "sources_used": list(support_result.get("sources_used") or []),
                        "product_links": list(support_result.get("product_links") or []),
                    } if support_result.get("engine_turn_id") else {}
                    await db.execute(
                        "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,ai_confidence,delivery_status,read,raw_metadata,created_at) "  # noqa: E501
                        "VALUES($1,$2,$3,$4,'ai','ai-assistant','AI Assistant',$5,'sending',FALSE,$6::jsonb,NOW())",
                        ai_id,
                        company_id,
                        convo_id,
                        support_result["response"],
                        float(support_result.get("confidence", 0.0) or 0.0),
                        json.dumps(engine_metadata) if engine_metadata else "{}",
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
                    logger.info(
                        "ai_response_persisted company_id=%s conversation_id=%s channel=web_chat auto_send=true workflow_id=webhook_web_chat message_id=%s",
                        company_id,
                        convo_id,
                        ai_id,
                    )
                    logger.info(
                        "ai_latency_stage stage=persistence duration_ms=%s conversation_id=%s company_id=%s request_id=%s trace_id=%s",
                        _elapsed_ms(persist_started_at),
                        convo_id,
                        company_id,
                        "",
                        trace_id,
                    )
                    await auto_disable_ai_after_failure_fallback(
                        db,
                        company_id,
                        convo_id,
                        support_result,
                        channel="web_chat",
                        trace_id=trace_id,
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
                    outbound_started_at = time.monotonic()
                    logger.info(
                        "FINAL_SEND_PAYLOAD company_id=%s conversation_id=%s message_id=%s "
                        "sender_type=%s content_len=%d attachments=%d "
                        "engine_turn_id=%s product_links=%d trace_id=%s",
                        company_id,
                        convo_id,
                        ai_id,
                        (ai_message or {}).get("sender_type", "ai"),
                        len(str((ai_message or {}).get("content") or "")),
                        len(list((ai_message or {}).get("attachments") or [])),
                        support_result.get("engine_turn_id", ""),
                        len(list(support_result.get("product_links") or [])),
                        trace_id,
                    )
                    logger.info(
                        "ai_outbound_send_attempt company_id=%s conversation_id=%s channel=web_chat provider=web_chat auto_send=true workflow_id=webhook_web_chat message_id=%s",
                        company_id,
                        convo_id,
                        ai_id,
                    )
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
                            "ai_outbound_send_failed company_id=%s conversation_id=%s channel=web_chat provider=web_chat auto_send=true workflow_id=webhook_web_chat message_id=%s error=%s",
                            company_id,
                            convo_id,
                            ai_id,
                            error,
                        )
                        await emit_new_message(convo_id, ai_message)
                    else:
                        logger.info(
                            "ai_outbound_send_success company_id=%s conversation_id=%s channel=web_chat provider=web_chat auto_send=true workflow_id=webhook_web_chat message_id=%s",
                            company_id,
                            convo_id,
                            ai_id,
                        )
                    logger.info(
                        "ai_latency_stage stage=outbound_send duration_ms=%s conversation_id=%s company_id=%s request_id=%s trace_id=%s",
                        _elapsed_ms(outbound_started_at),
                        convo_id,
                        company_id,
                        "",
                        trace_id,
                    )
                    ai_response_text = support_result["response"]
                    is_ai = True
                    create_safe_detached_task(
                        db,
                        _update_qualification_silently(
                            db,
                            company_id,
                            lead,
                            content,
                            intent,
                        ),
                        name=f"qualification-update-{convo_id}",
                        company_id=company_id,
                        channel="web_chat",
                        trace_id=trace_id,
                        event_id=ai_id,
                    )
                    logger.info(
                        "ai_auto_disable_skipped company_id=%s conversation_id=%s trigger=ai_success reason=ai_success actor_type=ai trigger_message_id=%s message_type=%s media_type=%s",
                        company_id,
                        convo_id,
                        ai_id,
                        "media" if ai_attachments else "text",
                        str((ai_attachments[0] or {}).get("file_type") or (ai_attachments[0] or {}).get("type") or "").strip()
                        if ai_attachments
                        else "",
                    )
                    logger.info(
                        "ai_latency_stage stage=total duration_ms=%s conversation_id=%s company_id=%s request_id=%s trace_id=%s",
                        _elapsed_ms(request_started_at),
                        convo_id,
                        company_id,
                        "",
                        trace_id,
                    )
                else:
                    skip_reason = (
                        "no_response_text"
                        if not str(support_result.get("response") or "").strip()
                        else "ai_disabled"
                        if support_result.get("escalate")
                        else "outbound_disabled"
                    )
                    logger.warning(
                        "ai_outbound_send_skipped company_id=%s conversation_id=%s channel=web_chat provider=web_chat auto_send=true workflow_id=webhook_web_chat message_id=%s skip_reason=%s",
                        company_id,
                        convo_id,
                        msg_id,
                        skip_reason,
                    )
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
        company_link = ""
        try:
            cs_row = await db.fetchrow(
                "SELECT website_address FROM company_settings WHERE company_id=$1 LIMIT 1",
                company_id,
            )
            if cs_row:
                company_link = str((dict(cs_row) if cs_row else {}).get("website_address") or "").strip()
        except Exception:
            pass
        return {
            "status": "ok",
            "conversation_id": convo_id,
            "customer_message": customer_message,
            "ai_message": ai_message,
            "response": ai_response_text or "Thanks for your message! A team member will respond shortly.",
            "is_ai": is_ai,
            "product_links": list(support_plan.get("product_links") or []),
            "company_link": company_link,
            # Absolute URLs of product images sent as individual messages before
            # the text response. The ChatWidget uses these to render image bubbles.
            "product_image_urls": sent_image_urls,
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
        service_label=provider_webhook_queue_label("meta"),
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
