from __future__ import annotations

import re
from typing import Any


PRODUCT_INTENTS = {
    "product_catalog_question",
    "product_recommendation",
    "purchase_inquiry",
    "product_image_request",
    "pricing_question",
    "availability_question",
    "buying_intent",
    "order_intent",
    "website_link_request",
}

KNOWLEDGE_INTENTS = {
    "company_question",
    "service_question",
    "business_question",
    "support_request",
    "complaint",
    "refund",
    "cancel_request",
    "shipping_question",
}

LOW_VALUE_INTENTS = {
    "greeting",
    "gratitude",
    "acknowledgement",
    "rejection_or_opt_out",
}

_LOW_VALUE_EXACT = {
    "hi",
    "hii",
    "hello",
    "hey",
    "salam",
    "assalam",
    "assalamu alaikum",
    "thanks",
    "thank you",
    "thx",
    "ok",
    "okay",
    "k",
    "yes",
    "yeah",
    "yep",
    "no",
    "nope",
    "sure",
    "alright",
    "got it",
    "understood",
    "fine",
}

_GREETING_EXACT = {"hi", "hii", "hello", "hey", "salam", "assalam", "assalamu alaikum"}
_GRATITUDE_EXACT = {"thanks", "thank you", "thx"}
_ACK_EXACT = _LOW_VALUE_EXACT - _GREETING_EXACT - _GRATITUDE_EXACT

_PRODUCT_TERMS = {
    "product",
    "products",
    "catalog",
    "catalogue",
    "item",
    "items",
    "collection",
    "inventory",
    "price",
    "pricing",
    "cost",
    "rate",
    "buy",
    "purchase",
    "order",
    "checkout",
    "available",
    "availability",
    "stock",
    "image",
    "images",
    "photo",
    "photos",
    "picture",
    "pictures",
    "recommend",
    "suggest",
}

_PRODUCT_PHRASES = (
    "what do you sell",
    "what products",
    "show products",
    "show me products",
    "show me catalog",
    "show your catalog",
    "send catalog",
    "product pictures",
    "product images",
    "show me pictures",
    "show me photos",
    "how much",
    "what is the price",
    "what's the price",
    "where can i buy",
    "where can i order",
    "how can i order",
    "place order",
    "order link",
    "checkout link",
)

_SERVICE_TERMS = {"service", "services", "offer", "provide", "providing", "business", "company", "support", "help"}
_SERVICE_PHRASES = (
    "what services",
    "which services",
    "show services",
    "show me services",
    "list services",
    "available services",
    "services do you offer",
    "services do you provide",
    "what do you offer",
    "what do you provide",
    "what are your services",
)
_AMBIGUOUS_PRODUCT_TERMS = {"order", "item", "items", "available", "availability"}
_STRONG_PRODUCT_TERMS = _PRODUCT_TERMS - _AMBIGUOUS_PRODUCT_TERMS
_SARCASM_PHRASES = (
    "thanks for nothing",
    "great service",
    "nice service",
    "great job",
    "nice job",
    "yeah right",
    "sure whatever",
    "as expected",
    "typical",
    "very helpful",
    "so helpful",
)
_PASSIVE_AGGRESSIVE_TERMS = {
    "whatever",
    "fine",
    "great",
    "nice",
    "brilliant",
    "wow",
    "typical",
    "useless",
    "nothing",
}
_NEGATIVE_CONTEXT_TERMS = {
    "sorry",
    "apologize",
    "unavailable",
    "failed",
    "cannot",
    "can't",
    "not able",
    "problem",
    "issue",
    "delay",
    "refund",
    "complaint",
}


