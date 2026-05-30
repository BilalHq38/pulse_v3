from __future__ import annotations

import asyncio
import logging
import random
import re

from services.ai_service.common import IntentResult
from services.ai_service.llm_client import _resolve_engine_for_request, call_model_json
from services.ai_service.routing_guards import is_low_value_message, lightweight_route_message

logger = logging.getLogger(__name__)

_ALLOWED_URGENCY = {"low", "medium", "high", "critical"}
_QUOTA_MARKERS = ("resource_exhausted", "quota", "rate-limit", "rate limit", "429")
_INTENT_ALIASES = {
    "product_interest": "product_recommendation",
    "product_inquiry": "product_catalog_question",
    "product_question": "product_catalog_question",
    "catalog_question": "product_catalog_question",
    "price_question": "pricing_question",
    "pricing_payment": "pricing_question",
    "payment_question": "pricing_question",
    "order_status": "shipping_question",
    "delivery_question": "shipping_question",
    "purchase": "buying_intent",
    "purchase_intent": "buying_intent",
    "conversion_intent": "buying_intent",
    "service_inquiry": "service_question",
    "service_info": "service_question",
    "company_info": "company_question",
    "business_inquiry": "business_question",
    "continue": "follow_up_continue",
    "next": "follow_up_continue",
    "opt_out": "rejection_or_opt_out",
    "escalation": "human_handoff",
}


def _normalize_intent_payload(raw: dict) -> dict:
    payload = dict(raw or {})
    intent_name = str(payload.get("intent") or "general_question").strip().lower()
    if not intent_name:
        intent_name = "general_question"
    intent_name = _INTENT_ALIASES.get(intent_name, intent_name)
    confidence = float(payload.get("confidence") or 0.0)
    confidence = max(0.0, min(1.0, confidence))
    urgency = str(payload.get("urgency") or "medium").strip().lower()
    if urgency not in _ALLOWED_URGENCY:
        urgency = "medium"
    entities = payload.get("entities") if isinstance(payload.get("entities"), dict) else {}
    return {
        "intent": intent_name,
        "confidence": confidence,
        "entities": entities,
        "urgency": urgency,
    }


def _render_history_context(conversation_context: list | None) -> str:
    lines: list[str] = []
    for item in (conversation_context or [])[-80:]:
        if not isinstance(item, dict):
            continue
        sender = str(item.get("sender_type") or "unknown").strip().lower()
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{sender}: {content}")
    return "\n".join(lines)


def is_short_follow_up_message(text: str, previous_ai_question: str = "") -> bool:
    normalized = re.sub(r"[^a-z0-9\s]", " ", str(text or "").strip().lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized in {
        "next",
        "yes",
        "yeah",
        "yep",
        "ok",
        "okay",
        "sure",
        "sure go ahead",
        "go ahead",
        "continue",
        "tell me more",
        "tell me about it",
        "what else",
        "show me more",
        "more",
    }:
        return True
    previous = str(previous_ai_question or "").lower()
    if previous and len(normalized.split()) <= 5:
        return any(marker in previous for marker in ("which", "do you want", "would you like", "details about"))
    return False


def _extract_last_topic(conversation_context: list | None) -> str:
    candidates: list[str] = []
    for item in conversation_context or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "")
        for match in re.finditer(r"\b([A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*){0,4})\b", content):
            value = " ".join(match.group(1).split()).strip()
            if value.lower() in {"I", "We", "You", "Hi", "Hello", "Thanks"}:
                continue
            candidates.append(value)
    return candidates[-1] if candidates else ""


def _safe_intent_error(exc: Exception | None) -> tuple[str, str]:
    if exc is None:
        return "unknown_error", "Intent classifier unavailable"
    message = " ".join(str(exc or "").split())
    lowered = message.lower()
    if any(marker in lowered for marker in _QUOTA_MARKERS):
        return "quota_exhausted", "AI provider quota exhausted"
    if "no configured ai providers" in lowered or "api key" in lowered or "not configured" in lowered:
        return "provider_not_configured", "AI provider not configured"
    return exc.__class__.__name__, message[:240] or exc.__class__.__name__


