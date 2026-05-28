from __future__ import annotations

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_ALLOWED_INTENTS = {
    "greeting",
    "gratitude",
    "general_question",
    "unclear_request",
    "company_question",
    "service_question",
    "business_question",
    "follow_up_continue",
    "product_catalog_question",
    "product_recommendation",
    "purchase_inquiry",
    "product_image_request",
    "pricing_question",
    "availability_question",
    "buying_intent",
    "order_intent",
    "website_link_request",
    "support_request",
    "complaint",
    "refund",
    "cancel_request",
    "shipping_question",
    "negotiation",
    "human_handoff",
    "rejection_or_opt_out",
    "lead_capture",
}

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

_ALLOWED_URGENCY = {"low", "medium", "high", "critical"}
_ALLOWED_SENTIMENT_LABELS = {"positive", "neutral", "negative"}
_ALLOWED_SENTIMENT_TRENDS = {"improving", "stable", "declining"}
_ALLOWED_LEAD_GRADES = {"hot", "warm", "cold", "deferred"}
_ALLOWED_LEAD_PHASES = {"awareness", "interest", "consideration", "intent", "retention"}
_QUALIFICATION_FIELDS = {"name", "email", "phone", "goal", "company", "budget", "timeline"}

_EMOTION_ALIASES = {
    "anger": "angry",
    "angry": "angry",
    "frustration": "frustrated",
    "frustrated": "frustrated",
    "sadness": "frustrated",
    "confusion": "confused",
    "confused": "confused",
    "satisfaction": "satisfied",
    "satisfied": "satisfied",
    "joy": "happy",
    "happy": "happy",
    "excited": "excited",
    "neutral": "neutral",
}


class UnifiedIntentPayload(BaseModel):
    intent: str = "general_question"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    entities: dict[str, Any] = Field(default_factory=dict)
    urgency: Literal["low", "medium", "high", "critical"] = "medium"


class UnifiedSentimentPayload(BaseModel):
    label: str = "neutral"
    score: float = Field(default=0.5, ge=0.0, le=1.0)
    emotion: str = "neutral"


class UnifiedConversationSentimentPayload(BaseModel):
    label: str = "neutral"
    score: float = Field(default=0.5, ge=0.0, le=1.0)
    trend: str = "stable"


class UnifiedSentimentGatePayload(BaseModel):
    escalate: bool = False
    reason: str = ""


class UnifiedAIResponsePayload(BaseModel):
    response: str = ""
    deliver_response: bool = True
    escalate: bool = False
    next_action: str = "continue_conversation"


class UnifiedQualificationHintPayload(BaseModel):
    missing_fields: list[str] = Field(default_factory=list)
    completed_fields: list[str] = Field(default_factory=list)
    ready_for_scoring: bool = False
    next_question: str = ""


class UnifiedInteractionSummaryPayload(BaseModel):
    summary: str = ""
    total_messages: int = 0
    avg_sentiment: float = 0.0
    resolution_status: str = "in_progress"


class UnifiedMessageAIResult(BaseModel):
    intent: UnifiedIntentPayload = Field(default_factory=UnifiedIntentPayload)
    sentiment: UnifiedSentimentPayload = Field(default_factory=UnifiedSentimentPayload)
    conversation_sentiment: UnifiedConversationSentimentPayload = Field(
        default_factory=UnifiedConversationSentimentPayload
    )
    sentiment_gate: UnifiedSentimentGatePayload = Field(default_factory=UnifiedSentimentGatePayload)
    ai_response: UnifiedAIResponsePayload = Field(default_factory=UnifiedAIResponsePayload)
    qualification_hint: UnifiedQualificationHintPayload = Field(default_factory=UnifiedQualificationHintPayload)
    interaction_summary: UnifiedInteractionSummaryPayload = Field(default_factory=UnifiedInteractionSummaryPayload)