def normalize_message_text(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9\s]", " ", str(text or "").strip().lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def assess_lightweight_conversational_risk(
    text: str,
    *,
    previous_ai_message: str = "",
) -> dict[str, Any]:
    raw = str(text or "").strip().lower()
    normalized = normalize_message_text(raw)
    tokens = set(normalized.split())
    previous = str(previous_ai_message or "").strip().lower()
    reasons: list[str] = []
    sarcasm_score = 0.0
    ambiguity_score = 0.0
    negativity_score = 0.0

    if not normalized:
        return {
            "should_bypass": True,
            "sarcasm_score": 0.0,
            "ambiguity_score": 0.0,
            "negativity_score": 0.0,
            "reasons": [],
        }

    if any(phrase in raw for phrase in _SARCASM_PHRASES):
        sarcasm_score += 0.75
        reasons.append("sarcasm_phrase")
    if any(marker in raw for marker in ("🙄", "😒", "👎", "/s")):
        sarcasm_score += 0.35
        reasons.append("negative_or_sarcastic_emoji")
    if "!" in raw and tokens & _PASSIVE_AGGRESSIVE_TERMS:
        sarcasm_score += 0.18
        reasons.append("emphatic_acknowledgement")
    if tokens & {"nothing", "whatever", "useless", "bad", "poor", "terrible", "wrong"}:
        negativity_score += 0.55
        reasons.append("negative_acknowledgement")
    if previous and any(term in previous for term in _NEGATIVE_CONTEXT_TERMS):
        ambiguity_score += 0.35
        reasons.append("negative_previous_context")
    if tokens & _PASSIVE_AGGRESSIVE_TERMS and tokens & _LOW_VALUE_EXACT:
        ambiguity_score += 0.35
        reasons.append("ambiguous_acknowledgement_tone")
    if len(tokens) <= 4 and tokens & {"fine", "sure", "ok", "okay"} and previous and "?" not in previous:
        ambiguity_score += 0.15
        reasons.append("short_ack_without_clear_question")

    should_bypass = (sarcasm_score < 0.45 and negativity_score < 0.35 and ambiguity_score < 0.5)
    return {
        "should_bypass": should_bypass,
        "sarcasm_score": round(min(sarcasm_score, 1.0), 3),
        "ambiguity_score": round(min(ambiguity_score, 1.0), 3),
        "negativity_score": round(min(negativity_score, 1.0), 3),
        "reasons": reasons,
    }


def is_low_value_message(text: str) -> bool:
    normalized = normalize_message_text(text)
    if not normalized:
        return True
    risk = assess_lightweight_conversational_risk(normalized)
    if not risk.get("should_bypass", True):
        return False
    if normalized in _LOW_VALUE_EXACT:
        return True
    tokens = normalized.split()
    if len(tokens) <= 3 and any(token in _LOW_VALUE_EXACT for token in tokens):
        product_overlap = any(token in _PRODUCT_TERMS for token in tokens)
        service_overlap = any(token in _SERVICE_TERMS for token in tokens)
        return not (product_overlap or service_overlap)
    return False


def should_lightweight_bypass(text: str, *, previous_ai_message: str = "") -> bool:
    if not is_low_value_message(text):
        return False
    return bool(assess_lightweight_conversational_risk(text, previous_ai_message=previous_ai_message).get("should_bypass", True))


