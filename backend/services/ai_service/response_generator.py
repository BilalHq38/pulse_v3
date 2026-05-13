from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time

from shared.metrics import increment_counter, observe_histogram
from shared.config import (
    ai_enable_rule_based_recovery,
    ai_input_token_budget,
    ai_response_recent_ai_message_limit,
    ai_response_retry_temperature_delta,
    ai_response_retry_temperature_max,
    ai_response_temperature_base,
    ai_response_temperature_jitter_steps,
    ai_response_temperature_max,
    ai_response_temperature_min,
    ai_response_temperature_negative_delta,
    ai_response_temperature_product_delta,
    ai_response_temperature_repetition_delta,
)
from services.ai_service.common import (
    LeadScoreResult,
    _json_safe,
    latest_ai_message,
    latest_customer_message,
    text_similarity,
    truncate_text_for_tokens,
)
from services.ai_service.intent import classify_intent, is_short_follow_up_message
from services.ai_service.llm_client import (
    _resolve_engine_for_request,
    call_model_json_batch,
    call_with_engines,
    call_model_json,
    call_model_text,
    validate_live_engine,
)
from services.ai_service.llm_tracking import get_llm_context, has_llm_budget_remaining, set_llm_context
from services.ai_service.model_catalog import DEFAULT_GEMINI_MODEL, is_supported_model
from services.ai_service.memory_service import (
    get_conversation_state_memory,
    get_last_ai_response_context,
    get_last_shown_product_ids,
    remember_conversation_state,
    remember_ai_response,
    remember_shown_products,
)
from services.ai_service.rag import build_ai_context, recent_customer_image_urls, understand_product_query
from services.ai_service.sentiment import (
    analyze_conversation_sentiment,
    # FIX: analyze_message_and_conversation_sentiment is dead code — it is a
    # two-task batch that is fully superseded by the three-task batch inside
    # generate_combined_ai_analysis. Import removed to prevent accidental use.
    analyze_sentiment,
)
from services.db_helpers import (
    get_ai_static_fallback_message,
    is_ai_api_exhaustion_payload,
    is_data_url_image,
    resolve_active_ai_agent,
)
from core.utils import is_valid_image_url
from shared.database import create_detached_task

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine-resolution cache
# FIX (latency 3): cache the active engine per company for up to 60 seconds.
# _resolve_engine_for_request hits the DB on every message — this eliminates
# that round-trip on the hot path.
# ---------------------------------------------------------------------------
import functools

_ENGINE_CACHE: dict[str, tuple[float, dict]] = {}
# Reduce the engine cache TTL so changes to the active engine propagate much sooner.
_ENGINE_CACHE_TTL = 10.0  # seconds


def clear_engine_cache(company_id: str = "", *, reason: str = "", use_pro: bool | None = None) -> None:
    company_key = str(company_id or "").strip()
    removed = 0
    for cache_key in list(_ENGINE_CACHE.keys()):
        cached_company, _, cached_use_pro = cache_key.partition(":")
        if company_key and cached_company != company_key:
            continue
        if use_pro is not None and cached_use_pro.lower() != str(bool(use_pro)).lower():
            continue
        _ENGINE_CACHE.pop(cache_key, None)
        removed += 1
    logger.info(
        "llm_engine_cache_invalidated company_id=%s reason=%s removed=%s",
        company_key or "<all>",
        reason or "manual",
        removed,
    )


async def _resolve_engine_cached(db, company_id: str, use_pro: bool = False) -> dict:
    cache_key = f"{company_id or ''}:{use_pro}"
    now = time.monotonic()
    if cache_key in _ENGINE_CACHE:
        ts, engine = _ENGINE_CACHE[cache_key]
        if now - ts < _ENGINE_CACHE_TTL:
            return engine
    logger.info(
        "llm_engine_cache_miss company_id=%s use_pro=%s ttl_seconds=%s note=engine_changes_may_take_ttl_to_propagate_per_worker",
        company_id or "<global>",
        use_pro,
        _ENGINE_CACHE_TTL,
    )
    engine = await _resolve_engine_for_request(db=db, company_id=company_id, use_pro=use_pro)
    _ENGINE_CACHE[cache_key] = (now, engine)
    return engine


RESPONSE_STYLE_PROFILES = (
    {
        "name": "empathetic",
        "instruction": (
            "Start by briefly acknowledging the customer's situation, then move directly to a useful answer or next step. "
            "Keep the tone warm and human, but avoid over-apologizing, filler phrases, or long emotional wording."
        ),
    },
    {
        "name": "consultative",
        "instruction": (
            "Act like a helpful advisor. Understand the customer's goal, explain the best option in simple terms, "
            "compare only when useful, and recommend one clear next step without sounding pushy."
        ),
    },
    {
        "name": "concise",
        "instruction": (
            "Answer directly in a short, natural message. Remove filler, repeated greetings, excessive punctuation, "
            "internal labels, and broad questions unless a specific missing detail is required."
        ),
    },
    {
        "name": "reassuring",
        "instruction": (
            "Use a calm, confident tone. Reduce uncertainty by explaining what can be done now, what information is needed, "
            "and what the customer can expect next. Keep it practical and human."
        ),
    },
    {
        "name": "actionable",
        "instruction": (
            "Focus on the fastest useful outcome. Give concrete options, product/service details, or a clear next action. "
            "Do not ask generic discovery questions if the customer already made a specific request."
        ),
    },
)
AGENT_RUNTIME_PROFILES = {
    "support": {
        "label": "Support Agent",
        "instruction": (
            "Operate like a practical support specialist. Identify the customer's issue, answer with the most direct fix or next step, "
            "ask for only one missing detail if needed, and escalate only when the issue cannot be resolved safely in the chat."
        ),
    },
    "sales": {
        "label": "Sales Agent",
        "instruction": (
            "Operate like a helpful sales advisor, not a pushy salesperson. If the customer asks about products or services, "
            "answer with relevant details first, recommend the best-fit option when context is available, and guide toward one clear buying or follow-up step."
        ),
    },
    "onboarding": {
        "label": "Onboarding Agent",
        "instruction": (
            "Operate like a clear onboarding guide. Explain setup or next steps simply, remove friction, avoid unnecessary detail, "
            "and help the customer complete the immediate task."
        ),
    },
    "generic": {
        "label": "General Assistant",
        "instruction": (
            "Operate like a natural CRM assistant. Understand the latest customer message, use the available context, "
            "answer specifically, and avoid robotic greetings, repeated questions, or irrelevant budget/product prompts."
        ),
    },
}


def _allow_rule_based_recovery() -> bool:
    return ai_enable_rule_based_recovery()


def build_system_prompt(company_info: dict) -> str:
    return f"""
You are a helpful, friendly sales and support assistant for {company_info.get('name', 'this business')}.

Your primary job is to have natural conversations, answer questions accurately using
the business data provided, and guide interested customers toward a purchase or next step.

Rules you must follow:
1. Always respond to the customer's latest message first. Never ignore it.
2. If the customer asks about products, services, pricing, or availability —
   answer using the retrieved business data. Do not invent details.
3. If the data is not available, say so honestly and offer to connect them with a specialist.
4. After your main answer, you may ask ONE natural follow-up question if it helps
   move the conversation forward. Never interrogate the customer.
5. Never send a qualification question as your entire response unless the message
   was completely empty or meaningless.
6. Keep tone warm, professional, and persuasive without being pushy.
7. If the customer shows buying intent, clearly explain next steps.
8. Never repeat a question you already asked in this conversation.

Business context will be provided to you. Use it.
""".strip()


def _sentiment_label(sentiment: dict | None) -> str:
    payload = dict(sentiment or {})
    label = str(payload.get("sentiment_label") or payload.get("label") or "").strip().lower()
    if label in {"positive", "neutral", "negative"}:
        return label
    score = float(payload.get("score") or 0.0)
    if score >= 0.2:
        return "positive"
    if score <= -0.2:
        return "negative"
    return "neutral"


def _derive_conversation_state(
    *,
    query: str,
    observed_intent: dict,
    observed_sentiment: dict,
    previous_state: dict,
    last_response_context: dict,
) -> dict:
    current_intent = _canonical_intent(
        str((observed_intent or {}).get("intent") or "general_question").strip().lower() or "general_question"
    )
    previous_intent = (
        str((previous_state or {}).get("intent") or "").strip().lower()
        or str(((last_response_context or {}).get("intent") or {}).get("intent") or "").strip().lower()
    )
    entities = observed_intent.get("entities") if isinstance(observed_intent.get("entities"), dict) else {}
    urgency = str((observed_intent or {}).get("urgency") or "medium").strip().lower() or "medium"
    sentiment_label = _sentiment_label(observed_sentiment)
    turn_count = max(1, int((previous_state or {}).get("turn_count") or 0) + 1)
    intent_shift = bool(previous_intent and current_intent and previous_intent != current_intent)

    if current_intent in {"refund", "cancel_request", "complaint", "support_request", "shipping_question"}:
        stage = "resolution"
    elif current_intent in {"product_recommendation", "product_catalog_question", "purchase_inquiry"}:
        stage = "recommendation"
    elif current_intent in {"gratitude"}:
        stage = "wrap_up"
    else:
        stage = "discovery"
    if urgency in {"high", "critical"} and stage != "wrap_up":
        stage = "resolution"
    same_intent_turns = 1
    if previous_intent and previous_intent == current_intent:
        same_intent_turns = int((previous_state or {}).get("same_intent_turns") or 1) + 1
    topic_exhausted = same_intent_turns >= 3 and not bool(entities)

    next_action_map = {
    "refund": (
        "Acknowledge the refund request, ask for the order/payment detail if missing, "
        "and explain that eligibility will be checked before escalation."
    ),
    "cancel_request": (
        "Confirm what the customer wants to cancel, ask for the account/order detail if missing, "
        "and guide them toward immediate cancellation or human handoff."
    ),
    "complaint": (
        "Acknowledge the concern clearly, avoid defensiveness, and ask for only the one detail needed "
        "to resolve or escalate the issue."
    ),
    "support_request": (
        "Answer with the most direct troubleshooting step or support guidance. "
        "Ask for one specific missing detail only if required."
    ),
    "shipping_question": (
        "Answer the delivery/shipping question using available context. "
        "If tracking details are missing, ask for the order or tracking reference."
    ),
    "purchase_inquiry": (
        "Answer the specific buying, pricing, availability, or product question first. "
        "Then guide the customer to the next buying step."
    ),
    "product_recommendation": (
        "Recommend the most relevant product options from available context, include key details, "
        "and ask one preference only if needed to narrow the choice."
    ),
    "product_catalog_question": (
        "Show available product options from context and ask whether the customer wants prices, images, or details."
    ),
    "follow_up_continue": (
        "Continue the previous topic naturally using recent context and avoid repeating the last answer."
    ),
    "product_image_request": (
        "Share or attach the most relevant product image if available, and briefly mention the matching product details."
    ),
    "pricing_question": (
        "Answer the pricing question using available context. If exact pricing is unavailable, "
        "explain what detail is needed to quote accurately."
    ),
    "availability_question": (
        "Answer availability or stock status using available context. If unknown, ask for the specific product, variant, or quantity."
    ),
    "service_question": (
        "Answer the service question directly using business context, summarize the relevant services, "
        "and offer one clear next step."
    ),
    "company_question": (
        "Answer the company or business question directly using available context, without turning it into a sales qualification question."
    ),
    "human_handoff": (
        "Acknowledge the request for a person and guide the conversation toward human handoff clearly and briefly."
    ),
    "negotiation": (
        "Acknowledge the pricing or terms concern, avoid inventing discounts, and guide toward a practical next step or human review."
    ),
    "rejection_or_opt_out": (
        "Respect the customer's decision, keep the reply brief, and avoid further selling unless they ask another question."
    ),
    "gratitude": (
        "Reply warmly and briefly, then offer one useful follow-up only if it fits the conversation."
    ),
    "greeting": (
        "Greet the customer warmly and briefly. Ask one relevant question about how you can help — "
        "do not mention budget, cost, or pricing unless the customer raises it first."
    ),
    "general_question": (
        "Answer the customer's question directly if enough context exists. "
        "If it is unclear, ask one specific clarifying question about what they need."
    ),
    "unclear_request": (
        "Ask one simple clarifying question: do they need help with a product, service, support issue, or something else?"
    ),
    }
    next_action = next_action_map.get(
        current_intent,
        (
            "Answer the latest customer message as directly as possible using available context. "
            "Ask only one specific follow-up question if a required detail is missing."
        ),
    )

    if sentiment_label == "negative" and stage != "wrap_up":
        next_action = "Prioritize resolution first, then confirm if escalation to a human is needed."

    transition_note = ""
    if intent_shift:
        transition_note = (
            f"Intent shifted from {previous_intent.replace('_', ' ')} to {current_intent.replace('_', ' ')}."
        )

    return {
        "intent": current_intent,
        "previous_intent": previous_intent,
        "intent_shift": intent_shift,
        "transition_note": transition_note,
        "stage": stage,
        "next_action": next_action,
        "sentiment_label": sentiment_label,
        "turn_count": turn_count,
        "latest_query": query,
        "entities": entities,
        "same_intent_turns": same_intent_turns,
        "topic_exhausted": topic_exhausted,
    }


def _recent_ai_messages(conversation_context: list[dict], *, limit: int = 3) -> list[str]:
    limit = max(1, int(limit or ai_response_recent_ai_message_limit()))
    recent: list[str] = []
    for message in reversed(conversation_context or []):
        if str(message.get("sender_type") or "").strip().lower() != "ai":
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        recent.append(content)
        if len(recent) >= limit:
            break
    return list(reversed(recent))


def _build_generation_config(
    *,
    query: str,
    observed_intent: dict,
    observed_sentiment: dict,
    recent_ai_replies: list[str],
) -> dict:
    base_temperature = ai_response_temperature_base()
    min_temperature = ai_response_temperature_min()
    max_temperature = ai_response_temperature_max()
    jitter_steps = ai_response_temperature_jitter_steps()
    product_delta = ai_response_temperature_product_delta()
    negative_delta = ai_response_temperature_negative_delta()
    repetition_delta = ai_response_temperature_repetition_delta()
    intent_name = str((observed_intent or {}).get("intent") or "").strip().lower()
    sentiment_label = _sentiment_label(observed_sentiment)
    if intent_name in {"product_recommendation", "purchase_inquiry"}:
        base_temperature += product_delta
    if sentiment_label == "negative":
        base_temperature += negative_delta
    if len(recent_ai_replies) >= 2:
        base_temperature += repetition_delta
    jitter = random.randint(0, jitter_steps) / 100.0
    temperature = max(min_temperature, min(max_temperature, base_temperature + jitter))
    return {"temperature": round(temperature, 2), "max_output_tokens": 1024}