class UnifiedLeadAIResult(BaseModel):
    intent: UnifiedIntentPayload = Field(default_factory=lambda: UnifiedIntentPayload(intent="lead_capture"))
    score: int = Field(default=0, ge=0, le=100)
    grade: str = "cold"
    phase: str = "awareness"
    reasoning: str = ""
    next_action: str = ""
    nurture_message: str = ""
    missing_fields: list[str] = Field(default_factory=list)
    ready_for_scoring: bool = False


MESSAGE_SYSTEM_PROMPT = """\
You are the AI engine for a CRM support platform.
For every customer message you MUST return a single, valid JSON object and nothing else.

Output schema, all keys required:
{
  "intent": {
    "intent": "<snake_case from allowed list>",
    "confidence": <float 0.0-1.0>,
    "entities": {"<key>": "<value>"},
    "urgency": "<low|medium|high|critical>"
  },
  "sentiment": {
    "label": "<positive|neutral|negative>",
    "score": <float 0.0-1.0>,
    "emotion": "<joy|frustration|anger|sadness|confusion|satisfaction|neutral>"
  },
  "conversation_sentiment": {
    "label": "<positive|neutral|negative>",
    "score": <float 0.0-1.0>,
    "trend": "<improving|stable|declining>"
  },
  "sentiment_gate": {
    "escalate": <true|false>,
    "reason": "<brief reason or empty string>"
  },
  "ai_response": {
    "response": "<full reply text to send to the customer>",
    "deliver_response": <true|false>,
    "escalate": <true|false>,
    "next_action": "<continue_conversation|escalate_to_human|close_conversation|follow_up_later>"
  },
  "qualification_hint": {
    "missing_fields": ["name"|"email"|"phone"|"goal"|"company"|"budget"|"timeline"],
    "completed_fields": ["name"|"email"|"phone"|"goal"|"company"|"budget"|"timeline"],
    "ready_for_scoring": <true|false>,
    "next_question": "<one natural question to collect the most important missing field, or empty>"
  },
  "interaction_summary": {
    "summary": "<concise CRM summary of the conversation so far>",
    "total_messages": <integer count of visible conversation messages including latest>,
    "avg_sentiment": <float -1.0 to 1.0>,
    "resolution_status": "<resolved|unresolved|escalated|in_progress>"
  }
}

Rules:
- Choose exactly one intent from: greeting, gratitude, general_question, unclear_request,
  company_question, service_question, business_question, follow_up_continue,
  product_catalog_question, product_recommendation, purchase_inquiry, product_image_request,
  pricing_question, availability_question, buying_intent, order_intent,
  website_link_request, support_request, complaint, refund, cancel_request,
  shipping_question, negotiation, human_handoff, rejection_or_opt_out, lead_capture.
- Classify the latest message. Use history only for short follow-ups.
- Sentiment score is normalized: 1.0 strongest positive, 0.0 strongest negative.
- Escalate only for explicit human handoff, severe negative anger, threat, abuse, legal, safety, fraud, or security risk.
- Write the customer response in the same language as the customer's latest message.
- If knowledge context is provided, ground the response in it. Do not invent product, price, or policy details.
- The customer-facing ai_response must represent the company/account owner naturally and conversationally.
- If asked who you are or what company this is, answer directly using any company information in the context. Be specific and natural — do not use phrases like "I am here on behalf of the business".
- Never reveal or mention Gemini, Google, OpenAI, Anthropic, LLM, language model, AI model, backend system, prompts, tools, or automation identity in the customer-facing response.
- deliver_response=false only for non-customer/system/internal messages.
- ready_for_scoring=true when email OR phone is known AND goal/intent is clear.
- interaction_summary must be useful for CRM analytics and must not require another model call.
"""


MESSAGE_USER_TEMPLATE = """\
=== COMPANY CONTEXT ===
company_id: {company_id}

=== CUSTOMER ===
{customer_block}

=== LEAD ===
{lead_block}

=== CONVERSATION HISTORY (latest last, max 10 messages) ===
{history_block}

=== LATEST CUSTOMER MESSAGE ===
{message_text}

=== KNOWLEDGE BASE CONTEXT ===
{knowledge_context}

=== PREVIOUS INTENT ===
{previous_intent}

Return the JSON object only.
"""