def lightweight_route_message(
    text: str,
    *,
    previous_intent: str = "",
    previous_ai_message: str = "",
) -> dict[str, Any]:
    normalized = normalize_message_text(text)
    risk = assess_lightweight_conversational_risk(text, previous_ai_message=previous_ai_message)
    if not normalized:
        return {
            "intent": "general_question",
            "confidence": 0.0,
            "entities": {},
            "urgency": "low",
            "source": "lightweight_empty",
            "low_value": True,
            "lightweight_risk": risk,
        }

    if not risk.get("should_bypass", True) and (
        normalized in _LOW_VALUE_EXACT or any(token in _LOW_VALUE_EXACT for token in normalized.split())
    ):
        return {}

    if normalized in _GREETING_EXACT:
        return {
            "intent": "greeting",
            "confidence": 0.98,
            "entities": {},
            "urgency": "low",
            "source": "lightweight_rule",
            "low_value": True,
            "lightweight_risk": risk,
        }
    if normalized in _GRATITUDE_EXACT:
        return {
            "intent": "gratitude",
            "confidence": 0.98,
            "entities": {},
            "urgency": "low",
            "source": "lightweight_rule",
            "low_value": True,
            "lightweight_risk": risk,
        }
    if normalized in _ACK_EXACT:
        return {
            "intent": "acknowledgement",
            "confidence": 0.96,
            "entities": {"previous_intent": previous_intent or ""},
            "urgency": "low",
            "source": "lightweight_rule",
            "low_value": True,
            "lightweight_risk": risk,
        }

    if any(phrase in normalized for phrase in _SERVICE_PHRASES):
        return {
            "intent": "service_question",
            "confidence": 0.84,
            "entities": {},
            "urgency": "low",
            "source": "lightweight_rule",
            "low_value": False,
        }

    if any(phrase in normalized for phrase in _PRODUCT_PHRASES):
        intent_name = "product_catalog_question"
        if any(term in normalized for term in ("image", "images", "photo", "photos", "picture", "pictures")):
            intent_name = "product_image_request"
        elif any(term in normalized for term in ("price", "pricing", "cost", "rate", "how much")):
            intent_name = "pricing_question"
        elif any(term in normalized for term in ("buy", "order", "checkout", "purchase")):
            intent_name = "buying_intent"
        return {
            "intent": intent_name,
            "confidence": 0.86,
            "entities": {},
            "urgency": "medium" if intent_name in {"buying_intent", "pricing_question"} else "low",
            "source": "lightweight_rule",
            "low_value": False,
        }

    tokens = set(normalized.split())
    if "order" in tokens and (
        any(term in normalized for term in ("delayed", "tracking", "shipped", "shipping", "delivery"))
        or tokens & {"where", "status", "late", "arrived"}
    ):
        return {
            "intent": "shipping_question",
            "confidence": 0.82,
            "entities": {},
            "urgency": "medium",
            "source": "lightweight_rule",
            "low_value": False,
        }
    support_issue_terms = {"issue", "problem", "refund", "cancel", "complaint", "delayed", "late", "wrong", "broken"}
    if "order" in tokens and tokens & support_issue_terms:
        return {
            "intent": "support_request",
            "confidence": 0.82,
            "entities": {},
            "urgency": "medium",
            "source": "lightweight_rule",
            "low_value": False,
        }
    if tokens & _PRODUCT_TERMS:
        intent_name = "product_catalog_question"
        if tokens & {"image", "images", "photo", "photos", "picture", "pictures"}:
            intent_name = "product_image_request"
        elif tokens & {"price", "pricing", "cost", "rate"}:
            intent_name = "pricing_question"
        elif tokens & {"buy", "purchase", "order", "checkout"}:
            intent_name = "buying_intent"
        elif tokens & {"available", "availability", "stock"}:
            intent_name = "availability_question"
        elif tokens & {"recommend", "suggest"}:
            intent_name = "product_recommendation"
        confidence = 0.82 if explicit_product_signal(normalized) else 0.66
        return {
            "intent": intent_name,
            "confidence": confidence,
            "entities": {},
            "urgency": "medium" if intent_name in {"buying_intent", "pricing_question"} else "low",
            "source": "lightweight_rule",
            "low_value": False,
        }

    if len(normalized.split()) <= 4 and previous_ai_message and previous_intent in PRODUCT_INTENTS:
        if any(term in normalized for term in ("price", "cost", "image", "photo", "picture", "available", "stock")):
            return {
                "intent": "pricing_question" if any(term in normalized for term in ("price", "cost")) else previous_intent,
                "confidence": 0.78,
                "entities": {"previous_intent": previous_intent},
                "urgency": "medium",
                "source": "lightweight_context_rule",
                "low_value": False,
            }

    return {}


def explicit_product_signal(text: str) -> bool:
    normalized = normalize_message_text(text)
    if not normalized:
        return False
    if any(phrase in normalized for phrase in _PRODUCT_PHRASES):
        return True
    tokens = set(normalized.split())
    if tokens & _STRONG_PRODUCT_TERMS:
        return True
    if "order" in tokens:
        return bool(tokens & {"buy", "purchase", "checkout", "link", "place"})
    return False


def is_high_confidence_product_intent(
    intent: dict[str, Any] | None,
    text: str = "",
    *,
    has_product_history: bool = False,
) -> bool:
    payload = dict(intent or {})
    intent_name = str(payload.get("intent") or "").strip().lower()
    if intent_name not in PRODUCT_INTENTS:
        return False
    try:
        confidence = float(payload.get("confidence") or 0.0)
    except Exception:
        confidence = 0.0
    normalized = normalize_message_text(text)
    explicit_signal = explicit_product_signal(normalized)

    if intent_name == "website_link_request":
        website_purchase_signal = any(
            phrase in normalized
            for phrase in (
                "where can i buy",
                "where can i order",
                "how can i order",
                "order link",
                "checkout",
                "buy link",
                "purchase link",
            )
        )
        return confidence >= 0.78 and (website_purchase_signal or has_product_history)

    if intent_name in {"buying_intent", "order_intent"}:
        if confidence >= 0.9:
            return True
        return confidence >= 0.78 and (explicit_signal or has_product_history)

    if confidence >= 0.8:
        return True
    if confidence >= 0.68 and explicit_signal:
        return True
    if confidence >= 0.72 and has_product_history and not is_low_value_message(text):
        return True
    return False


def should_fetch_knowledge_context(intent: dict[str, Any] | None, text: str = "") -> bool:
    intent_name = str((intent or {}).get("intent") or "").strip().lower()
    if intent_name in KNOWLEDGE_INTENTS:
        return True
    normalized = normalize_message_text(text)
    if not normalized or is_low_value_message(normalized):
        return False
    return any(term in normalized for term in _SERVICE_TERMS)