def _fallback_intent(text: str, previous_intent: str = "", exc: Exception | None = None) -> dict:
    lower = (text or "").lower()
    normalized = " ".join("".join(ch if ch.isalnum() or ch.isspace() else " " for ch in lower).split())
    intent_name = previous_intent if previous_intent and previous_intent != "unknown" else "general_question"
    confidence = 0.15
    lightweight = lightweight_route_message(text, previous_intent=previous_intent)
    if lightweight:
        intent_name = str(lightweight.get("intent") or intent_name)
        confidence = max(float(lightweight.get("confidence") or 0.0), 0.5 if is_low_value_message(text) else 0.35)
    elif is_short_follow_up_message(text) or normalized in {"next", "continue", "show", "send", "proceed", "go ahead", "tell me more", "more"}:
        intent_name = "follow_up_continue"
        confidence = 0.5
    elif any(token in lower for token in ("image", "photo", "picture", "catalog", "show me")):
        intent_name = "product_image_request"
        confidence = 0.4
    elif any(phrase in lower for phrase in ("where can i buy", "where can i order", "order link", "website", "buy link")):
        intent_name = "website_link_request"
        confidence = 0.45
    elif any(phrase in lower for phrase in ("i want to buy", "want to buy", "i want this", "want this", "purchase", "place order", "how can i order")):
        intent_name = "buying_intent"
        confidence = 0.45
    elif any(token in lower for token in ("price", "cost", "how much", "rate", "charges")):
        intent_name = "pricing_question"
        confidence = 0.4
    elif any(token in lower for token in ("available", "availability", "stock", "in stock")):
        intent_name = "availability_question"
        confidence = 0.4
    elif any(token in lower for token in ("product", "products", "item", "items", "catalog")):
        intent_name = "product_catalog_question"
        confidence = 0.35
    elif any(token in lower for token in ("service", "services", "offer", "provide", "providing")):
        intent_name = "service_question"
        confidence = 0.35
    elif any(phrase in lower for phrase in ("who are you", "about your company", "your business", "your brand")):
        intent_name = "company_question"
        confidence = 0.35
    elif any(token in lower for token in ("refund", "cancel", "complaint", "broken", "issue", "problem")):
        intent_name = "support_request"
        confidence = 0.35
    elif any(token in lower for token in ("order", "delivery", "shipping", "tracking")):
        intent_name = "shipping_question"
        confidence = 0.3
    elif any(token in lower for token in ("hi", "hello", "hey", "salam", "assalam")):
        intent_name = "greeting"
        confidence = 0.25
    urgency = "high" if any(token in lower for token in ("urgent", "immediately", "asap", "legal")) else "low"
    error_type, error_reason = _safe_intent_error(exc)
    result = IntentResult(
        intent=intent_name,
        confidence=confidence,
        entities={},
        urgency=urgency,
    ).model_dump()
    result.update(
        {
            "source": "local_fallback",
            "error_type": error_type,
            "error_reason": error_reason,
        }
    )
    return result


async def classify_intent(text: str, db=None, company_id: str = "", **kwargs) -> dict:
    history_context = _render_history_context(kwargs.get("conversation_context"))
    previous_intent = str(kwargs.get("previous_intent") or "").strip().lower()
    lightweight = lightweight_route_message(
        text,
        previous_intent=previous_intent,
        previous_ai_message=str(kwargs.get("previous_ai_message") or ""),
    )
    if lightweight:
        logger.info(
            "intent_llm_skipped company_id=%s reason=lightweight_route intent=%s confidence=%s",
            company_id or "",
            str(lightweight.get("intent") or ""),
            lightweight.get("confidence"),
        )
        return _normalize_intent_payload(lightweight)
    prompt = (
        "Classify the latest CRM customer message.\n"
        "Return ONLY JSON: {\"intent\":\"snake_case_intent\",\"confidence\":0.0,\"entities\":{},\"urgency\":\"low|medium|high|critical\"}\n"
        "Allowed intents: greeting, gratitude, general_question, unclear_request, company_question, service_question, business_question, "
        "follow_up_continue, product_catalog_question, product_recommendation, purchase_inquiry, product_image_request, pricing_question, "
        "availability_question, buying_intent, order_intent, website_link_request, support_request, complaint, refund, cancel_request, "
        "shipping_question, negotiation, human_handoff, rejection_or_opt_out.\n"
        "Rules: choose one intent; classify the latest message over older context; use context only for short follow-ups; "
        "extract only explicit entities; urgency low for routine, medium for buying/pricing/support, high for anger or human-help pressure, "
        "critical only for legal, safety, fraud, security, or immediate severe escalation.\n"
        f"previous_intent: {previous_intent or 'unknown'}\n"
        f"recent_conversation:\n{history_context or '[none]'}\n"
        f"latest_message:\n{text}"
    )
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            logger.info("intent_llm_called company_id=%s attempt=%s", company_id or "", attempt + 1)
            remote = await call_model_json(
                prompt,
                IntentResult,
                engine=await _resolve_engine_for_request(db=db, company_id=company_id),
                call_purpose="intent_classification",
                function_name="classify_intent",
            )
            normalized = _normalize_intent_payload(remote)
            last_topic = _extract_last_topic(kwargs.get("conversation_context"))
            if last_topic and isinstance(normalized.get("entities"), dict):
                normalized["entities"].setdefault("last_topic", last_topic)
            return normalized
        except Exception as exc:
            last_exc = exc
            error_type, error_reason = _safe_intent_error(exc)
            logger.warning(
                "intent_llm_attempt_failed company_id=%s attempt=%s fallback_call=%s error_type=%s error_reason=%s",
                company_id or "",
                attempt + 1,
                attempt > 0,
                error_type,
                error_reason,
            )
            # Exponential backoff with jitter to prevent retry storms
            await asyncio.sleep(0.15 * (2 ** attempt) + random.uniform(0, 0.1))
            continue
    fallback = _fallback_intent(text, previous_intent=previous_intent, exc=last_exc)
    logger.warning(
        "intent_classification_degraded company_id=%s intent=%s error_type=%s error_reason=%s",
        company_id or "",
        fallback.get("intent", ""),
        fallback.get("error_type", ""),
        fallback.get("error_reason", ""),
    )
    last_topic = _extract_last_topic(kwargs.get("conversation_context"))
    if last_topic and isinstance(fallback.get("entities"), dict):
        fallback["entities"].setdefault("last_topic", last_topic)
    return fallback


__all__ = ["classify_intent", "is_short_follow_up_message"]