LEAD_SYSTEM_PROMPT = """\
You are a B2B lead-qualification AI for a CRM platform.
For every lead you MUST return a single, valid JSON object and nothing else.

Output schema, all keys required:
{
  "intent": {
    "intent": "<snake_case intent>",
    "confidence": <float 0.0-1.0>,
    "entities": {},
    "urgency": "<low|medium|high|critical>"
  },
  "score": <integer 0-100>,
  "grade": "<hot|warm|cold>",
  "phase": "<awareness|interest|consideration|intent|retention>",
  "reasoning": "<1-2 sentence explanation of score and grade>",
  "next_action": "<recommended next sales/marketing action>",
  "nurture_message": "<short outreach message to send, or empty string>",
  "missing_fields": ["name"|"email"|"phone"|"goal"|"company"|"budget"|"timeline"],
  "ready_for_scoring": <true|false>
}

Scoring rules:
- Start at 40 base points.
- Add 20 if both email and phone are present.
- Add 10 if only one of email or phone is present.
- Add 10 if company name is known.
- Add 10 if a clear goal or need is stated.
- Add 10 if budget or timeline is mentioned.
- Subtract 10 if the lead message is vague or very short.
- score >= 80 is hot, score >= 60 is warm, otherwise cold.
- Empty nurture_message if the lead has no contact info.
"""


LEAD_USER_TEMPLATE = """\
=== COMPANY CONTEXT ===
company_id: {company_id}

=== LEAD DATA ===
{lead_block}

=== CUSTOMER DATA ===
{customer_block}

=== RAW LEAD MESSAGE / NOTES ===
{raw_message}

Return the JSON object only.

lead:
{lead_json}"""


def _fmt_customer(customer: dict) -> str:
    if not customer:
        return "(no customer data)"
    lines = []
    for key in ("id", "name", "email", "phone", "lifecycle_stage", "customer_company_name"):
        val = customer.get(key)
        if val:
            lines.append(f"  {key}: {val}")
    return "\n".join(lines) or "(no customer data)"


def _fmt_lead(lead: dict) -> str:
    if not lead:
        return "(no lead data)"
    lines = []
    for key in ("id", "name", "email", "phone", "source", "status", "phase", "grade", "score", "notes"):
        val = lead.get(key)
        if val is not None and str(val).strip():
            lines.append(f"  {key}: {val}")
    return "\n".join(lines) or "(no lead data)"


def _fmt_history(conversation_history: list[dict]) -> str:
    lines: list[str] = []
    for item in (conversation_history or [])[-80:]:
        if not isinstance(item, dict):
            continue
        sender = str(item.get("sender_type") or "unknown").strip().lower()
        content = str(item.get("content") or "").strip()
        if content:
            lines.append(f"  [{sender}]: {content}")
    return "\n".join(lines) or "  (no history)"