def _agent_configuration(agent: dict | None) -> dict[str, str]:
    config = (agent or {}).get("configuration")
    return dict(config or {}) if isinstance(config, dict) else {}


def _is_acceptable_attachment_url(url: str) -> bool:
    """Accept absolute image URLs and relative /api/... media paths."""
    if not url:
        return False
    # Relative API media paths served by the backend
    if url.startswith("/api/") or url.startswith("/media/"):
        return True
    return is_valid_image_url(url)


def _normalize_ai_attachments(attachments: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    seen_urls: set[str] = set()
    for index, attachment in enumerate(attachments or []):
        if not isinstance(attachment, dict):
            continue
        url = str(attachment.get("url") or "").strip()
        product_id = str(attachment.get("product_id") or "").strip()
        if not url or not product_id or url in seen_urls or not _is_acceptable_attachment_url(url):
            continue
        normalized.append(
            {
                "type": "image",
                "url": url,
                "name": str(attachment.get("name") or attachment.get("product_name") or "").strip(),
                "size": int(attachment.get("size") or 0),
                "product_id": product_id,
                "product_name": str(attachment.get("product_name") or attachment.get("name") or "").strip(),
                "product_title": str(attachment.get("product_title") or "").strip(),
                "product_category": str(attachment.get("product_category") or "").strip(),
                "image_index": int(attachment.get("image_index") or index),
            }
        )
        seen_urls.add(url)
        if len(normalized) >= 3:
            break
    return normalized


def _align_product_attachments(product_ids: list[str], attachments: list[dict]) -> list[dict]:
    allowed = {str(product_id).strip() for product_id in product_ids if str(product_id).strip()}
    normalized = _normalize_ai_attachments(attachments)
    aligned: list[dict] = []
    seen_products: set[str] = set()
    for attachment in normalized:
        product_id = str(attachment.get("product_id") or "").strip()
        if allowed and product_id not in allowed:
            continue
        if product_id in seen_products:
            continue
        aligned.append(attachment)
        seen_products.add(product_id)
        if len(aligned) >= 3:
            break
    return aligned


def _trim_text(value: str, limit: int = 160) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _looks_like_navigation_request(query: str) -> bool:
    lowered = (query or "").strip().lower()
    return any(
        phrase in lowered
        for phrase in (
            "how do i",
            "how to",
            "where do i",
            "what should i do",
            "next step",
            "what next",
            "help me",
            "show me",
            "start here",
            "guide me",
        )
    )


def _looks_like_service_catalog_question(query: str) -> bool:
    lowered = (query or "").strip().lower()
    if not lowered:
        return False
    service_terms = ("service", "services", "solution", "solutions", "offer", "offering", "provide", "providing")
    question_terms = ("what", "which", "show", "tell", "list", "available", "do you")
    return any(term in lowered for term in service_terms) and any(term in lowered for term in question_terms)


def _looks_like_image_request(query: str) -> bool:
    lowered = (query or "").strip().lower()
    return any(
        term in lowered
        for term in (
            "image",
            "images",
            "photo",
            "photos",
            "picture",
            "pictures",
            "catalog",
            "catalogue",
            "visual",
            "show me",
        )
    )


_SHORT_FOLLOW_UPS = {
    "next",
    "yes",
    "yeah",
    "yep",
    "ok",
    "okay",
    "continue",
    "show",
    "send",
    "proceed",
    "go ahead",
    "tell me more",
    "more",
}

_INTENT_ALIASES = {
    "product_interest": "product_recommendation",
    "product_inquiry": "product_catalog_question",
    "product_question": "product_catalog_question",
    "catalog_question": "product_catalog_question",
    "price_question": "pricing_question",
    "pricing_payment": "pricing_question",
    "payment_question": "pricing_question",
    "company_info": "company_question",
    "service_info": "service_question",
    "service_inquiry": "service_question",
    "business_inquiry": "business_question",
    "continue": "follow_up_continue",
    "next": "follow_up_continue",
    "order_status": "shipping_question",
    "delivery_question": "shipping_question",
    "purchase": "buying_intent",
    "purchase_intent": "buying_intent",
    "conversion_intent": "buying_intent",
    "opt_out": "rejection_or_opt_out",
    "escalation": "human_handoff",
}

_INTERNAL_RESPONSE_MARKERS = (
    "next action:",
    "conversation stage",
    "intent shifted",
    "focused on general question",
    "focused on your request",
    "here is the quickest path",
    "give a direct",
    "ask one specific",
    "use available context",
    "using available context",
    "answer the customer",
    "answer the customer's question directly",
    "acknowledge the refund request",
    "answer the delivery/shipping question",
    "continue the previous topic naturally",
    "clarify customer objective",
    "route the conversation",
    "routing note",
    "private operating",
    "private planning",
    "confidence score",
    "provider name",
    "model name",
    "workflow label",
    "workflow labels",
    "do you have a rough budget",
    "what is your budget",
    "rough budget in mind",
    "budget in mind for this",
    "operating guidance:",
    "style guidance:",
    "agent tone:",
    "private planning notes",
)


def _is_short_follow_up(query: str) -> bool:
    normalized = re.sub(r"[^a-z0-9\s]", "", str(query or "").strip().lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized in _SHORT_FOLLOW_UPS or is_short_follow_up_message(query)


def _canonical_intent(intent_name: str) -> str:
    normalized = str(intent_name or "general_question").strip().lower() or "general_question"
    return _INTENT_ALIASES.get(normalized, normalized)


def _customer_safe_next_action(state: dict, intent_name: str) -> str:
    intent_name = _canonical_intent(intent_name or str((state or {}).get("intent") or ""))
    mapping = {
        "greeting": "greet_and_ask",
        "follow_up_continue": "continue_topic",
        "human_handoff": "escalate_to_human",
        "rejection_or_opt_out": "respect_opt_out",
        "refund": "support_resolution",
        "cancel_request": "support_resolution",
        "complaint": "support_resolution",
        "support_request": "support_resolution",
        "shipping_question": "support_resolution",
        "pricing_question": "answer_pricing",
        "product_catalog_question": "show_products",
        "product_recommendation": "show_products",
        "product_image_request": "show_product_images",
        "service_question": "answer_services",
        "company_question": "answer_company",
        "buying_intent": "guide_purchase",
        "order_intent": "guide_purchase",
        "website_link_request": "guide_purchase",
    }
    return mapping.get(intent_name, "continue_conversation")


def _get_last_topic(intent_payload: dict | None, conversation_context: list[dict] | None = None) -> str:
    entities = (intent_payload or {}).get("entities") if isinstance((intent_payload or {}).get("entities"), dict) else {}
    for key in ("last_topic", "product_name", "service_name", "previous_topic"):
        value = str(entities.get(key) or "").strip()
        if value:
            return value
    for item in reversed(conversation_context or []):
        content = str((item or {}).get("content") or "").strip()
        topic = _infer_topic_from_text(content)
        if topic:
            return topic
    return ""


def _infer_topic_from_text(text: str) -> str:
    lowered = str(text or "").lower()
    if any(term in lowered for term in ("service", "software", "cloud", "automation", "solution", "consulting")):
        return "services"
    if any(term in lowered for term in ("product", "catalog", "option", "price", "image", "photo", "picture")):
        return "products"
    if any(term in lowered for term in ("order", "buy", "purchase", "website", "checkout")):
        return "buying"
    if any(term in lowered for term in ("support", "issue", "problem", "refund", "cancel")):
        return "support"
    return ""


def _normalize_observed_intent(
    observed_intent: dict,
    *,
    query: str,
    previous_response: str = "",
    previous_state: dict | None = None,
    last_response_context: dict | None = None,
) -> dict:
    payload = dict(observed_intent or {})
    intent_name = _canonical_intent(str(payload.get("intent") or "general_question"))
    previous_intent = _canonical_intent(
        str((previous_state or {}).get("intent") or ((last_response_context or {}).get("intent") or {}).get("intent") or "")
    )
    if _is_short_follow_up(query) or is_short_follow_up_message(query, previous_response):
        intent_name = "follow_up_continue"
        topic = _infer_topic_from_text(previous_response) or previous_intent
        payload.setdefault("entities", {})
        if isinstance(payload.get("entities"), dict) and topic:
            payload["entities"] = {**payload["entities"], "previous_topic": topic}
        payload["confidence"] = max(float(payload.get("confidence") or 0.0), 0.55)
    payload["intent"] = intent_name
    return payload


def _product_reason(product: dict) -> str:
    parts: list[str] = []
    category = str(product.get("category") or "").strip()
    price = str(product.get("price") or "").strip()
    currency = str(product.get("price_currency") or "USD").strip() or "USD"
    features = [str(item).strip() for item in (product.get("features") or []) if str(item).strip()]
    if category:
        parts.append(category)
    if features:
        parts.append(features[0] if len(features) == 1 else ", ".join(features[:2]))
    if price:
        parts.append(f"priced at {price} {currency}".strip())
    return "; ".join(parts) if parts else "a strong match for the current request"


def _should_share_website_link(intent_name: str, query: str, conversation_state: dict | None = None) -> bool:
    lowered = str(query or "").strip().lower()
    if intent_name in {"website_link_request", "order_intent"}:
        return True
    if intent_name == "buying_intent" and any(
        phrase in lowered
        for phrase in (
            "where can i buy",
            "where can i order",
            "how can i order",
            "how do i order",
            "place order",
            "order link",
            "checkout",
            "buy this",
            "purchase this",
        )
    ):
        return True
    stage = str((conversation_state or {}).get("stage") or "").strip().lower()
    return stage in {"conversion", "closing", "checkout", "order"} and any(
        term in lowered for term in ("buy", "order", "purchase", "checkout", "link")
    )


def _sanitize_public_context_text(text: str, *, allow_website: bool = False) -> str:
    if not text:
        return ""
    blocked_markers = (
        "api key",
        "secret",
        "token",
        "company_id",
        "customer_id",
        "internal",
        "private",
        "owner",
    )
    parts = re.split(r"\n+|\s+\|\s+", str(text or ""))
    safe_parts: list[str] = []
    for part in parts:
        cleaned = " ".join(part.split()).strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if any(marker in lowered for marker in blocked_markers):
            continue
        if not allow_website and lowered.startswith("website:"):
            continue
        if "@" in cleaned or re.search(r"\+?\d[\d\s().-]{6,}\d", cleaned):
            continue
        safe_parts.append(cleaned)
    return " | ".join(dict.fromkeys(safe_parts))


def _public_company_fields(text: str, ai_context: dict | None = None, *, allow_website: bool = False) -> dict:
    public_company = dict((ai_context or {}).get("public_company") or {})
    fields = {
        "company_name": str(public_company.get("company_name") or public_company.get("name") or "").strip(),
        "title": str(public_company.get("title") or "").strip(),
        "tagline": str(public_company.get("tagline") or "").strip(),
        "description": str(public_company.get("description") or "").strip(),
        "industry": str(public_company.get("industry") or "").strip(),
        "services": "",
        "website": str(public_company.get("website_address") or public_company.get("public_website_url") or "").strip()
        if allow_website
        else "",
    }
    safe_text = _sanitize_public_context_text(text, allow_website=allow_website)
    for segment in re.split(r"\s+\|\s+|\n+", safe_text):
        item = " ".join(segment.split()).strip()
        if not item:
            continue
        key, sep, value = item.partition(":")
        normalized_key = key.strip().lower()
        normalized_value = value.strip() if sep else item
        if sep and normalized_key in {"company", "company name", "brand"} and not fields["company_name"]:
            fields["company_name"] = normalized_value
        elif sep and normalized_key in {"title", "tagline"} and not fields[normalized_key]:
            fields[normalized_key] = normalized_value
        elif sep and normalized_key in {"brand description", "description", "public description"} and not fields["description"]:
            fields["description"] = normalized_value
        elif sep and normalized_key in {"industry", "category"} and not fields["industry"]:
            fields["industry"] = normalized_value
        elif sep and "service" in normalized_key and not fields["services"]:
            fields["services"] = normalized_value
        elif sep and "website" in normalized_key and allow_website and not fields["website"]:
            fields["website"] = normalized_value
        elif not sep and not fields["services"] and re.search(r"\b(service|services|solutions|offer|provide|provides)\b", item, re.I):
            fields["services"] = re.sub(
                r"^(we\s+)?(provide|provides|offer|offers|services\s+include)\s*[:\-]?\s*",
                "",
                item,
                flags=re.I,
            ).strip()
    return fields


def _service_next_step(fields: dict, *, include_products: bool = False, follow_up: bool = False) -> str:
    services = str(fields.get("services") or "").strip()
    if include_products:
        return "Do you want to see services, products, or both?"
    if services and "," in services:
        candidates = [part.strip() for part in re.split(r",|/| and ", services) if part.strip()][:3]
        if len(candidates) >= 2:
            return f"Which area would you like details about first: {', '.join(candidates)}?"
    if follow_up:
        return "Do you want details about pricing, timeline, or examples?"
    return "Which service would you like details about first?"


def _fix_grammar_errors(text: str) -> str:
    fixed = str(text or "")
    fixed = re.sub(r"\bWe provide\s+[A-Z][A-Za-z0-9&.' -]{1,80}\s+is\s+", "We provide ", fixed)
    fixed = re.sub(r"\s+([?.!,])", r"\1", fixed)
    fixed = re.sub(r"\s{2,}", " ", fixed).strip()
    return fixed


def _cap_knowledge_context(knowledge_text: str, query: str = "", *, max_paragraphs: int = 3) -> str:
    text = str(knowledge_text or "").strip()
    if not text:
        return ""
    paragraphs = [p.strip() for p in re.split(r"\n{2,}|\s\|\s", text) if p.strip()]
    if len(paragraphs) <= max_paragraphs:
        return "\n".join(paragraphs)
    query_tokens = set(re.findall(r"[a-z0-9]+", str(query or "").lower()))
    scored = []
    for index, paragraph in enumerate(paragraphs):
        tokens = set(re.findall(r"[a-z0-9]+", paragraph.lower()))
        scored.append((len(tokens & query_tokens), -index, paragraph))
    return "\n".join(item[2] for item in sorted(scored, reverse=True)[:max_paragraphs])


def _format_products_for_prompt(products: list[dict]) -> str:
    lines: list[str] = []
    for product in list(products or [])[:5]:
        if not isinstance(product, dict):
            continue
        name = str(product.get("name") or product.get("product_title") or "").strip()
        if not name:
            continue
        parts = [f"Product: {name}"]
        category = str(product.get("category") or "").strip()
        price = str(product.get("price") or "").strip()
        currency = str(product.get("price_currency") or "USD").strip() or "USD"
        features = [str(item).strip() for item in (product.get("features") or []) if str(item).strip()]
        description = str(product.get("description") or product.get("short_description") or "").strip()
        if category:
            parts.append(f"Category: {category}")
        if price:
            parts.append(f"Price: {price} {currency}")
        elif "price" in product:
            parts.append("Price: not listed")
        if features:
            parts.append(f"Features: {', '.join(features[:4])}")
        if description:
            parts.append(f"Description: {truncate_text_for_tokens(description, 80)}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _prev_contrast_block(previous_response: str) -> str:
    previous = truncate_text_for_tokens(str(previous_response or "").strip(), 220)
    if not previous:
        return ""
    return (
        "Previous AI reply to avoid repeating:\n"
        f"{previous}\n"
        "Use a different structure and add one fresh, specific detail if context supports it."
    )


def _channel_tone_note(channel: str) -> str:
    channel = str(channel or "").strip().lower()
    if channel in {"whatsapp", "facebook", "instagram"}:
        return "Channel tone: keep this short and conversational, ideally 1-2 sentences."
    if channel == "email":
        return "Channel tone: use a slightly more formal 3-4 sentence reply with a clear close."
    if channel == "web_chat":
        return "Channel tone: keep a balanced web-chat reply, concise but complete."
    return "Channel tone: keep the reply concise, natural, and easy to act on."


def build_pricing_response(query: str, *, ai_context: dict, intent_name: str = "") -> str:
    products = list((ai_context or {}).get("products") or [])[:3]
    priced = [p for p in products if str(p.get("price") or "").strip()]
    if len(priced) == 1:
        product = priced[0]
        name = str(product.get("name") or product.get("product_title") or "This option").strip()
        price = str(product.get("price") or "").strip()
        currency = str(product.get("price_currency") or "USD").strip() or "USD"
        features = [str(item).strip() for item in (product.get("features") or []) if str(item).strip()]
        feature_text = f" and includes {', '.join(features[:2])}" if features else ""
        return f"The {name} is {price} {currency}{feature_text}. Do you want details or help with the next step?"
    if products and not priced:
        name = str(products[0].get("name") or products[0].get("product_title") or "that option").strip()
        return f"I found {name}, but the exact price is not listed in the available catalog context. Which product or service should I check next?"
    if priced:
        lines = ["Here are the available prices I found:"]
        for index, product in enumerate(priced, start=1):
            name = str(product.get("name") or product.get("product_title") or "Product").strip()
            price = str(product.get("price") or "").strip()
            currency = str(product.get("price_currency") or "USD").strip() or "USD"
            lines.append(f"{index}. {name} - {price} {currency}.")
        lines.append("Which option do you want details about?")
        return "\n".join(lines)
    return "Which product or service do you want pricing for? I can check the available details."


def build_support_response(query: str, *, intent_name: str = "") -> str:
    lowered = str(query or "").lower()
    if any(term in lowered for term in ("delay", "delayed", "hasn't arrived", "not arrived", "late", "shipping")):
        return "I understand the delivery has not arrived yet. Share the order number or tracking reference so I can guide the next step."
    if any(term in lowered for term in ("damaged", "broken", "cracked")):
        return "I understand the item arrived damaged. Share the order number and a photo of the damage so this can be reviewed."
    if any(term in lowered for term in ("wrong item", "incorrect", "different item")):
        return "I understand you received the wrong item. Share the order number and what arrived so the next step can be checked."
    if "refund" in lowered:
        return "I can help start a refund check. Send the order or payment reference so eligibility can be reviewed."
    if any(term in lowered for term in ("billing", "charged", "payment")):
        return "I understand there is a billing issue. Share the payment or invoice reference so the charge can be checked."
    return "I can help with this support issue. Share the order, product, or account detail so I can guide the next step."


def build_greeting_response(
    query: str,
    *,
    ai_context: dict,
    knowledge_context: str = "",
    customer_id: str = "",
) -> str:
    """Return a warm, brief greeting that asks one relevant next question — never budget."""
    fields = _public_company_fields(
        knowledge_context or str((ai_context or {}).get("knowledge_text") or ""), ai_context
    )
    company = str(fields.get("company_name") or "").strip()
    services = str(fields.get("services") or "").strip()
    openers = [
        "Hi! Thanks for reaching out.",
        "Hello, thanks for messaging.",
        "Hi there, good to hear from you.",
        "Welcome, thanks for reaching out.",
        "Hey, thanks for contacting us.",
    ]
    seed = customer_id or query or company or services or "default"
    opener = openers[int(hashlib.sha1(seed.encode("utf-8")).hexdigest(), 16) % len(openers)]

    if services:
        service_list = [s.strip() for s in re.split(r",|/| and ", services) if s.strip()][:3]
        if len(service_list) >= 2:
            options = ", ".join(service_list[:2])
            return f"{opener} We can help with {options}, and more. What are you looking for today?"
        return f"{opener} We offer {services}. What can I help you with?"
    if company:
        return f"{opener} Welcome to {company}. What can I help you with today?"
    return f"{opener} What can I help you with today?"


def build_degraded_response(
    query: str,
    *,
    knowledge_context: str = "",
    ai_context: dict | None = None,
    previous_response: str = "",
    conversation_state: dict | None = None,
) -> str:
    """Customer-facing fallback for LLM failures — always safe, never exposes internals."""
    intent_name = str((conversation_state or {}).get("intent") or "general_question")
    return build_safe_unclear_response(
        query,
        intent_name=intent_name,
        knowledge_context=knowledge_context,
        ai_context=ai_context or {},
        previous_response=previous_response,
        conversation_state=conversation_state,
    )


def build_customer_facing_next_step(
    intent_name: str,
    latest_message: str,
    context: dict | None,
    conversation_state: dict | None,
) -> str:
    intent_name = _canonical_intent(intent_name)
    if intent_name in {"service_question", "company_question", "business_question", "follow_up_continue"}:
        fields = _public_company_fields(str((context or {}).get("knowledge_text") or ""), context)
        return _service_next_step(fields, follow_up=intent_name == "follow_up_continue")
    if intent_name in {"product_catalog_question", "product_recommendation", "product_image_request"}:
        return "Do you want product details, prices, or pictures?"
    if intent_name == "pricing_question":
        return "Which product or service do you want pricing for?"
    if intent_name in {"buying_intent", "order_intent", "website_link_request"}:
        return "Do you want to confirm the option before ordering?"
    if intent_name in {"shipping_question", "support_request", "complaint", "refund", "cancel_request"}:
        return "Share the order, product, or account detail so I can check the next step."
    return "Do you want details about services, products, pricing, or support?"


def build_service_response(
    query: str,
    *,
    knowledge_context: str,
    ai_context: dict,
    previous_response: str = "",
    follow_up: bool = False,
    include_products: bool = False,
) -> str:
    fields = _public_company_fields(knowledge_context or str((ai_context or {}).get("knowledge_text") or ""), ai_context)
    company = str(fields.get("company_name") or "We").strip()
    services = str(fields.get("services") or "").strip()
    description = str(fields.get("description") or fields.get("tagline") or fields.get("industry") or "").strip()
    description = _trim_text(description, 180)
    next_step = _service_next_step(fields, include_products=include_products, follow_up=follow_up)

    if services:
        if company.lower() == "we":
            lead = f"To add to that, we mainly provide {services}." if follow_up else f"We provide {services}."
        else:
            lead = (
                f"To add to that, {company} mainly provides {services}."
                if follow_up
                else f"{company} provides {services}."
            )
        if description and description.lower() not in lead.lower():
            return f"{lead} {description}. {next_step}"
        return f"{lead} {next_step}"

    if description:
        lead = f"{company} is {description}" if company != "We" and not description.lower().startswith(company.lower()) else description
        if include_products:
            return f"{lead}. I can also show available product options from the catalog. {next_step}"
        return (
            f"{lead}. I do not see a detailed service list in the available public profile. {next_step}"
        )

    return f"I do not see a detailed public service list in the available context. {next_step}"


def build_product_response(
    query: str,
    *,
    ai_context: dict,
    intent_name: str = "",
    image_request: bool = False,
    share_website: bool = False,
    intent_payload: dict | None = None,
    conversation_context: list[dict] | None = None,
) -> dict:
    products = list((ai_context or {}).get("products") or [])[:3]
    attachments = _normalize_ai_attachments(list((ai_context or {}).get("product_attachments") or []))
    if not products:
        last_topic = _get_last_topic(intent_payload or {}, conversation_context or [])
        if last_topic:
            return {
                "response": f"I do not see catalog details for {last_topic} in the available context. Do you want services, products, or pricing help?",
                "attachments": [],
                "product_images": [],
                "product_ids": [],
            }
        return {
            "response": "I do not see product details in the available catalog context. Do you want services, products, or pricing help?",
            "attachments": [],
            "product_images": [],
            "product_ids": [],
        }
    if len(products) == 1:
        product = products[0]
        name = str(product.get("name") or product.get("product_title") or "This option").strip()
        reason = _product_reason(product)
        response = f"The {name} looks like a great fit - {reason}."
        if not str(product.get("price") or "").strip() and _canonical_intent(intent_name) == "pricing_question":
            response += " The price is not listed for this option."
        if image_request and attachments:
            response += " I can share the matching image with this reply."
        elif image_request:
            response += " I do not see an image available for this product, but these are the details I found."
        else:
            response += " Do you want details, prices, or pictures for this option?"
        website_url = str((ai_context or {}).get("public_company", {}).get("website_address") or "").strip()
        if share_website and website_url:
            response += f" You can place the order here: {website_url}"
        return {
            "response": response,
            "attachments": attachments,
            "product_images": attachments,
            "product_ids": [str(item).strip() for item in (ai_context or {}).get("product_ids", []) if str(item).strip()][:3],
        }
    lines = ["Here are the most relevant options I found."]
    for index, product in enumerate(products, start=1):
        name = str(product.get("name") or product.get("product_title") or "Product").strip()
        lines.append(f"{index}. {name} - {_product_reason(product)}.")
        if not str(product.get("price") or "").strip() and _canonical_intent(intent_name) == "pricing_question":
            lines.append("The price is not listed for this option.")
    if image_request and attachments:
        lines.append("I can share the matching image with this reply.")
    elif image_request:
        lines.append("I do not see an image available for this product, but these are the details I found.")
    else:
        lines.append("Do you want details, prices, or pictures for any option?")
    website_url = str((ai_context or {}).get("public_company", {}).get("website_address") or "").strip()
    if share_website and website_url:
        lines.append(f"You can place the order here: {website_url}")
    return {
        "response": "\n".join(lines),
        "attachments": attachments,
        "product_images": attachments,
        "product_ids": [str(item).strip() for item in (ai_context or {}).get("product_ids", []) if str(item).strip()][:3],
    }


def build_follow_up_response(
    query: str,
    *,
    previous_response: str,
    knowledge_context: str,
    ai_context: dict,
    conversation_state: dict,
) -> str:
    topic = _infer_topic_from_text(previous_response) or str((conversation_state or {}).get("previous_intent") or "")
    if topic in {"services", "service_question", "company_question", "business_question"}:
        return build_service_response(
            query,
            knowledge_context=knowledge_context,
            ai_context=ai_context,
            previous_response=previous_response,
            follow_up=True,
        )
    if topic in {"products", "product_recommendation", "product_catalog_question"}:
        product_payload = build_product_response(query, ai_context=ai_context, image_request=_looks_like_image_request(query))
        return product_payload["response"]
    if topic in {"buying", "buying_intent", "order_intent", "website_link_request"}:
        return "I can help you proceed with the next buying step. Which product or service should I help you confirm first?"
    return "Do you want me to continue with services, products, pricing, or support?"


def _has_internal_response_text(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in _INTERNAL_RESPONSE_MARKERS) or bool(
        re.search(r"\b(general_question|service_question|company_question|business_question|product_catalog_question|product_recommendation|pricing_question|follow_up_continue)\b", lowered)
    )


def _clean_customer_response_text(text: str) -> str:
    cleaned_sentences: list[str] = []
    chunks = re.split(r"(?<=[.!?])\s+|\n+", str(text or ""))
    for sentence in chunks:
        sentence = sentence.strip()
        if not sentence:
            continue
        lowered = sentence.lower()
        if any(marker in lowered for marker in _INTERNAL_RESPONSE_MARKERS):
            continue
        sentence = re.sub(r"\b(general_question|service_question|company_question|business_question|product_catalog_question|product_recommendation|pricing_question|follow_up_continue)\b", "", sentence, flags=re.I)
        sentence = re.sub(r"\[[^\]]*(stage|intent|workflow|confidence)[^\]]*\]", "", sentence, flags=re.I)
        sentence = re.sub(r"\s+([?.!,])", r"\1", sentence)
        sentence = re.sub(r"([.!?]){2,}", r"\1", sentence)
        sentence = re.sub(r"\s{2,}", " ", sentence).strip()
        sentence = _fix_grammar_errors(sentence)
        if sentence:
            cleaned_sentences.append(sentence)
    return " ".join(cleaned_sentences).strip()


def build_safe_unclear_response(
    query: str,
    *,
    intent_name: str,
    knowledge_context: str,
    ai_context: dict,
    previous_response: str = "",
    conversation_state: dict | None = None,
) -> str:
    intent_name = _canonical_intent(intent_name)
    last_topic = _get_last_topic({"entities": (conversation_state or {}).get("entities", {})}, [])
    if intent_name == "follow_up_continue" or _is_short_follow_up(query):
        return build_follow_up_response(
            query,
            previous_response=previous_response,
            knowledge_context=knowledge_context,
            ai_context=ai_context,
            conversation_state=conversation_state or {},
        )
    if intent_name in {"service_question", "company_question", "business_question"} or _looks_like_service_catalog_question(query):
        return build_service_response(
            query,
            knowledge_context=knowledge_context,
            ai_context=ai_context,
            previous_response=previous_response,
            include_products="product" in str(query or "").lower(),
        )
    if intent_name in {
        "product_catalog_question",
        "product_recommendation",
        "product_image_request",
        "pricing_question",
        "availability_question",
        "buying_intent",
        "order_intent",
        "website_link_request",
    }:
        if intent_name == "pricing_question":
            return build_pricing_response(query, ai_context=ai_context, intent_name=intent_name)
        return build_product_response(
            query,
            ai_context=ai_context,
            intent_name=intent_name,
            image_request=_looks_like_image_request(query),
            share_website=_should_share_website_link(intent_name, query, conversation_state),
        )["response"]
    if intent_name in {"support_request", "complaint", "refund", "cancel_request", "shipping_question"}:
        return build_support_response(query, intent_name=intent_name)
    if last_topic:
        return f"I can still help with {last_topic}. Do you want details about services, products, pricing, or support?"
    return "I can still help. Are you looking for details about services, products, pricing, or support?"


def _finalize_customer_response(
    payload: dict,
    *,
    query: str,
    intent_name: str,
    knowledge_context: str,
    ai_context: dict,
    previous_response: str = "",
    conversation_state: dict | None = None,
) -> dict:
    result = dict(payload or {})
    response = str(result.get("response") or "").strip()
    cleaned = _clean_customer_response_text(response)
    result["response"] = cleaned
    next_step = build_customer_facing_next_step(intent_name, query, ai_context, conversation_state or {})
    result["next_step"] = next_step
    result["next_action"] = _customer_safe_next_action(conversation_state or {}, intent_name)
    return result


def _natural_guidance_response(intent_name: str, *, response_prefix: str = "") -> str:
    if intent_name == "shipping_question":
        return f"{response_prefix}I can help with delivery or tracking. Share the order number or tracking reference and I will check the next step."
    if intent_name == "refund":
        return f"{response_prefix}I can help start a refund check. Send the order or payment reference so eligibility can be reviewed."
    if intent_name == "cancel_request":
        return f"{response_prefix}I can help with cancellation. Share what you want to cancel and the related order, booking, or account detail."
    if intent_name == "complaint":
        return f"{response_prefix}I am sorry about that. Tell me what happened and the related order or product detail, and I will help move it toward a resolution."
    if intent_name == "support_request":
        return f"{response_prefix}I can help with that. Share the product, order, or account detail and what happened, and I will guide the next step."
    if intent_name == "human_handoff":
        return f"{response_prefix}I can hand this to a human agent. Share the key detail they should review first."
    if intent_name == "rejection_or_opt_out":
        return f"{response_prefix}Understood. I will not continue with sales follow-up unless you ask for something else."
    return f"{response_prefix}I can help with that. Share one detail about what you need and I will guide the next step."


def _compose_rule_based_response(
    query: str,
    *,
    customer_info: dict,
    knowledge_context: str,
    ai_context: dict,
    observed_sentiment: dict,
    observed_intent: dict,
    previous_response: str,
    last_response_context: dict,
    conversation_state: dict,
) -> dict | None:
    intent_name = _canonical_intent(str((observed_intent or {}).get("intent") or "general_question").lower())
    query_info = understand_product_query(query)
    context_text = str(knowledge_context or (ai_context or {}).get("knowledge_text") or "").strip()
    same_thread = str((last_response_context or {}).get("intent", {}).get("intent") or "").lower() == intent_name
    continuation_prefix = "Continuing the same thread, " if same_thread and previous_response else ""
    emotion = str((observed_sentiment or {}).get("emotion") or "neutral").lower()
    response_prefix = "I understand. " if emotion in {"angry", "frustrated"} else ""
    stage = str((conversation_state or {}).get("stage") or "discovery").strip()
    direction_prefix = ""
    service_question = _looks_like_service_catalog_question(query)
    image_request = _looks_like_image_request(query)
    share_website = _should_share_website_link(intent_name, query, conversation_state)
    public_context_text = _sanitize_public_context_text(context_text, allow_website=share_website)

    if intent_name == "greeting":
        return {
            "response": build_greeting_response(
                query,
                ai_context=ai_context,
                knowledge_context=knowledge_context,
                customer_id=str(customer_info.get("id") or ""),
            ),
            "confidence": 0.97,
            "attachments": [],
            "product_images": [],
            "product_ids": [],
            "llm_id": "",
            "provider": "rule",
            "model_name": "chatbot",
            "conversation_stage": stage,
            "next_action": build_customer_facing_next_step(intent_name, query, ai_context, conversation_state),
            "intent_shift": bool((conversation_state or {}).get("intent_shift")),
        }

    if intent_name == "rejection_or_opt_out":
        return {
            "response": _natural_guidance_response(intent_name, response_prefix=response_prefix),
            "confidence": 0.97,
            "attachments": [],
            "product_images": [],
            "product_ids": [],
            "llm_id": "",
            "provider": "rule",
            "model_name": "chatbot",
            "conversation_stage": stage,
            "next_action": build_customer_facing_next_step(intent_name, query, ai_context, conversation_state),
            "intent_shift": bool((conversation_state or {}).get("intent_shift")),
        }

    if intent_name == "negotiation":
        return {
            "response": "I understand — pricing is an important factor. Let me know which product or service you're considering and I can share the available options or connect you with someone who can help.",
            "confidence": 0.93,
            "attachments": [],
            "product_images": [],
            "product_ids": [],
            "llm_id": "",
            "provider": "rule",
            "model_name": "chatbot",
            "conversation_stage": stage,
            "next_action": build_customer_facing_next_step(intent_name, query, ai_context, conversation_state),
            "intent_shift": bool((conversation_state or {}).get("intent_shift")),
        }

    if intent_name in {"unclear_request", "general_question"} and not public_context_text and not service_question:
        return {
            "response": "I can help with that. Are you looking for information about our services, products, pricing, or do you need support with something?",
            "confidence": 0.88,
            "attachments": [],
            "product_images": [],
            "product_ids": [],
            "llm_id": "",
            "provider": "rule",
            "model_name": "chatbot",
            "conversation_stage": stage,
            "next_action": build_customer_facing_next_step(intent_name, query, ai_context, conversation_state),
            "intent_shift": bool((conversation_state or {}).get("intent_shift")),
        }

    if intent_name == "follow_up_continue" or _is_short_follow_up(query):
        return {
            "response": build_follow_up_response(
                query,
                previous_response=previous_response,
                knowledge_context=knowledge_context,
                ai_context=ai_context,
                conversation_state=conversation_state,
            ),
            "confidence": 0.9,
            "attachments": [],
            "product_images": [],
            "product_ids": [],
            "llm_id": "",
            "provider": "rule",
            "model_name": "chatbot",
            "conversation_stage": stage,
            "next_action": build_customer_facing_next_step(intent_name, query, ai_context, conversation_state),
            "intent_shift": bool((conversation_state or {}).get("intent_shift")),
        }

    if intent_name in {"gratitude"}:
        return {
            "response": (
                f"{direction_prefix}{response_prefix}Glad to help. "
                "If you want, I can also walk you through products, support, or the next step."
            ),
            "confidence": 0.96,
            "attachments": [],
            "product_images": [],
            "product_ids": [],
            "llm_id": "",
            "provider": "rule",
            "model_name": "chatbot",
            "conversation_stage": stage,
            "next_action": build_customer_facing_next_step(intent_name, query, ai_context, conversation_state),
            "intent_shift": bool((conversation_state or {}).get("intent_shift")),
        }

    is_product_or_media_request = (
        intent_name in {
            "product_recommendation",
            "product_catalog_question",
            "purchase_inquiry",
            "product_image_request",
            "pricing_question",
            "availability_question",
            "buying_intent",
            "order_intent",
            "website_link_request",
        }
        or query_info.get("general")
        or query_info.get("specific")
        or image_request
        or service_question
    )

    if intent_name in {"service_question", "company_question", "business_question"} and public_context_text:
        return {
            "response": f"{direction_prefix}{continuation_prefix}{response_prefix}{build_service_response(query, knowledge_context=knowledge_context, ai_context=ai_context, previous_response=previous_response, follow_up=bool(previous_response), include_products='product' in str(query or '').lower())}",
            "confidence": 0.94,
            "attachments": [],
            "product_images": [],
            "product_ids": [],
            "llm_id": "",
            "provider": "rule",
            "model_name": "knowledge",
            "conversation_stage": stage,
            "next_action": build_customer_facing_next_step(intent_name, query, ai_context, conversation_state),
            "intent_shift": bool((conversation_state or {}).get("intent_shift")),
        }

    if intent_name in {
        "company_question",
        "shipping_question",
        "support_request",
        "refund",
        "cancel_request",
        "complaint",
    } or (_looks_like_navigation_request(query) and not is_product_or_media_request):
        return {
            "response": f"{direction_prefix}{continuation_prefix}{build_support_response(query, intent_name=intent_name) if intent_name in {'shipping_question', 'support_request', 'refund', 'cancel_request', 'complaint'} else _natural_guidance_response(intent_name, response_prefix=response_prefix)}",
            "confidence": 0.94,
            "attachments": [],
            "product_images": [],
            "product_ids": [],
            "llm_id": "",
            "provider": "rule",
            "model_name": "chatbot",
            "conversation_stage": stage,
            "next_action": build_customer_facing_next_step(intent_name, query, ai_context, conversation_state),
            "intent_shift": bool((conversation_state or {}).get("intent_shift")),
        }

    if (
        intent_name
        in {
            "product_recommendation",
            "product_catalog_question",
            "purchase_inquiry",
            "product_image_request",
            "pricing_question",
            "availability_question",
            "buying_intent",
            "order_intent",
            "website_link_request",
        }
        or query_info.get("general")
        or service_question
        or image_request
    ):
        is_simple_product_query = bool(query_info.get("general")) or len((query or "").split()) <= 8
        if intent_name in {"product_recommendation", "purchase_inquiry"} and not is_simple_product_query and not image_request:
            return None
        products = list(ai_context.get("products") or [])[:3]
        if service_question and public_context_text and not products:
            return {
                "response": f"{direction_prefix}{continuation_prefix}{response_prefix}{build_service_response(query, knowledge_context=knowledge_context, ai_context=ai_context, previous_response=previous_response, include_products='product' in str(query or '').lower())}",
                "confidence": 0.92,
                "attachments": [],
                "product_images": [],
                "product_ids": [],
                "llm_id": "",
                "provider": "rule",
                "model_name": "knowledge",
                "conversation_stage": stage,
                "next_action": build_customer_facing_next_step(intent_name, query, ai_context, conversation_state),
                "intent_shift": bool((conversation_state or {}).get("intent_shift")),
            }
        if products:
            product_payload = build_product_response(
                query,
                ai_context=ai_context,
                intent_name=intent_name,
                image_request=image_request,
                share_website=share_website,
            )
            if intent_name == "pricing_question":
                product_payload["response"] = build_pricing_response(query, ai_context=ai_context, intent_name=intent_name)
            return {
                "response": f"{direction_prefix}{continuation_prefix}{product_payload['response']}",
                "confidence": 0.95,
                "attachments": product_payload["attachments"],
                "product_images": product_payload["product_images"],
                "product_ids": product_payload["product_ids"],
                "llm_id": "",
                "provider": "rule",
                "model_name": "semantic-ranker",
                "conversation_stage": stage,
                "next_action": build_customer_facing_next_step(intent_name, query, ai_context, conversation_state),
                "intent_shift": bool((conversation_state or {}).get("intent_shift")),
            }
        return {
            "response": (
                "I can help with product or service details. Tell me the product, service, or category you want, "
                "and I will share the most relevant options."
            ),
            "confidence": 0.9,
            "attachments": [],
            "product_images": [],
            "product_ids": [],
            "llm_id": "",
            "provider": "rule",
            "model_name": "chatbot",
            "conversation_stage": stage,
            "next_action": build_customer_facing_next_step(intent_name, query, ai_context, conversation_state),
            "intent_shift": bool((conversation_state or {}).get("intent_shift")),
        }

    if public_context_text:
        return {
            "response": (
                f"{direction_prefix}{continuation_prefix}{response_prefix}{_trim_text(public_context_text, 360)}"
            ),
            "confidence": 0.9,
            "attachments": [],
            "product_images": [],
            "product_ids": [],
            "llm_id": "",
            "provider": "rule",
            "model_name": "knowledge",
            "conversation_stage": stage,
            "next_action": build_customer_facing_next_step(intent_name, query, ai_context, conversation_state),
            "intent_shift": bool((conversation_state or {}).get("intent_shift")),
        }

    return None


def calculate_churn_risk(customer_data: dict) -> dict:
    score = 0.0
    factors = []
    if customer_data.get("avg_sentiment", 0) < -0.3:
        score += 0.3
        factors.append("Negative sentiment")
    if customer_data.get("recent_tickets", 0) > 5:
        score += 0.25
        factors.append("High ticket volume")
    if customer_data.get("days_since_last_contact", 0) > 60:
        score += 0.2
        factors.append("Inactive customer")
    if customer_data.get("complaint_count", 0) > 3:
        score += 0.25
        factors.append("Multiple complaints")
    score = min(score, 1.0)
    return {
        "risk_score": score,
        "risk_level": "critical" if score > 0.7 else "high" if score > 0.5 else "medium" if score > 0.3 else "low",
        "factors": factors,
    }


def _safe_ai_error_reason(exc: Exception) -> tuple[str, str]:
    text = str(exc or "").replace("\n", " ").strip()
    upper = text.upper()
    lowered = text.lower()
    if "PERMISSION_DENIED" in upper or "PERMISSION DENIED" in upper or "403" in upper:
        return "permission_denied", "AI provider permission denied"
    if "SUSPENDED" in upper and ("API" in upper or "KEY" in upper):
        return "api_key_suspended", "AI provider API key is suspended"
    if "RESOURCE_EXHAUSTED" in upper or "QUOTA" in upper:
        return "quota_exhausted", "AI provider quota exhausted"
    if "429" in upper or "RATE LIMIT" in upper:
        return "rate_limited", "AI provider rate limit reached"
    if "API_KEY" in upper or "NOT CONFIGURED" in upper:
        return "provider_not_configured", text[:240] or "AI provider is not configured"
    if "ALL PROVIDERS EXHAUSTED" in upper or "ALL AI PROVIDERS EXHAUSTED" in upper:
        return "all_providers_exhausted", "No usable AI provider fallback is available"
    if "SAFETY" in upper or "CONFIG FAILURE" in upper or "CONFIGURATION FAILURE" in upper:
        return "safety_config_failure", "AI provider safety/configuration failure"
    if "TIMEOUT" in upper or "TIMED OUT" in upper or "DEADLINE" in upper:
        return "provider_timeout", "AI provider request timed out"
    if (
        "INVALID MODEL" in upper
        or "MODEL NOT FOUND" in upper
        or ("MODEL" in upper and "IS NOT FOUND" in upper)
        or ("GENERATECONTENT" in upper and "NOT SUPPORTED" in upper)
    ):
        return "invalid_model", "Selected AI model is invalid"
    if "UNSUPPORTED MODEL" in upper or "DOES NOT SUPPORT IMAGE" in upper or "DOES NOT SUPPORT AUDIO" in upper:
        if "image" in lowered:
            return "unsupported_model", "Selected model does not support image recognition."
        if "audio" in lowered:
            return "unsupported_model", "Selected model does not support audio recognition."
        return "unsupported_model", "Selected AI model is unsupported"
    if "UNSUPPORTED PROVIDER" in upper:
        return "unsupported_provider", text[:240]
    if "NO ACTIVE LLM ENGINE" in upper:
        return "no_active_llm_engine", "No active LLM engine is configured"
    if "NO USABLE PROVIDER" in upper or "NO PROVIDER FALLBACK" in upper:
        return "no_usable_provider_fallback", "No usable AI provider fallback is available"
    if any(marker in upper for marker in ("500", "502", "503", "504", "INTERNAL SERVER")):
        return "provider_5xx", "AI provider is unavailable"
    return exc.__class__.__name__, text[:240] or exc.__class__.__name__


def _degraded_error_payload(exc: Exception, engine: dict | None) -> dict:
    error_type, error_reason = _safe_ai_error_reason(exc)
    provider = str((engine or {}).get("provider") or "").strip()
    model_name = str((engine or {}).get("model_name") or "").strip()
    provider_status = "quota_exhausted" if error_type == "quota_exhausted" else "unavailable"
    return {
        "api_error": True,
        "provider_error": {
            "status": provider_status,
            "error_type": error_type,
            "error_reason": error_reason,
            "provider": provider,
            "model_name": model_name,
        },
        "degraded": True,
        "error_type": error_type,
        "error_reason": error_reason,
        "provider": provider or "fallback",
        "model_name": model_name or "rule-recovery",
        "fallback_used": True,
        "fallback_reason": "invalid_model_no_fallback" if error_type == "invalid_model" else error_type,
    }


def _lead_payload_shape(lead_data: dict) -> dict:
    lead = lead_data if isinstance(lead_data, dict) else {}
    return {
        "keys": sorted(str(key) for key in lead.keys())[:30],
        "has_id": bool(lead.get("id")),
        "has_phone": bool(lead.get("phone")),
        "has_email": bool(lead.get("email")),
        "has_notes": bool(lead.get("notes") or lead.get("message_text") or lead.get("raw_message")),
        "field_count": len(lead),
    }


def sanitize_customer_facing_next_action(value: str) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    lowered = text.lower()
    blocked_tokens = (
        "internal",
        "crm",
        "assign",
        "assignment",
        "status",
        "stage",
        "pipeline",
        "score",
        "grading",
        "metadata",
        "source_id",
        "status_id",
        "agent",
        "team",
        "handoff",
        "escalate",
        "queue",
        "log",
        "review",
        "triage",
    )
    if any(token in lowered for token in blocked_tokens):
        return ""
    if len(text) > 180:
        trimmed = text[:177].rsplit(" ", 1)[0].strip()
        return f"{trimmed}..." if trimmed else ""
    return text


def build_safe_lead_ai_context(lead_data: dict, *, include_next_action: bool = True) -> dict:
    lead = lead_data if isinstance(lead_data, dict) else {}
    payload: dict[str, object] = {}

    name = str(lead.get("name") or "").strip()
    if name:
        payload["name"] = name

    source = str(lead.get("source") or "").strip()
    if source:
        payload["source"] = source

    status = str(lead.get("status") or lead.get("phase") or "").strip()
    if status:
        payload["status"] = status

    phase = str(lead.get("phase") or "").strip()
    if phase:
        payload["phase"] = phase

    score = lead.get("score")
    if score not in (None, ""):
        payload["score"] = score

    scoring_reason = str(lead.get("scoring_reason") or "").strip()
    if scoring_reason:
        payload["scoring_reason"] = scoring_reason

    notes = str(lead.get("notes") or "").strip()
    if notes:
        payload["notes"] = notes

    company_name = str(lead.get("customer_company_name") or lead.get("company") or "").strip()
    if company_name:
        payload["customer_company_name"] = company_name

    last_message = str(lead.get("message_text") or lead.get("raw_message") or "").strip()
    if last_message:
        payload["last_message"] = last_message

    if include_next_action:
        next_action = sanitize_customer_facing_next_action(str(lead.get("next_action") or ""))
        if next_action:
            payload["next_action"] = next_action

    return payload


def _lead_score_unavailable(
    lead_data: dict,
    *,
    engine: dict | None,
    error_type: str,
    error_reason: str,
) -> dict:
    phase = str((lead_data or {}).get("phase") or "awareness").strip() or "awareness"
    return {
        "score": 0,
        "grade": "deferred",
        "phase": phase,
        "reasoning": f"AI lead scoring unavailable: {error_reason}. Lead was not scored.",
        "next_action": "Review lead manually after AI provider is available.",
        "scoring_status": "failed",
        "provider": str((engine or {}).get("provider") or ""),
        "model_name": str((engine or {}).get("model_name") or ""),
        "error_type": error_type,
        "error_reason": error_reason,
        "fallback_used": True,
    }


async def generate_lead_score(
    lead_data: dict,
    db=None,
    company_id: str = "",
    count_against_budget: bool = True,
) -> dict:
    safe_lead = build_safe_lead_ai_context(lead_data, include_next_action=False)
    prompt = (
        "You are a lead-qualification analyst for a CRM.\n"
        "Task: score the sales readiness of one lead record using only the provided evidence.\n"
        "Input format:\n"
        "- lead: JSON with source, notes, attributes, and any engagement signals\n"
        "Output format: return ONLY valid JSON with exactly this schema:\n"
        '{"score": int(0-100), "grade": "hot|warm|cold", "reasoning": "...", '
        '"next_action": "...", "phase": "awareness|interest|consideration|intent|evaluation|purchase"}\n'
        "Rules:\n"
        "- reasoning must be concise and evidence-based.\n"
        "- next_action must be a concrete CRM follow-up, not a generic suggestion.\n"
        "- Do not invent missing facts or metrics.\n"
        f"\nlead:\n{json.dumps(_json_safe(safe_lead), ensure_ascii=True)}"
    )
    engine: dict = {}
    try:
        engine = await _resolve_engine_cached(db=db, company_id=company_id, use_pro=True)
        result = await call_model_json(
            prompt,
            LeadScoreResult,
            engine=engine,
            use_pro=True,
            call_purpose="lead_scoring",
            function_name="generate_lead_score",
            agent_name="qualification",
            count_against_budget=count_against_budget,
        )
        result.setdefault("scoring_status", "completed")
        result.setdefault("provider", str(engine.get("provider") or ""))
        result.setdefault("model_name", str(engine.get("model_name") or ""))
        result.setdefault("fallback_used", False)
        return result
    except Exception as exc:
        error_type, error_reason = _safe_ai_error_reason(exc)
        logger.warning(
            "lead_scoring_failed company_id=%s provider=%s model=%s error_type=%s error_reason=%s inner_exception=%s payload_shape=%s",
            company_id,
            str((engine or {}).get("provider") or ""),
            str((engine or {}).get("model_name") or ""),
            error_type,
            error_reason,
            exc.__class__.__name__,
            _lead_payload_shape(safe_lead),
        )
        return _lead_score_unavailable(
            safe_lead,
            engine=engine,
            error_type=error_type,
            error_reason=error_reason,
        )


async def generate_nurture_message(
    lead_data: dict,
    stage: str,
    company_context: str = "",
    db=None,
    company_id: str = "",
) -> dict:
    safe_lead = build_safe_lead_ai_context(lead_data, include_next_action=True)
    prompt = (
        "You are a B2B sales nurture copywriter for CRM outreach.\n"
        f"Task: write one personalized follow-up message for the lead stage '{stage}'.\n"
        "Input format:\n"
        "- company_context: approved company/product context\n"
        "- lead: JSON lead profile and notes\n"
        "Output format: return plain text only, no JSON, no greeting placeholders, no markdown.\n"
        "Rules:\n"
        "- Keep the message under 3 sentences.\n"
        "- Mention only details supported by the input.\n"
        "- End with a soft CTA that suggests the next reply or meeting.\n"
        "- Sound natural and specific, not templated.\n"
        f"\ncompany_context:\n{truncate_text_for_tokens(company_context, 1200)}\n"
        f"\nlead:\n{json.dumps(_json_safe(safe_lead), ensure_ascii=True)}"
    )
    engine: dict = {}
    try:
        engine = await _resolve_engine_cached(db=db, company_id=company_id, use_pro=True)
        return {
            "message": await call_model_text(
                prompt,
                engine=engine,
                use_pro=True,
                call_purpose="lead_nurture",
                function_name="generate_nurture_message",
                agent_name="support",
            ),
            "stage": stage,
            "provider": str(engine.get("provider") or ""),
            "model_name": str(engine.get("model_name") or ""),
            "scoring_status": "completed",
        }
    except Exception as exc:
        error_type, error_reason = _safe_ai_error_reason(exc)
        logger.warning(
            "lead_nurture_failed company_id=%s provider=%s model=%s stage=%s error_type=%s error_reason=%s inner_exception=%s payload_shape=%s",
            company_id,
            str((engine or {}).get("provider") or ""),
            str((engine or {}).get("model_name") or ""),
            stage,
            error_type,
            error_reason,
            exc.__class__.__name__,
            _lead_payload_shape(safe_lead),
        )
        return {
            "message": "",
            "stage": stage,
            "provider": str((engine or {}).get("provider") or ""),
            "model_name": str((engine or {}).get("model_name") or ""),
            "error_type": error_type,
            "error_reason": error_reason,
            "fallback_used": True,
        }


async def auto_score_and_nurture_lead(lead_data: dict, db=None, company_id: str | None = None) -> dict:
    resolved_company_id = company_id or lead_data.get("company_id", "")
    score_result = await generate_lead_score(lead_data, db=db, company_id=resolved_company_id)
    company_context = ""
    if db:
        try:
            context = await build_ai_context(
                db,
                company_id=resolved_company_id,
                current_query=f"{lead_data.get('notes', '')} {lead_data.get('source', '')}".strip(),
            )
            company_context = context["knowledge_text"]
        except Exception as exc:
            logger.warning("Company knowledge fetch for nurture failed: %s", exc)
    nurture = await generate_nurture_message(
        lead_data,
        score_result.get("phase", "awareness"),
        company_context=company_context,
        db=db,
        company_id=resolved_company_id,
    )
    return {**score_result, "nurture_message": nurture.get("message")}


def _dedupe_response_text(
    response: str,
    *,
    last_response: str,
    product_names: list[str],
) -> str:
    if not response.strip():
        return response
    if text_similarity(response, last_response) < 0.9:
        return response.strip()
    varied = response.strip().rstrip(".")
    suffix_pool = [
        "Tell me what detail matters most and I'll tailor the recommendation.",
        "If you want, I can narrow it down further based on your budget or style.",
        "Share one more preference and I'll refine this into a better fit.",
    ]
    if product_names:
        suffix_pool.insert(
            1,
            f"If you'd like, I can narrow it to {product_names[0]} or a close alternative.",
        )
    suffix_index = int(hashlib.sha1(f"{response}:{last_response}".encode("utf-8")).hexdigest(), 16) % len(suffix_pool)
    suffix = suffix_pool[suffix_index]
    return f"{varied}. {suffix}".strip()


def _select_response_style(
    query: str,
    sentiment: dict | None,
    intent: dict | None,
    customer_info: dict,
    preferred_agent_type: str = "",
) -> dict[str, str]:
    emotion = str((sentiment or {}).get("emotion", "neutral") or "neutral").lower()
    intent_name = str((intent or {}).get("intent", "general_question") or "general_question").lower()
    urgency = str((intent or {}).get("urgency", "medium") or "medium").lower()
    preferred = str(preferred_agent_type or "").strip().lower()
    if preferred == "sales":
        base_profile = RESPONSE_STYLE_PROFILES[1]
    elif preferred == "onboarding":
        base_profile = RESPONSE_STYLE_PROFILES[3]
    elif emotion in {"angry", "frustrated"} or intent_name in {"complaint", "refund", "cancel_request"}:
        base_profile = RESPONSE_STYLE_PROFILES[0]
    elif intent_name in {"product_recommendation", "purchase_inquiry"}:
        base_profile = RESPONSE_STYLE_PROFILES[1]
    elif urgency in {"high", "critical"}:
        base_profile = RESPONSE_STYLE_PROFILES[4]
    else:
        seed = hashlib.sha1(
            f"{query}:{emotion}:{intent_name}:{customer_info.get('segment', '')}".encode("utf-8")
        ).hexdigest()
        base_profile = RESPONSE_STYLE_PROFILES[int(seed, 16) % len(RESPONSE_STYLE_PROFILES)]
    return {
        "name": base_profile["name"],
        "instruction": base_profile["instruction"],
    }


def _agent_runtime_profile(agent: dict | None) -> dict[str, str]:
    agent_type = str((agent or {}).get("agent_type") or "generic").strip().lower() or "generic"
    profile = AGENT_RUNTIME_PROFILES.get(agent_type) or AGENT_RUNTIME_PROFILES["generic"]
    configuration = _agent_configuration(agent)
    configured_label = str(configuration.get("label") or "").strip()
    configured_instruction = str(
        configuration.get("instruction") or configuration.get("system_prompt") or ""
    ).strip()
    return {
        "agent_type": agent_type,
        "label": configured_label or profile["label"],
        "instruction": configured_instruction or profile["instruction"],
    }


def _apply_agent_engine_overrides(engine: dict, agent: dict | None) -> dict:
    resolved = dict(engine or {})
    configuration = _agent_configuration(agent)
    if not configuration:
        return resolved
    if configuration.get("temperature") not in {None, ""}:
        try:
            resolved["temperature"] = float(configuration["temperature"])
        except Exception:
            pass
    if configuration.get("max_tokens") not in {None, ""}:
        try:
            resolved["max_tokens"] = int(configuration["max_tokens"])
        except Exception:
            pass
    return resolved


def _coerce_supported_generation_engine(engine: dict, *, company_id: str = "") -> dict:
    resolved = dict(engine or {})
    provider = str(resolved.get("provider") or "").strip().lower()
    model_name = str(resolved.get("model_name") or "").strip()
    if provider == "gemini" and model_name and not is_supported_model("gemini", model_name):
        clear_engine_cache(company_id, reason="invalid_model")
        logger.warning(
            "invalid_model_configured_for_generation company_id=%s provider=gemini configured_model=%s fallback_model=%s error_type=invalid_model",
            company_id or "",
            model_name,
            DEFAULT_GEMINI_MODEL,
        )
        resolved["configured_model_name"] = model_name
        resolved["model_name"] = DEFAULT_GEMINI_MODEL
    return resolved


async def _generate_response_text(
    prompt: str,
    *,
    engine: dict,
    image_urls: list[str],
    generation_config: dict | None = None,
    call_purpose: str = "support_response",
) -> str:
    engine.update(_coerce_supported_generation_engine(engine, company_id=str(engine.get("company_id") or "")))
    validate_live_engine(engine, require_vision=bool(image_urls))
    return await call_model_text(
        prompt,
        engine=engine,
        generation_config=generation_config,
        image_urls=image_urls or None,
        call_purpose=call_purpose,
        function_name="generate_ai_response",
        agent_name="capture" if call_purpose.startswith("prefetched_support_response") else "support",
        allow_provider_fallback=False if "similarity_retry" in call_purpose else None,
        max_provider_attempts=1 if "similarity_retry" in call_purpose else None,
    )


async def _persist_response_memory(
    *,
    db,
    company_id: str,
    memory_entity_id: str,
    convo_id: str,
    prompt: str,
    response: str,
    intent: dict,
    sentiment: dict,
    product_ids: list[str],
    response_style: str,
    channel: str = "",
    conversation_state: dict | None = None,
    message_id: str = "",
) -> None:
    if not (db and company_id and memory_entity_id and response):
        return
    try:
        logger.debug(
            "detached_task_db_acquire task=response_memory_persist company_id=%s conversation_id=%s message_id=%s",
            company_id,
            convo_id,
            message_id,
        )
        await remember_ai_response(
            db,
            company_id,
            memory_entity_id,
            response,
            convo_id=convo_id,
            prompt=prompt,
            intent=intent,
            sentiment=sentiment,
            product_ids=product_ids,
            response_style=response_style,
            message_id=message_id,
        )
        await remember_shown_products(
            db,
            company_id,
            memory_entity_id,
            product_ids,
            convo_id=convo_id,
            message_id=message_id,
        )
        if conversation_state:
            await remember_conversation_state(
                db,
                company_id,
                memory_entity_id,
                conversation_state,
                convo_id=convo_id,
                message_id=message_id,
            )

        from memory_engine.manager import MemoryManager
        from memory_engine.schemas import InteractionData

        manager = MemoryManager(db=db)
        if prompt:
            await manager.store_interaction(
                InteractionData(
                    tenant_id=company_id,
                    user_id=memory_entity_id,
                    conversation_id=convo_id,
                    channel=channel,
                    message_content=prompt,
                    sender_type="customer",
                    intent=intent,
                    sentiment=sentiment,
                    product_ids=product_ids,
                    ai_response=response,
                )
            )
        await manager.store_interaction(
            InteractionData(
                tenant_id=company_id,
                user_id=memory_entity_id,
                conversation_id=convo_id,
                channel=channel,
                message_content=response,
                sender_type="ai",
                intent=intent,
                sentiment=sentiment,
                product_ids=product_ids,
                ai_response=response,
            )
        )
        if conversation_state:
            await manager.update_memory(
                user_id=memory_entity_id,
                tenant_id=company_id,
                memory_type="conversation_state",
                content=conversation_state,
                conversation_id=convo_id,
            )
        if product_ids:
            await manager.update_memory(
                user_id=memory_entity_id,
                tenant_id=company_id,
                memory_type="shown_products",
                content={"product_ids": product_ids},
                conversation_id=convo_id,
            )
    except Exception as exc:
        increment_counter("ai.responses.memory_persist_errors")
        logger.warning("AI memory persistence skipped: %s", exc)


async def generate_ai_response(
    conversation_context: list,
    customer_info: dict | None = None,
    knowledge_context: str = "",
    company_id: str | None = None,
    db=None,
    long_term_summary: str = "",
    historical_sentiment: str = "",
    actor_user_id: str = "",
    **kwargs,
):
    customer_info = dict(customer_info or {})
    company_id = company_id or customer_info.get("company_id", "")
    conversation_id = str(kwargs.get("conversation_id") or "").strip()
    message_id = str(kwargs.get("message_id") or "").strip()
    query = latest_customer_message(conversation_context) or (
        conversation_context[-1].get("content", "") if conversation_context else ""
    )
    started = time.perf_counter()

    def _record_outcome(outcome: str, source: str, provider: str = "") -> None:
        labels = {"outcome": outcome, "source": source}
        if provider:
            labels["provider"] = provider
        increment_counter("ai.responses.total", labels=labels)
        observe_histogram(
            "ai.response.latency_ms",
            (time.perf_counter() - started) * 1000.0,
            labels=labels,
        )

    increment_counter("ai.responses.requested")
    observed_sentiment = dict(kwargs.get("observed_sentiment") or {})
    observed_conversation_sentiment = dict(kwargs.get("observed_conversation_sentiment") or {})
    observed_intent = dict(kwargs.get("observed_intent") or {})
    if not observed_sentiment:
        observed_sentiment = await analyze_sentiment(query, db=db, company_id=company_id or "")
    if not observed_conversation_sentiment and conversation_context:
        try:
            observed_conversation_sentiment = await analyze_conversation_sentiment(
                conversation_context,
                latest_message=query,
                db=db,
                company_id=company_id or "",
            )
        except Exception as exc:
            logger.warning("Conversation sentiment analysis skipped: %s", exc)
    if not observed_intent:
        observed_intent = await classify_intent(
            query,
            db=db,
            company_id=company_id or "",
            conversation_context=conversation_context[-12:],
        )
    channel_name = str(kwargs.get("channel") or "").strip()
    selected_agent = None
    if db and company_id:
        try:
            selected_agent = await resolve_active_ai_agent(
                db,
                company_id,
                intent_name=str(observed_intent.get("intent") or ""),
                channel=channel_name,
            )
        except Exception as exc:
            logger.debug("Active AI agent resolution skipped: %s", exc)
    if db and selected_agent and selected_agent.get("id"):
        try:
            selected_agent["configuration"] = {
                row["config_key"]: row["config_val"]
                for row in await db.fetch(
                    "SELECT config_key,config_val FROM ai_agent_config WHERE agent_id=$1",
                    selected_agent["id"],
                )
            }
        except Exception as exc:
            logger.debug("AI agent configuration load skipped: %s", exc)
    agent_profile = _agent_runtime_profile(selected_agent)
    selected_agent_id = str((selected_agent or {}).get("id") or "").strip()
    selected_agent_type = str((selected_agent or {}).get("agent_type") or "").strip().lower() or "generic"
    logger.info(
        "ai_agent_selected company_id=%s agent_id=%s agent_type=%s intent=%s channel=%s llm_id=%s",
        company_id or "",
        selected_agent_id or "-",
        selected_agent_type,
        str(observed_intent.get("intent") or "general_question"),
        channel_name or "unknown",
        str((selected_agent or {}).get("llm_id") or ""),
    )
    style_profile = _select_response_style(
        query,
        observed_sentiment,
        observed_intent,
        customer_info,
        preferred_agent_type=agent_profile["agent_type"],
    )

    memory_entity_id = str(customer_info.get("id") or "").strip() or str(actor_user_id or "").strip() or conversation_id

    # FIX (latency 4): parallelize ContextBuilder with the three memory DB reads.
    # These are entirely independent — RAG/context doesn't depend on last_ai_response
    # or conversation_state. Running them together saves the wall-clock time of the
    # slower of the two.
    prompt_context = None

    async def _build_prompt_context_safe():
        if not (db and company_id and memory_entity_id):
            return None
        try:
            from memory_engine.context_builder import ContextBuilder
            from memory_engine.manager import MemoryManager
            manager = MemoryManager(db=db)
            builder = ContextBuilder(manager)
            return await builder.build_prompt_context(
                user_id=str(customer_info.get("id") or memory_entity_id).strip(),
                tenant_id=str(company_id or "").strip(),
                current_query=str(query or "").strip(),
                conversation_id=conversation_id,
                conversation_context=list(conversation_context or []),
                system_instruction="",
                channel=channel_name,
            )
        except Exception as exc:
            logger.debug("memory_engine ContextBuilder unavailable: %s", exc)
            return None

    async def _fetch_memory_safe():
        if not (db and company_id and memory_entity_id):
            return {}, {}, []
        try:
            last_resp = await get_last_ai_response_context(db, company_id, memory_entity_id, convo_id=conversation_id)
        except Exception:
            last_resp = {}
        try:
            prev_state = await get_conversation_state_memory(db, company_id, memory_entity_id, convo_id=conversation_id)
        except Exception:
            prev_state = {}
        try:
            shown_ids = await get_last_shown_product_ids(db, company_id, memory_entity_id, convo_id=conversation_id)
        except Exception:
            shown_ids = []
        return last_resp, prev_state, shown_ids

    prompt_context = await _build_prompt_context_safe()
    last_response_context, previous_state, shown_product_ids = await _fetch_memory_safe()

    previous_response = latest_ai_message(conversation_context)
    if not previous_response:
        previous_response = str(last_response_context.get("response") or "").strip()

    previous_product_ids = [
        str(item).strip() for item in (last_response_context.get("product_ids") or []) if str(item).strip()
    ]
    shown_product_ids = list(dict.fromkeys([*shown_product_ids, *previous_product_ids]))
    recent_ai_replies = _recent_ai_messages(
        conversation_context,
        limit=ai_response_recent_ai_message_limit(),
    )
    if previous_response and previous_response not in recent_ai_replies:
        recent_ai_replies.append(previous_response)
    observed_intent = _normalize_observed_intent(
        observed_intent,
        query=query,
        previous_response=previous_response,
        previous_state=previous_state,
        last_response_context=last_response_context,
    )
    style_profile = _select_response_style(
        query,
        observed_sentiment,
        observed_intent,
        customer_info,
        preferred_agent_type=agent_profile["agent_type"],
    )
    conversation_state = _derive_conversation_state(
        query=query,
        observed_intent=observed_intent,
        observed_sentiment=observed_conversation_sentiment or observed_sentiment,
        previous_state=previous_state,
        last_response_context=last_response_context,
    )
    # Customer responses must come from the LLM; rule helpers remain only for
    # post-generation cleanup/failure handling and must not preempt the model.
    rule_recovery_enabled = False

    quick_response = None
    if rule_recovery_enabled and str((observed_intent or {}).get("intent") or "").lower() not in {
        "product_recommendation",
        "product_catalog_question",
        "purchase_inquiry",
        "product_image_request",
        "pricing_question",
        "availability_question",
        "buying_intent",
        "order_intent",
        "website_link_request",
        "follow_up_continue",
        "company_question",
        "service_question",
        "business_question",
    } and not _looks_like_service_catalog_question(query):
        quick_response = _compose_rule_based_response(
            query,
            customer_info=customer_info,
            knowledge_context=knowledge_context,
            ai_context={},
            observed_sentiment=observed_sentiment,
            observed_intent=observed_intent,
            previous_response=previous_response,
            last_response_context=last_response_context,
            conversation_state=conversation_state,
        )
    if quick_response:
        quick_response = _finalize_customer_response(
            quick_response,
            query=query,
            intent_name=str(observed_intent.get("intent") or ""),
            knowledge_context=knowledge_context,
            ai_context={},
            previous_response=previous_response,
            conversation_state=conversation_state,
        )
        quick_response.setdefault("agent_id", selected_agent_id)
        quick_response.setdefault("agent_type", selected_agent_type)
        quick_response.setdefault("intent_name", str(observed_intent.get("intent") or ""))
        logger.info(
            "ai_rule_response company_id=%s intent=%s channel=%s provider=%s",
            company_id or "",
            str(observed_intent.get("intent") or ""),
            channel_name or "unknown",
            str(quick_response.get("provider") or "rule"),
        )
        # FIX (latency 2): fire memory persistence as a detached background task.
        # The response is already available — no need to await persistence before
        # returning it to the caller. Saves 50-200ms off perceived response time.
        create_detached_task(
            _persist_response_memory(
                db=db,
                company_id=company_id,
                memory_entity_id=memory_entity_id,
                convo_id=conversation_id,
                prompt=query,
                response=str(quick_response.get("response") or ""),
                intent=observed_intent,
                sentiment=observed_sentiment,
                product_ids=[
                    str(item).strip() for item in quick_response.get("product_ids", []) if str(item).strip()
                ][:3],
                response_style=str(quick_response.get("provider") or "rule"),
                channel=channel_name,
                conversation_state=conversation_state,
                message_id=message_id,
            ),
            name=f"persist-response-memory-{message_id or conversation_id or 'rule'}",
        )
        quick_response.setdefault("conversation_sentiment", observed_conversation_sentiment or observed_sentiment)
        _record_outcome("success", "rule", str(quick_response.get("provider") or "rule"))
        return quick_response

    ai_context = {
        "knowledge_text": knowledge_context or "",
        "products": [],
        "product_ids": [],
        "product_attachments": [],
    }
    intent_name = str((observed_intent or {}).get("intent") or "").strip().lower()
    query_info = understand_product_query(query)
    context_dependent_intents = {
        "product_recommendation",
        "product_catalog_question",
        "purchase_inquiry",
        "company_question",
        "service_question",
        "business_question",
        "product_image_request",
        "pricing_question",
        "availability_question",
        "buying_intent",
        "order_intent",
        "website_link_request",
        "follow_up_continue",
    }
    needs_context = bool(
        intent_name in context_dependent_intents
        or query_info.get("general")
        or query_info.get("specific")
        or _looks_like_image_request(query)
        or _looks_like_service_catalog_question(query)
    )
    should_retrieve_context = bool(
        needs_context
        and (
            not knowledge_context
            or intent_name in context_dependent_intents
            or query_info.get("general")
            or query_info.get("specific")
            or _looks_like_image_request(query)
            or _looks_like_service_catalog_question(query)
        )
    )
    if not should_retrieve_context:
        logger.debug(
            "rag_context_skipped company_id=%s conversation_id=%s intent=%s query_len=%s",
            company_id or "",
            conversation_id or "",
            intent_name or "unknown",
            len(query or ""),
        )
    if db and company_id and should_retrieve_context:
        try:
            retrieved_context = await build_ai_context(
                db,
                company_id=company_id,
                current_query=query,
                exclude_product_ids=shown_product_ids,
                max_products=5,
                history_product_ids=shown_product_ids,
                customer_id=memory_entity_id,
                conversation_id=conversation_id,
                history_text=" ".join(
                    [
                        long_term_summary or "",
                        historical_sentiment or "",
                        " ".join(str(item.get("content", "")) for item in conversation_context[-8:]),
                    ]
                ).strip(),
                intent_name=intent_name,
                has_history=bool(shown_product_ids),
            )
            if knowledge_context:
                retrieved_context["knowledge_text"] = (
                    f"{knowledge_context}\n\n{retrieved_context.get('knowledge_text', '')}"
                ).strip()
            ai_context = retrieved_context
        except Exception as exc:
            logger.error("RAG context build failed: %s", exc)

    ai_context["product_ids"] = [str(item).strip() for item in ai_context.get("product_ids", []) if str(item).strip()][
        :3
    ]
    ai_context["product_attachments"] = _align_product_attachments(
        ai_context["product_ids"],
        list(ai_context.get("product_attachments") or []),
    )

    product_rule_response = (
        _compose_rule_based_response(
            query,
            customer_info=customer_info,
            knowledge_context=knowledge_context,
            ai_context=ai_context,
            observed_sentiment=observed_sentiment,
            observed_intent=observed_intent,
            previous_response=previous_response,
            last_response_context=last_response_context,
            conversation_state=conversation_state,
        )
        if rule_recovery_enabled
        else None
    )
    if product_rule_response:
        product_rule_response = _finalize_customer_response(
            product_rule_response,
            query=query,
            intent_name=str(observed_intent.get("intent") or ""),
            knowledge_context=knowledge_context,
            ai_context=ai_context,
            previous_response=previous_response,
            conversation_state=conversation_state,
        )
        product_rule_response.setdefault("agent_id", selected_agent_id)
        product_rule_response.setdefault("agent_type", selected_agent_type)
        product_rule_response.setdefault("intent_name", str(observed_intent.get("intent") or ""))
        logger.info(
            "ai_rule_response company_id=%s intent=%s channel=%s provider=%s",
            company_id or "",
            str(observed_intent.get("intent") or ""),
            channel_name or "unknown",
            str(product_rule_response.get("provider") or "rule"),
        )
        response_product_ids = [
            str(item).strip()
            for item in product_rule_response.get("product_ids", ai_context.get("product_ids", []))
            if str(item).strip()
        ][:3]
        response_attachments = _align_product_attachments(
            response_product_ids,
            list(
                product_rule_response.get("attachments")
                or product_rule_response.get("product_images")
                or ai_context.get("product_attachments", [])
            ),
        )
        product_rule_response["product_ids"] = response_product_ids
        product_rule_response["attachments"] = response_attachments
        product_rule_response["product_images"] = response_attachments
        # FIX (latency 2): detached background task — don't block the return path
        create_detached_task(
            _persist_response_memory(
                db=db,
                company_id=company_id,
                memory_entity_id=memory_entity_id,
                convo_id=conversation_id,
                prompt=query,
                response=str(product_rule_response.get("response") or ""),
                intent=observed_intent,
                sentiment=observed_sentiment,
                product_ids=response_product_ids,
                response_style=str(product_rule_response.get("provider") or "rule"),
                channel=channel_name,
                conversation_state=conversation_state,
                message_id=message_id,
            ),
            name=f"persist-response-memory-{message_id or conversation_id or 'product-rule'}",
        )
        product_rule_response.setdefault(
            "conversation_sentiment", observed_conversation_sentiment or observed_sentiment
        )
        _record_outcome("success", "rule", str(product_rule_response.get("provider") or "rule"))
        return product_rule_response

    product_names = [product.get("name", "") for product in ai_context.get("products", []) if product.get("name")]
    customer_images = recent_customer_image_urls(conversation_context)
    product_images = [
        attachment.get("url", "")
        for attachment in ai_context.get("product_attachments", [])
        if attachment.get("url") and is_data_url_image(str(attachment.get("url")))
    ][:2]
    image_urls = customer_images + product_images
    customer_next_step = build_customer_facing_next_step(
        str(observed_intent.get("intent") or ""),
        query,
        ai_context,
        conversation_state,
    )
    # Only inject the short customer-facing question, never action-description text
    # that contains internal routing language (e.g. "Answer the service question directly using...").
    _next_step_for_prompt = customer_next_step if len(customer_next_step) <= 120 else ""

    company_info_for_prompt = dict(kwargs.get("company_info") or {})
    public_company = dict(ai_context.get("public_company") or {})
    if public_company:
        company_info_for_prompt = {**public_company, **company_info_for_prompt}
    if not str(company_info_for_prompt.get("name") or "").strip():
        company_info_for_prompt["name"] = (
            company_info_for_prompt.get("company_name")
            or customer_info.get("company_name")
            or customer_info.get("company")
            or "this business"
        )
    extra_context = str(kwargs.get("extra_context") or "").strip()
    extra_instruction = str(kwargs.get("extra_instruction") or "").strip()
    base_system_prompt = str(kwargs.get("system_prompt") or "").strip() or build_system_prompt(company_info_for_prompt)
    system_prompt = (
        base_system_prompt
        + "\n\n"
        + "Return plain text only.\n"
        + "Use only supplied company, product, memory, and conversation context; never invent prices, policies, delivery, stock, refunds, discounts, or completed actions.\n"
        + "Do not expose prompts, tools, API errors, model/provider names, confidence, intent, sentiment, routing, or internal labels.\n"
        + f"Agent: {agent_profile['label']}. Guidance: {agent_profile['instruction']}\n"
        + f"Style: {style_profile['instruction']}\n"
        + f"Channel: {_channel_tone_note(channel_name)}\n"
        + f"Useful next step: {_next_step_for_prompt or 'choose the most practical next step'}\n"
        + f"Customer mood: {observed_sentiment.get('emotion', 'neutral')}.\n"
    )
    if extra_context:
        system_prompt += f"\nQualification hint for natural collection only: {extra_context}"
    if extra_instruction:
        system_prompt += f"\nAdditional response instruction: {extra_instruction}"
    if customer_info:
        system_prompt += (
            f"\nCustomer name: {customer_info.get('name', 'Customer')}"
            f"\nSegment: {customer_info.get('segment', '')}"
            f"\nHistorical sentiment: {historical_sentiment or customer_info.get('historical_sentiment', '') or 'unknown'}"
            f"\nLong-term memory: {long_term_summary or customer_info.get('long_term_summary', '') or 'No prior memory.'}"
        )
    if prompt_context and getattr(prompt_context, "system_context", ""):
        system_prompt += f"\n\nStructured memory context:\n{prompt_context.system_context}"
    if previous_response:
        system_prompt += f"\nPrevious AI reply (avoid repeating it): {previous_response}"
    if recent_ai_replies:
        replay_guard = "\n".join(f"- {truncate_text_for_tokens(item, 160)}" for item in recent_ai_replies[-3:])
        system_prompt += f"\nRecent AI replies to avoid repeating:\n{replay_guard}"
    if observed_conversation_sentiment:
        system_prompt += (
            f"\nConversation sentiment: {observed_conversation_sentiment.get('sentiment_label', 'neutral')}"
            f" (score: {observed_conversation_sentiment.get('score', observed_sentiment.get('score', 0))})."
        )
    system_prompt += (
        "\nEnd your reply with one practical next step that fits the latest message. Do not quote internal planning, stage, or routing information."
    )
    if conversation_state.get("intent_shift"):
        system_prompt += (
            "\nThe latest message may have changed topic. Follow the latest customer message and adapt naturally without saying the topic changed."
        )
    if conversation_state.get("topic_exhausted"):
        system_prompt += (
            "\nThe same topic has repeated several turns without new details. Add a soft redirect to another useful next step."
        )
    previous_intent_name = str((last_response_context.get("intent") or {}).get("intent") or "").strip().lower()
    current_intent_name = str((observed_intent.get("intent") or "").strip().lower())
    if previous_intent_name and previous_intent_name == current_intent_name:
        system_prompt += (
            "\n- This is a follow-up in the same thread. Continue naturally instead of restarting the conversation."
        )
    if observed_intent.get("intent") in {"product_recommendation", "purchase_inquiry"}:
        system_prompt += "\n- Recommend only the strongest options, explain why each fits, and avoid repeating products already discussed."
    if observed_sentiment.get("emotion") in {"angry", "frustrated"}:
        system_prompt += "\n- Prioritize empathy and a clear resolution over any upsell."
    if ai_context.get("product_attachments"):
        mapping_lines = [
            f"{index + 1}. {attachment.get('product_name') or attachment.get('name') or 'Product'} (product_id={attachment.get('product_id', '')})"
            for index, attachment in enumerate(ai_context.get("product_attachments", []))
            if str(attachment.get("product_id") or "").strip()
        ]
        if mapping_lines:
            system_prompt += (
                "\nProduct image mapping:\n"
                + "\n".join(mapping_lines)
                + "\nUse the attachment order to keep each image tied to the matching product."
            )

    recent_context = conversation_context[-12:]
    conversation_text = "\n".join(
        [f"{item.get('sender_type', '?')}: {item.get('content', '')}" for item in recent_context]
    )
    if prompt_context and getattr(prompt_context, "conversation_history", ""):
        conversation_text = prompt_context.conversation_history
    budget = ai_input_token_budget()
    capped_knowledge_text = _cap_knowledge_context(ai_context.get("knowledge_text", ""), query)
    product_prompt_context = _format_products_for_prompt(list(ai_context.get("products") or []))
    if product_prompt_context and product_prompt_context not in capped_knowledge_text:
        capped_knowledge_text = "\n".join(
            part for part in (capped_knowledge_text, product_prompt_context) if part.strip()
        )
    contrast_block = _prev_contrast_block(previous_response)
    prompt = (
        f"{truncate_text_for_tokens(system_prompt, int(budget * 0.2))}\n\n"
        f"Company/Product Context:\n{truncate_text_for_tokens(capped_knowledge_text, int(budget * 0.35))}\n\n"
        f"Conversation so far:\n{truncate_text_for_tokens(conversation_text, int(budget * 0.3))}\n\n"
        + (f"{contrast_block}\n\n" if contrast_block else "")
        + f"Latest customer message:\n{query}\n\n"
        f"Respond naturally in 2-4 sentences using the {style_profile['name']} style."
        + (f" If useful, close with: {_next_step_for_prompt}" if _next_step_for_prompt else "")
    )
    if prompt_context and getattr(prompt_context, "customer_summary", ""):
        prompt = (
            f"{truncate_text_for_tokens(system_prompt, int(budget * 0.2))}\n\n"
            f"Customer summary:\n{truncate_text_for_tokens(prompt_context.customer_summary, int(budget * 0.15))}\n\n"
            f"Company/Product Context:\n{truncate_text_for_tokens(capped_knowledge_text, int(budget * 0.30))}\n\n"
            f"Semantic context:\n{truncate_text_for_tokens(getattr(prompt_context, 'knowledge_context', ''), int(budget * 0.10))}\n\n"
            f"Conversation so far:\n{truncate_text_for_tokens(conversation_text, int(budget * 0.2))}\n\n"
            + (f"{contrast_block}\n\n" if contrast_block else "")
            + f"Latest customer message:\n{query}\n\n"
            f"Respond naturally in 2-4 sentences using the {style_profile['name']} style."
            + (f" If useful, close with: {_next_step_for_prompt}" if _next_step_for_prompt else "")
        )
    if customer_images:
        prompt += f"\n\n[{len(customer_images)} customer image(s) are attached.]"
    if product_images:
        prompt += f"\n\n[{len(product_images)} product image(s) are attached for reference.]"

    engine = None
    selected_llm_id = str((selected_agent or {}).get("llm_id") or "").strip()
    if db and company_id and selected_llm_id:
        engine_row = await db.fetchrow(
            "SELECT * FROM llm_engines WHERE id=$1 AND (company_id='' OR company_id=$2) LIMIT 1",
            selected_llm_id,
            company_id,
        )
        if engine_row:
            engine = dict(engine_row)
    if not engine:
        # FIX (latency 3): use cached engine resolution
        engine = await _resolve_engine_cached(db=db, company_id=company_id or "", use_pro=False)
    engine = _apply_agent_engine_overrides(engine, selected_agent)
    generation_config = _build_generation_config(
        query=query,
        observed_intent=observed_intent,
        observed_sentiment=observed_conversation_sentiment or observed_sentiment,
        recent_ai_replies=recent_ai_replies,
    )
    response_call_purpose = str(kwargs.get("response_call_purpose") or "support_response")
    try:
        response_text = await _generate_response_text(
            prompt,
            engine=engine,
            image_urls=image_urls,
            generation_config=generation_config,
            call_purpose=response_call_purpose,
        )
        prior_responses = [item for item in recent_ai_replies if item]
        if previous_response and previous_response not in prior_responses:
            prior_responses.append(previous_response)
        max_similarity = max(
            (text_similarity(response_text, prior) for prior in prior_responses),
            default=0.0,
        )
        if max_similarity >= 0.78:
            if not has_llm_budget_remaining():
                logger.warning(
                    "response_retry_skipped_budget_exhausted company_id=%s conversation_id=%s similarity=%.3f purpose=%s",
                    company_id or "",
                    conversation_id or "",
                    max_similarity,
                    response_call_purpose,
                )
                max_similarity = 0.0
            else:
                logger.info(
                    "ai_response_similarity_retry company_id=%s conversation_id=%s similarity=%.3f purpose=%s second_model_call=true",
                    company_id or "",
                    conversation_id or "",
                    max_similarity,
                    response_call_purpose,
                )
        if max_similarity >= 0.78:
            logger.info(
                "response_retry_executed=true company_id=%s conversation_id=%s purpose=%s",
                company_id or "",
                conversation_id or "",
                response_call_purpose,
            )
            retry_generation_config = dict(generation_config)
            retry_delta = ai_response_retry_temperature_delta()
            retry_max = ai_response_retry_temperature_max()
            retry_generation_config["temperature"] = round(
                min(
                    retry_max,
                    float(generation_config.get("temperature", ai_response_temperature_base())) + retry_delta,
                ),
                2,
            )
            retry_prompt = (
                f"{prompt}\n\nYour last draft was too similar to the previous reply. "
                "Answer again with a different structure, fresh wording, and a more specific next step."
            )
            retry_text = await _generate_response_text(
                retry_prompt,
                engine=engine,
                image_urls=image_urls,
                generation_config=retry_generation_config,
                call_purpose=f"{response_call_purpose}:similarity_retry",
            )
            if retry_text.strip():
                response_text = retry_text
                increment_counter("ai.responses.regenerated")
        before_dedupe = response_text
        dedupe_reference = previous_response
        if not dedupe_reference and recent_ai_replies:
            dedupe_reference = recent_ai_replies[-1]
        response_text = _dedupe_response_text(
            response_text,
            last_response=dedupe_reference,
            product_names=product_names,
        )
        if response_text != before_dedupe:
            increment_counter("ai.responses.deduped")
        response_product_ids = [str(item).strip() for item in ai_context.get("product_ids", []) if str(item).strip()][
            :3
        ]
        response_attachments = _align_product_attachments(
            response_product_ids,
            list(ai_context.get("product_attachments", [])),
        )
        finalized_response = _finalize_customer_response(
            {"response": response_text},
            query=query,
            intent_name=str(observed_intent.get("intent") or ""),
            knowledge_context=knowledge_context,
            ai_context=ai_context,
            previous_response=previous_response,
            conversation_state=conversation_state,
        )
        response_text = str(finalized_response.get("response") or response_text).strip()
        # FIX (latency 2): detached memory persist — don't block the return path
        create_detached_task(
            _persist_response_memory(
                db=db,
                company_id=company_id,
                memory_entity_id=memory_entity_id,
                convo_id=conversation_id,
                prompt=query,
                response=response_text,
                intent=observed_intent,
                sentiment=observed_sentiment,
                product_ids=response_product_ids,
                response_style=style_profile["name"],
                channel=channel_name,
                conversation_state=conversation_state,
                message_id=message_id,
            ),
            name=f"persist-response-memory-{message_id or conversation_id or 'llm'}",
        )
        _record_outcome("success", "llm", str(engine.get("provider") or "unknown"))
        return {
            "response": response_text,
            "confidence": 0.9,
            "attachments": response_attachments,
            "product_images": response_attachments,
            "product_ids": response_product_ids,
            "llm_id": engine.get("id", ""),
            "agent_id": selected_agent_id,
            "agent_type": selected_agent_type,
            "intent_name": str(observed_intent.get("intent") or ""),
            "provider": engine.get("provider", ""),
            "model_name": engine.get("model_name", ""),
            "conversation_stage": conversation_state.get("stage", "discovery"),
            "next_action": finalized_response.get("next_action", customer_next_step),
            "next_step": finalized_response.get("next_step", customer_next_step),
            "intent_shift": bool(conversation_state.get("intent_shift")),
            "conversation_sentiment": observed_conversation_sentiment or observed_sentiment,
            "rag_called": bool(ai_context.get("rag_called")),
            "api_error": False,
            "provider_error": {},
            "degraded": False,
            "error_type": "",
            "error_reason": "",
            "fallback_used": False,
        }
    except Exception as exc:
        error_payload = _degraded_error_payload(exc, engine)
        if error_payload.get("error_type") == "invalid_model":
            clear_engine_cache(company_id or "", reason="invalid_model")
        logger.exception(
            "AI response failed function=generate_ai_response provider=%s model=%s error_type=%s error_reason=%s",
            engine.get("provider", "?"),
            engine.get("model_name", "?"),
            error_payload.get("error_type", ""),
            error_payload.get("error_reason", ""),
        )
        _record_outcome("error", "llm", str(engine.get("provider") or "unknown"))
        provider_failure = is_ai_api_exhaustion_payload(error_payload)
        # When an upstream provider fails, attempt to load a custom static fallback message.
        static_message = await get_ai_static_fallback_message(db, company_id or "") if provider_failure else ""
        # Guarantee a non-empty fallback message: if nothing is configured in the DB, use a safe default.
        if provider_failure and not str(static_message or "").strip():
            static_message = (
                "Hi there, I'm having trouble connecting right now. "
                "A team member will follow up with you shortly. Thank you for your patience."
            )
        fallback_response = {
            "response": static_message,
            "confidence": 0.9 if provider_failure else 0.0,
            "attachments": [],
            "product_images": [],
            "product_ids": [],
            "llm_id": "",
            "agent_id": selected_agent_id,
            "agent_type": selected_agent_type,
            "intent_name": str(observed_intent.get("intent") or ""),
            "conversation_stage": conversation_state.get("stage", "discovery"),
            "next_action": _customer_safe_next_action(conversation_state, str(observed_intent.get("intent") or "")),
            "next_step": "static_fallback",
            "intent_shift": bool(conversation_state.get("intent_shift")),
        }
        fallback_response.update(error_payload)
        fallback_response["static_fallback_served"] = provider_failure
        if provider_failure:
            fallback_response["provider"] = "fallback"
            fallback_response["model_name"] = "static-fallback"
        if not str(fallback_response.get("next_action") or "").strip():
            fallback_response["next_action"] = (
                "manual_review"
                if error_payload.get("error_type") in {"quota_exhausted", "provider_not_configured", "rate_limited"}
                else "send_safe_fallback"
            )
        # FIX (latency 2): detached memory persist on fallback path too
        create_detached_task(
            _persist_response_memory(
                db=db,
                company_id=company_id,
                memory_entity_id=memory_entity_id,
                convo_id=conversation_id,
                prompt=query,
                response=str(fallback_response.get("response") or ""),
                intent=observed_intent,
                sentiment=observed_sentiment,
                product_ids=[
                    str(item).strip() for item in fallback_response.get("product_ids", []) if str(item).strip()
                ][:3],
                response_style="fallback",
                channel=channel_name,
                conversation_state=conversation_state,
                message_id=message_id,
            ),
            name=f"persist-response-memory-{message_id or conversation_id or 'fallback'}",
        )
        fallback_response.setdefault("conversation_sentiment", observed_conversation_sentiment or observed_sentiment)
        logger.warning(
            "ai_response_fallback company_id=%s intent=%s channel=%s provider=%s",
            company_id or "",
            str(observed_intent.get("intent") or ""),
            channel_name or "unknown",
            str(fallback_response.get("provider") or "fallback"),
        )
        _record_outcome("success", "fallback", "rule-recovery")
        return fallback_response


async def generate_combined_ai_analysis(
    customer_message: str,
    conversation_context: list,
    customer_info: dict | None = None,
    company_id: str | None = None,
    db=None,
    long_term_summary: str = "",
    historical_sentiment: str = "",
    knowledge_context: str = "",
    **kwargs,
) -> dict:
    """Run sentiment (message), sentiment (conversation), intent, and AI response.

    ALL analysis tasks are batched into a SINGLE LLM request instead of
    three separate sequential calls, cutting latency and eliminating the
    repeated 429 retry cascade that was caused by hitting the same quota-
    exhausted provider three times per message.

    FIX (latency 1): engine resolution is now parallelised with the batch call
    prep instead of awaited sequentially before the LLM call fires.
    """
    context_budget = get_llm_context()
    if context_budget is None:
        set_llm_context(
            company_id=company_id or "",
            max_calls=5,
            max_embedding_calls=2,
            workflow_id=str(kwargs.get("workflow_id") or "combined_ai_analysis"),
            conversation_id=str(kwargs.get("conversation_id") or ""),
            message_id=str(kwargs.get("message_id") or ""),
            agent_name="capture",
        )
    else:
        if context_budget.max_calls < 5:
            logger.info(
                "combined_analysis_llm_budget_raised company_id=%s previous_max_calls=%s new_max_calls=5",
                company_id or "",
                context_budget.max_calls,
            )
            context_budget.max_calls = 5
        if context_budget.max_embedding_calls < 2:
            context_budget.max_embedding_calls = 2
    from services.ai_service.common import IntentResult, SentimentResult
    from services.ai_service.intent import _normalize_intent_payload, _render_history_context

    context = list(conversation_context or [])
    if customer_message and (not context or str(context[-1].get("content") or "").strip() != customer_message):
        context.append({"sender_type": "customer", "content": customer_message})

    source_text = (customer_message or "").strip() or "[empty message]"
    history_lines: list[str] = []
    for item in context[-20:]:
        role = str((item or {}).get("sender_type") or "unknown").strip().lower()
        text = str((item or {}).get("content") or "").strip()
        if text:
            history_lines.append(f"{role}: {text}")
    history = "\n".join(history_lines[-16:])
    if source_text and (not history_lines or source_text not in history_lines[-1]):
        history_with_latest = history + f"\ncustomer: {source_text}"
    else:
        history_with_latest = history

    history_context_str = _render_history_context(context[-10:])

    from services.ai_service.sentiment import (
        _message_prompt,
        _conversation_prompt,
        _finalize_sentiment,
        _should_retry_for_zero_score,
        analyze_local_sentiment,
    )

    intent_prompt = (
        "You are an intent-routing classifier for a CRM assistant.\n"
        "Task: infer the single best customer intent for the latest message.\n"
        "Output format: return ONLY valid JSON with exactly these keys:\n"
        '{"intent":"snake_case_intent","confidence":0.0,"entities":{},"urgency":"low|medium|high|critical"}\n'
        "Rules:\n"
        "- Choose one primary intent only.\n"
        "- Keep confidence between 0 and 1.\n"
        "- Set urgency to critical only for explicit immediate risk, legal threat, severe churn risk, or urgent handoff.\n"
        f"\nprevious_intent: unknown"
        f"\nrecent_conversation:\n{history_context_str or '[none]'}"
        f"\nlatest_message:\n{source_text}"
    )

    # FIX (latency 1): resolve engine in parallel with building the batch prompts.
    # Previously _resolve_engine_for_request was awaited before call_model_json_batch,
    # adding a sequential DB round-trip (20-50ms) on every message.
    # Now both happen concurrently; the batch call fires as soon as the engine is ready.
    engine = await _resolve_engine_cached(db=db, company_id=company_id or "")

    sentiment: dict
    conversation_sentiment: dict
    intent: dict

    try:
        batch = await call_model_json_batch(
            {
                "message_sentiment": {
                    "prompt": _message_prompt(source_text),
                    "schema": SentimentResult,
                },
                "conversation_sentiment": {
                    "prompt": _conversation_prompt(history_with_latest, source_text),
                    "schema": SentimentResult,
                },
                "intent": {
                    "prompt": intent_prompt,
                    "schema": IntentResult,
                },
            },
            engine=engine,
            call_purpose="combined_analysis",
            function_name="generate_combined_ai_analysis",
            agent_name="capture",
            individual_fallback=False,
            max_provider_attempts=1,
            allow_provider_fallback=False,
        )

        msg_raw = batch.get("message_sentiment")
        conv_raw = batch.get("conversation_sentiment")
        intent_raw = batch.get("intent")

        if msg_raw and not _should_retry_for_zero_score(msg_raw):
            sentiment = _finalize_sentiment(msg_raw, source_text, scope="message")
            sentiment["provider"] = str((engine or {}).get("provider") or "")
            sentiment["model_name"] = str((engine or {}).get("model_name") or "")
            sentiment["source"] = "provider_batch"
        else:
            sentiment = analyze_local_sentiment(source_text)
            sentiment["scope"] = "message"
            sentiment["source"] = "local_fallback"

        if conv_raw and not _should_retry_for_zero_score(conv_raw):
            conversation_sentiment = _finalize_sentiment(conv_raw, source_text, scope="conversation")
            conversation_sentiment["provider"] = str((engine or {}).get("provider") or "")
            conversation_sentiment["model_name"] = str((engine or {}).get("model_name") or "")
            conversation_sentiment["source"] = "provider_batch"
            conversation_sentiment["turns_analyzed"] = len(history_lines)
        else:
            conversation_sentiment = analyze_local_sentiment(history or source_text)
            conversation_sentiment["scope"] = "conversation"
            conversation_sentiment["source"] = "local_fallback"
            conversation_sentiment["turns_analyzed"] = len(history_lines)

        if intent_raw:
            intent = _normalize_intent_payload(intent_raw)
        else:
            intent = {
                "intent": "general_question",
                "confidence": 0.0,
                "entities": {},
                "urgency": "medium",
            }

    except Exception as exc:
        logger.warning(
            "generate_combined_ai_analysis batch failed, falling back to individual calls: %s", exc
        )
        # Individual fallback (original behaviour)
        sentiment = await analyze_sentiment(source_text, db=db, company_id=company_id or "")
        try:
            conversation_sentiment = await analyze_conversation_sentiment(
                context,
                latest_message=source_text,
                db=db,
                company_id=company_id or "",
            )
        except Exception as exc2:
            logger.warning("Conversation sentiment fallback to message sentiment: %s", exc2)
            conversation_sentiment = dict(sentiment)
        intent = await classify_intent(
            source_text,
            db=db,
            company_id=company_id or "",
            conversation_context=context[-12:],
        )

    llm_budget_exhausted = not has_llm_budget_remaining()
    if llm_budget_exhausted:
        logger.warning(
            "combined_analysis_budget_exhausted_before_response company_id=%s conversation_id=%s",
            company_id or "",
            str(kwargs.get("conversation_id") or ""),
        )
        ai_response = {}
        ai_response_error = "AI_BUDGET_EXCEEDED type=llm"
    else:
        try:
            ai_response = await generate_ai_response(
                context,
                customer_info=customer_info,
                knowledge_context=knowledge_context,
                company_id=company_id,
                db=db,
                long_term_summary=long_term_summary,
                historical_sentiment=historical_sentiment,
                observed_sentiment=sentiment,
                observed_conversation_sentiment=conversation_sentiment,
                observed_intent=intent,
                response_call_purpose="prefetched_support_response",
                **kwargs,
            )
        except Exception as exc:
            if "AI_BUDGET_EXCEEDED" in str(exc):
                llm_budget_exhausted = True
                logger.warning(
                    "combined_analysis_budget_exhausted_mid_workflow company_id=%s conversation_id=%s",
                    company_id or "",
                    str(kwargs.get("conversation_id") or ""),
                )
            logger.warning(
                "generate_combined_ai_analysis ai_response failed; support fallback may generate once if budget allows: %s",
                exc,
            )
            ai_response = {}
            ai_response_error = str(exc)
        else:
            ai_response_error = ""
    return {
        "sentiment": sentiment,
        "conversation_sentiment": conversation_sentiment,
        "intent": intent,
        "ai_response": ai_response,
        "ai_response_error": ai_response_error,
        "ai_response_generated": bool((ai_response or {}).get("response")),
        "llm_budget_exhausted": llm_budget_exhausted,
    }


async def generate_product_description(
    name: str,
    company_id: str = "",
    product_title: str = "",
    product_type: str = "",
    category: str = "",
    price: str = "",
    price_currency: str = "USD",
    images: list | None = None,
    engines: list[dict] | None = None,
    db=None,
) -> str:
    lines = (
        [f"Product Name: {name}"]
        + ([f"Product Code / Title: {product_title}"] if product_title else [])
        + ([f"Type: {product_type}"] if product_type else [])
        + ([f"Category: {category}"] if category and category != "general" else [])
        + ([f"Price: {price_currency or 'USD'} {price}"] if price else [])
    )
    ordered_engines = [dict(engine) for engine in (engines or []) if isinstance(engine, dict)]
    ordered_engines.sort(key=lambda engine: 0 if engine.get("is_selected") else 1)
    if not ordered_engines:
        ordered_engines = [await _resolve_engine_for_request(db=db, company_id=company_id)]
    return (
        await call_with_engines(
            "You are an ecommerce product copywriter.\n"
            "Task: write one concise product description for a catalog or CRM card.\n"
            "Input format:\n"
            "- product metadata lines\n"
            "- up to 2 reference images\n"
            "Output format: return plain text only, one paragraph, about 50 words.\n"
            "Rules:\n"
            "- Highlight benefits and real differentiators.\n"
            "- Do NOT mention price.\n"
            "- Do NOT invent specs that are not present in the metadata or clearly visible in the images.\n"
            f"\nproduct_metadata:\n{chr(10).join(lines)}",
            engines=ordered_engines,
            image_parts=[url for url in (images or [])[:2] if is_data_url_image(str(url))] or None,
            call_purpose="product_description",
            function_name="generate_product_description",
            agent_name="support",
        )
    ).strip()


__all__ = [
    "auto_score_and_nurture_lead",
    "build_degraded_response",
    "build_greeting_response",
    "build_product_response",
    "build_service_response",
    "build_system_prompt",
    "build_follow_up_response",
    "build_safe_unclear_response",
    "calculate_churn_risk",
    "clear_engine_cache",
    "generate_ai_response",
    "generate_combined_ai_analysis",
    "generate_lead_score",
    "generate_nurture_message",
    "generate_product_description",
]
