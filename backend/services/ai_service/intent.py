from __future__ import annotations

import logging

from services.ai_service.common import IntentResult
from services.ai_service.llm_client import _resolve_engine_for_request, call_model_json

logger = logging.getLogger(__name__)

_ALLOWED_URGENCY = {"low", "medium", "high", "critical"}
_QUOTA_MARKERS = ("resource_exhausted", "quota", "rate-limit", "rate limit", "429")


def _normalize_intent_payload(raw: dict) -> dict:
    payload = dict(raw or {})
    intent_name = str(payload.get("intent") or "general_question").strip().lower()
    if not intent_name:
        intent_name = "general_question"
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
    for item in (conversation_context or [])[-10:]:
        if not isinstance(item, dict):
            continue
        sender = str(item.get("sender_type") or "unknown").strip().lower()
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{sender}: {content}")
    return "\n".join(lines)


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
    intent_name = previous_intent if previous_intent and previous_intent != "unknown" else "general_question"
    confidence = 0.15
    if any(token in lower for token in ("price", "cost", "buy", "purchase", "available", "product")):
        intent_name = "product_interest"
        confidence = 0.35
    elif any(token in lower for token in ("refund", "cancel", "complaint", "broken", "issue", "problem")):
        intent_name = "support_request"
        confidence = 0.35
    elif any(token in lower for token in ("order", "delivery", "shipping", "tracking")):
        intent_name = "order_status"
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
    prompt = (
        "You are an expert intent-routing classifier for a CRM/customer-support AI assistant.\n"
        "Your job is to infer the single best customer intent from the latest customer message, using the recent conversation only as context.\n"
        "This classification will be used by downstream automation to decide whether to answer, ask a follow-up question, recommend a product, create a lead, escalate to a human, send a payment link, or take another CRM action.\n\n"

        "INPUTS YOU WILL RECEIVE:\n"
        "- previous_intent: the most recent known intent, or 'unknown' if no reliable intent exists.\n"
        "- recent_conversation: the latest customer/agent/AI turns, if available. Use this only to disambiguate the newest message.\n"
        "- latest_message: the newest customer message. This is the most important input.\n\n"

        "OUTPUT REQUIREMENT:\n"
        "Return ONLY valid JSON. Do not include markdown, comments, explanations, code fences, or extra text.\n"
        "The JSON object must contain exactly these keys:\n"
        '{"intent":"snake_case_intent","confidence":0.0,"entities":{},"urgency":"low|medium|high|critical"}\n\n'

        "INTENT SELECTION RULES:\n"
        "- Choose exactly one primary intent.\n"
        "- The intent must be written in clear snake_case.\n"
        "- Prefer a specific intent when the customer message clearly indicates one.\n"
        "- If the latest message is short but the recent conversation clearly explains it, use the conversation context.\n"
        "- If the latest message changes the topic, classify the latest message, not the older topic.\n"
        "- If the message is only a greeting, thanks, emoji, or very vague text, use a general intent with lower confidence.\n"
        "- If the customer is asking about price, cost, rates, packages, discounts, or payment, classify it as a pricing/payment-related intent.\n"
        "- If the customer asks for product details, availability, images, catalog, features, size, color, stock, or recommendations, classify it as a product/service inquiry intent.\n"
        "- If the customer wants to buy, book, order, reserve, confirm, subscribe, or proceed, classify it as a purchase/booking/conversion intent.\n"
        "- If the customer complains, reports an issue, asks for support, refund, cancellation, replacement, or says something is not working, classify it as a support/complaint intent.\n"
        "- If the customer asks to speak to a person, manager, owner, agent, or support team, classify it as a human handoff/escalation intent.\n"
        "- If the message contains rejection such as 'not interested', 'stop', 'don't message me', or 'no thanks', classify it as a rejection/opt-out intent.\n"
        "- If the customer is negotiating price, asking for a discount, requesting final price, or objecting to terms, classify it as negotiation intent.\n"
        "- If none of the above clearly applies, choose a broad but useful intent such as general_question, general_interest, greeting, or unclear_request.\n\n"

        "CONFIDENCE RULES:\n"
        "- confidence must be a number between 0 and 1.\n"
        "- Use 0.90 to 1.00 only when the intent is explicit and unambiguous.\n"
        "- Use 0.70 to 0.89 when the intent is likely but needs mild interpretation.\n"
        "- Use 0.40 to 0.69 when the message is ambiguous, short, or depends heavily on context.\n"
        "- Use below 0.40 only when the message is unclear, incomplete, spam-like, or not enough information is available.\n\n"

        "ENTITY EXTRACTION RULES:\n"
        "- entities must be a JSON object.\n"
        "- Extract only factual information explicitly present in the latest message or recent conversation.\n"
        "- Do not invent IDs, prices, products, dates, names, locations, phone numbers, emails, order numbers, or quantities.\n"
        "- Preserve numbers, product names, dates, times, emails, phone numbers, locations, order IDs, and budget values when explicitly provided.\n"
        "- If no factual entities are present, return an empty object: {}.\n"
        "- Good entity examples include: product_name, service_name, budget, quantity, location, date, time, order_id, phone, email, preferred_channel, issue_type.\n\n"

        "URGENCY RULES:\n"
        "- urgency must be one of: low, medium, high, critical.\n"
        "- Use low for greetings, general questions, casual interest, simple product inquiries, or normal follow-ups.\n"
        "- Use medium for purchase intent, pricing questions, booking requests, product availability, or normal support requests.\n"
        "- Use high for angry complaints, repeated unresolved issues, refund/cancellation pressure, strong churn risk, urgent deadlines, or explicit request for human help.\n"
        "- Use critical only for explicit immediate risk, legal threat, safety issue, fraud/security concern, severe escalation, or urgent human handoff.\n"
        "- Do not mark critical unless the customer clearly expresses immediate serious risk or severe escalation.\n\n"

        "CONTEXT HANDLING:\n"
        "- previous_intent can help maintain continuity, but it must not override a clear new intent in latest_message.\n"
        "- recent_conversation can clarify pronouns like 'it', 'that one', 'same', 'how much', or 'send it'.\n"
        "- If latest_message is a short reply such as 'yes', 'ok', 'send', or 'price?', infer intent from recent_conversation and lower confidence if still uncertain.\n"
        "- If recent_conversation is missing, classify using latest_message only.\n\n"

        "SAFETY AND ACCURACY:\n"
        "- Do not answer the customer.\n"
        "- Do not perform any action.\n"
        "- Do not include reasoning.\n"
        "- Do not include multiple intents.\n"
        "- Do not include fields outside the required JSON keys.\n"
        "- Ensure the JSON is parseable by Python json.loads.\n\n"

        "NOW CLASSIFY THE CUSTOMER MESSAGE.\n\n"
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
            return _normalize_intent_payload(remote)
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
            continue
    fallback = _fallback_intent(text, previous_intent=previous_intent, exc=last_exc)
    logger.warning(
        "intent_classification_degraded company_id=%s intent=%s error_type=%s error_reason=%s",
        company_id or "",
        fallback.get("intent", ""),
        fallback.get("error_type", ""),
        fallback.get("error_reason", ""),
    )
    return fallback


__all__ = ["classify_intent"]