def _safe_float(val: Any, default: float = 0.0, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        return max(lo, min(hi, float(val)))
    except (TypeError, ValueError):
        return default


def _safe_str(val: Any, allowed: set[str] | None = None, default: str = "") -> str:
    s = str(val or "").strip().lower()
    if allowed and s not in allowed:
        return default
    return s


def _clean_field_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        field = str(value or "").strip().lower()
        if field in _QUALIFICATION_FIELDS and field not in result:
            result.append(field)
    return result


def _legacy_label(label: str) -> str:
    return "Negative" if label == "negative" else "Positive" if label == "positive" else "Neutral"


def _normalized_to_legacy_score(score_0_to_1: Any, label: str) -> float:
    normalized = _safe_float(score_0_to_1, 0.5)
    score = (normalized * 2.0) - 1.0
    if label == "negative" and score > -0.05:
        score = -0.25
    elif label == "positive" and score < 0.05:
        score = 0.25
    elif label == "neutral" and abs(score) > 0.25:
        score = 0.08 if score > 0 else -0.08
    return round(max(-1.0, min(1.0, score)), 3)


def _percentage(score: float) -> int:
    return int(((score + 1.0) / 2.0) * 100)


def _normalize_emotion(raw_emotion: str, label: str, score: float) -> str:
    emotion = _EMOTION_ALIASES.get(str(raw_emotion or "").strip().lower(), "")
    if emotion:
        return emotion
    if label == "negative":
        return "angry" if score <= -0.55 else "frustrated"
    if label == "positive":
        return "happy" if score >= 0.55 else "satisfied"
    return "neutral"


def _normalize_intent(intent_raw: dict, *, default: str = "general_question") -> dict:
    intent_name = str(intent_raw.get("intent") or default).strip().lower()
    intent_name = _INTENT_ALIASES.get(intent_name, intent_name)
    if intent_name not in _ALLOWED_INTENTS:
        intent_name = default
    return {
        "intent": intent_name,
        "confidence": _safe_float(intent_raw.get("confidence"), 0.5),
        "entities": intent_raw.get("entities") if isinstance(intent_raw.get("entities"), dict) else {},
        "urgency": _safe_str(intent_raw.get("urgency"), _ALLOWED_URGENCY, "medium"),
        "source": "unified_llm",
    }


def _normalize_sentiment(sent_raw: dict, *, scope: str, trend: str = "") -> dict:
    label = _safe_str(sent_raw.get("label"), _ALLOWED_SENTIMENT_LABELS, "neutral")
    score = _normalized_to_legacy_score(sent_raw.get("score"), label)
    emotion = _normalize_emotion(str(sent_raw.get("emotion") or ""), label, score)
    result = {
        "label": _legacy_label(label),
        "score": score,
        "emotion": emotion,
        "confidence": 0.82,
        "sentiment_label": label,
        "keywords": [],
        "emotion_breakdown": {
            "joy": round(max(score, 0.0), 3),
            "anger": round(max(-score, 0.0), 3),
            "sadness": round(max(-score * 0.65, 0.0), 3),
            "fear": round(max(-score * 0.35, 0.0), 3),
            "surprise": round(min(abs(score) * 0.3, 0.5), 3),
        },
        "normalized_score": round((score + 1.0) / 2.0, 4),
        "percentage": _percentage(score),
        "scope": scope,
        "source": "unified_llm",
    }
    if trend:
        result["trend"] = _safe_str(trend, _ALLOWED_SENTIMENT_TRENDS, "stable")
    return result


def _normalize_message_response(raw: dict, *, message_text: str = "", engine: dict | None = None) -> dict:
    intent = _normalize_intent(dict(raw.get("intent") or {}))
    sentiment = _normalize_sentiment(dict(raw.get("sentiment") or {}), scope="message")
    conv_sent_raw = dict(raw.get("conversation_sentiment") or {})
    conversation_sentiment = _normalize_sentiment(
        conv_sent_raw,
        scope="conversation",
        trend=str(conv_sent_raw.get("trend") or "stable"),
    )

    gate_raw = dict(raw.get("sentiment_gate") or {})
    sentiment_gate = {
        "escalate": bool(gate_raw.get("escalate", False)),
        "reason": str(gate_raw.get("reason") or ""),
    }

    resp_raw = dict(raw.get("ai_response") or {})
    provider = str((engine or {}).get("provider") or "")
    model_name = str((engine or {}).get("model_name") or "")
    ai_response = {
        "response": str(resp_raw.get("response") or ""),
        "confidence": 0.9 if str(resp_raw.get("response") or "").strip() else 0.0,
        "deliver_response": bool(resp_raw.get("deliver_response", True)),
        "escalate": bool(resp_raw.get("escalate", False)),
        "next_action": str(resp_raw.get("next_action") or "continue_conversation"),
        "attachments": [],
        "product_images": [],
        "product_ids": [],
        "provider": provider,
        "model_name": model_name,
        "llm_id": str((engine or {}).get("id") or ""),
        "rag_called": False,
        "api_error": False,
        "provider_error": {},
        "degraded": False,
        "fallback_used": False,
        "source": "unified_llm",
    }

    hint_raw = dict(raw.get("qualification_hint") or {})
    qualification_hint = {
        "missing_fields": _clean_field_list(hint_raw.get("missing_fields")),
        "completed_fields": _clean_field_list(hint_raw.get("completed_fields")),
        "ready_for_scoring": bool(hint_raw.get("ready_for_scoring", False)),
        "next_question": str(hint_raw.get("next_question") or ""),
    }

    summary_raw = dict(raw.get("interaction_summary") or {})
    interaction_summary = {
        "summary": str(summary_raw.get("summary") or ""),
        "total_messages": max(0, int(float(summary_raw.get("total_messages") or 0))),
        "avg_sentiment": round(_safe_float(summary_raw.get("avg_sentiment"), 0.0, -1.0, 1.0), 4),
        "resolution_status": str(summary_raw.get("resolution_status") or "in_progress").strip().lower()
        or "in_progress",
        "source": "unified_llm",
    }

    return {
        "intent": intent,
        "sentiment": sentiment,
        "conversation_sentiment": conversation_sentiment,
        "sentiment_gate": sentiment_gate,
        "ai_response": ai_response,
        "qualification_hint": qualification_hint,
        "interaction_summary": interaction_summary,
    }


def _normalize_lead_response(raw: dict, *, engine: dict | None = None) -> dict:
    score = max(0, min(100, int(float(raw.get("score") or 0))))
    grade = _safe_str(raw.get("grade"), _ALLOWED_LEAD_GRADES, "")
    if not grade or grade == "deferred":
        grade = "hot" if score >= 80 else "warm" if score >= 60 else "cold"
    provider = str((engine or {}).get("provider") or "")
    model_name = str((engine or {}).get("model_name") or "")
    return {
        "intent": _normalize_intent(dict(raw.get("intent") or {}), default="lead_capture"),
        "score": score,
        "grade": grade,
        "phase": _safe_str(raw.get("phase"), _ALLOWED_LEAD_PHASES, "awareness"),
        "reasoning": str(raw.get("reasoning") or ""),
        "next_action": str(raw.get("next_action") or ""),
        "nurture_message": str(raw.get("nurture_message") or ""),
        "missing_fields": _clean_field_list(raw.get("missing_fields")),
        "ready_for_scoring": bool(raw.get("ready_for_scoring", False)),
        "scoring_status": "completed",
        "provider": provider,
        "model_name": model_name,
        "fallback_used": False,
    }


def _error_type_and_reason(exc: Exception | None) -> tuple[str, str]:
    if exc is None:
        return "unknown_error", "AI provider unavailable"
    text = str(exc or "").replace("\n", " ").strip()
    upper = text.upper()
    if "RESOURCE_EXHAUSTED" in upper or "QUOTA" in upper:
        return "quota_exhausted", "AI provider quota exhausted"
    if "429" in upper or "RATE LIMIT" in upper:
        return "rate_limited", "AI provider rate limit reached"
    if "API_KEY" in upper or "NOT CONFIGURED" in upper:
        return "provider_not_configured", text[:240] or "AI provider is not configured"
    if "PERMISSION_DENIED" in upper or "403" in upper:
        return "permission_denied", "AI provider permission denied"
    if "INVALID MODEL" in upper or "MODEL NOT FOUND" in upper:
        return "invalid_model", "Selected AI model is invalid"
    if any(marker in upper for marker in ("500", "502", "503", "504", "TIMEOUT", "TIMED OUT")):
        return "provider_unavailable", "AI provider is unavailable"
    return exc.__class__.__name__, text[:240] or exc.__class__.__name__


def _message_fallback(message_text: str, *, engine: dict | None = None, exc: Exception | None = None) -> dict:
    lower = (message_text or "").lower()
    intent_name = "general_question"
    if any(t in lower for t in ("hi", "hello", "hey")):
        intent_name = "greeting"
    elif any(t in lower for t in ("price", "cost", "how much")):
        intent_name = "pricing_question"
    elif any(t in lower for t in ("buy", "order", "purchase")):
        intent_name = "buying_intent"
    elif any(t in lower for t in ("refund", "cancel", "complaint", "issue")):
        intent_name = "support_request"

    error_type, error_reason = _error_type_and_reason(exc)
    provider = str((engine or {}).get("provider") or "")
    model_name = str((engine or {}).get("model_name") or "")
    return {
        "intent": {
            "intent": intent_name,
            "confidence": 0.15,
            "entities": {},
            "urgency": "low",
            "source": "local_fallback",
        },
        "sentiment": {
            "label": "Neutral",
            "score": 0.02,
            "emotion": "neutral",
            "confidence": 0.2,
            "sentiment_label": "neutral",
            "keywords": [],
            "emotion_breakdown": {"joy": 0.02, "anger": 0.0, "sadness": 0.0, "fear": 0.0, "surprise": 0.01},
            "normalized_score": 0.51,
            "percentage": 51,
            "scope": "message",
            "source": "local_fallback",
        },
        "conversation_sentiment": {
            "label": "Neutral",
            "score": 0.02,
            "emotion": "neutral",
            "confidence": 0.2,
            "sentiment_label": "neutral",
            "keywords": [],
            "emotion_breakdown": {"joy": 0.02, "anger": 0.0, "sadness": 0.0, "fear": 0.0, "surprise": 0.01},
            "normalized_score": 0.51,
            "percentage": 51,
            "scope": "conversation",
            "trend": "stable",
            "source": "local_fallback",
        },
        "sentiment_gate": {"escalate": False, "reason": ""},
        "ai_response": {
            "response": "",
            "confidence": 0.0,
            "deliver_response": bool(message_text),
            "escalate": False,
            "next_action": "continue_conversation",
            "attachments": [],
            "product_images": [],
            "product_ids": [],
            "provider": provider,
            "model_name": model_name,
            "llm_id": str((engine or {}).get("id") or ""),
            "rag_called": False,
            "api_error": exc is not None,
            "provider_error": {
                "status": "unavailable",
                "error_type": error_type if exc is not None else "",
                "error_reason": error_reason if exc is not None else "",
                "provider": provider,
                "model_name": model_name,
            }
            if exc is not None
            else {},
            "degraded": exc is not None,
            "fallback_used": True,
            "error_type": error_type if exc is not None else "",
            "error_reason": error_reason if exc is not None else "",
            "source": "local_fallback",
        },
        "qualification_hint": {
            "missing_fields": [],
            "completed_fields": [],
            "ready_for_scoring": False,
            "next_question": "",
        },
        "interaction_summary": {
            "summary": "",
            "total_messages": 0,
            "avg_sentiment": 0.0,
            "resolution_status": "in_progress",
            "source": "local_fallback",
        },
    }


def _lead_fallback(lead: dict, *, engine: dict | None = None, exc: Exception | None = None) -> dict:
    error_type, error_reason = _error_type_and_reason(exc)
    return {
        "intent": {
            "intent": "lead_capture",
            "confidence": 0.0,
            "entities": {},
            "urgency": "low",
            "source": "local_fallback",
        },
        "score": 0,
        "grade": "deferred",
        "phase": str((lead or {}).get("phase") or "awareness"),
        "reasoning": f"AI lead scoring unavailable: {error_reason}. Lead was not scored.",
        "next_action": "Review lead manually after AI provider is available.",
        "nurture_message": "",
        "missing_fields": [f for f in ("name", "email", "phone") if not (lead or {}).get(f)],
        "ready_for_scoring": False,
        "scoring_status": "failed",
        "provider": str((engine or {}).get("provider") or ""),
        "model_name": str((engine or {}).get("model_name") or ""),
        "error_type": error_type,
        "error_reason": error_reason,
        "fallback_used": True,
    }


async def call_unified_message_ai(
    *,
    message_text: str,
    conversation_history: list[dict],
    customer: dict,
    lead: dict,
    company_id: str,
    knowledge_context: str = "",
    previous_intent: str = "",
    engine: Any = None,
    call_model_json_fn: Any = None,
) -> dict:
    if call_model_json_fn is None:
        from services.ai_service.llm_client import call_model_json as call_model_json_fn

    user_msg = MESSAGE_USER_TEMPLATE.format(
        company_id=company_id or "",
        customer_block=_fmt_customer(customer or {}),
        lead_block=_fmt_lead(lead or {}),
        history_block=_fmt_history(conversation_history or []),
        message_text=message_text or "",
        knowledge_context=knowledge_context.strip() if knowledge_context else "(none)",
        previous_intent=previous_intent or "unknown",
    )

    prompt = f"{MESSAGE_SYSTEM_PROMPT}\n\n{user_msg}"
    last_exc: Exception | None = None
    for attempt in range(1):
        try:
            logger.info("unified_message_ai_called company_id=%s attempt=%s", company_id or "", attempt + 1)
            raw = await call_model_json_fn(
                prompt,
                UnifiedMessageAIResult,
                engine=engine,
                call_purpose="unified_message_ai",
                function_name="call_unified_message_ai",
                agent_name="capture",
                max_provider_attempts=1,
                allow_provider_fallback=False,
            )
            return _normalize_message_response(raw, message_text=message_text, engine=engine)
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "unified_message_ai_attempt_failed company_id=%s attempt=%s error=%s",
                company_id or "",
                attempt + 1,
                str(exc)[:200],
            )

    logger.error("unified_message_ai_degraded company_id=%s error=%s", company_id or "", str(last_exc)[:200])
    return _message_fallback(message_text, engine=engine, exc=last_exc)


async def call_unified_lead_ai(
    *,
    lead: dict,
    customer: dict,
    company_id: str,
    engine: Any = None,
    call_model_json_fn: Any = None,
    count_against_budget: bool = True,
) -> dict:
    if call_model_json_fn is None:
        from services.ai_service.llm_client import call_model_json as call_model_json_fn

    raw_message = str((lead or {}).get("notes") or (lead or {}).get("message_text") or "").strip()
    lead_json = json.dumps(lead or {}, ensure_ascii=True, default=str, sort_keys=True)
    user_msg = LEAD_USER_TEMPLATE.format(
        company_id=company_id or "",
        lead_block=_fmt_lead(lead or {}),
        customer_block=_fmt_customer(customer or {}),
        raw_message=raw_message or "(no message)",
        lead_json=lead_json,
    )
    prompt = f"{LEAD_SYSTEM_PROMPT}\n\n{user_msg}"

    last_exc: Exception | None = None
    for attempt in range(1):
        try:
            logger.info("unified_lead_ai_called company_id=%s attempt=%s", company_id or "", attempt + 1)
            raw = await call_model_json_fn(
                prompt,
                UnifiedLeadAIResult,
                engine=engine,
                use_pro=True,
                call_purpose="unified_lead_ai",
                function_name="call_unified_lead_ai",
                agent_name="qualification",
                max_provider_attempts=1,
                allow_provider_fallback=False,
                count_against_budget=count_against_budget,
            )
            return _normalize_lead_response(raw, engine=engine)
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "unified_lead_ai_attempt_failed company_id=%s attempt=%s error=%s",
                company_id or "",
                attempt + 1,
                str(exc)[:200],
            )

    logger.error("unified_lead_ai_degraded company_id=%s error=%s", company_id or "", str(last_exc)[:200])
    return _lead_fallback(lead or {}, engine=engine, exc=last_exc)


__all__ = [
    "UnifiedLeadAIResult",
    "UnifiedMessageAIResult",
    "call_unified_lead_ai",
    "call_unified_message_ai",
]
