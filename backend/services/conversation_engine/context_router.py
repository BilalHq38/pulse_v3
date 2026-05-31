"""Rule-based scorer that decides which sources a turn should query.

The orchestrator always queries `company_data` (it's always relevant and
returns one tiny row). For the other three sources we score the query against
small keyword sets — quick, deterministic, and unit-testable. When the rule
scorer is ambiguous (top two sources within `AMBIGUITY_BAND`) callers can
fall back to an LLM classifier; that hook lives in the orchestrator so this
module stays pure-Python.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from services.ai_service.routing_guards import is_low_value_message, lightweight_route_message
from services.conversation_engine.schemas import SourceType


# Keyword seeds — extended from the existing routing_guards module. Kept small
# on purpose: the rule scorer is meant to be cheap. Adding a keyword is one
# line of code, so we err on the side of curation, not catch-all coverage.
_PRODUCT_KEYWORDS = {
    "price", "cost", "buy", "purchase", "order", "ordering", "checkout",
    "cart", "add to cart", "stock", "available", "availability",
    "in stock", "deliver", "ship", "shipping", "product", "item", "model",
    "variant", "size", "color", "discount", "offer", "sale", "compare",
    # Purchase intent and recommendation
    "recommend", "recommendation", "suggest", "suggestion", "looking for",
    "need", "want", "affordable", "budget", "cheap", "best", "top",
    "which", "option", "choice", "feature", "spec", "specification",
    "how much", "show me", "can i get", "do you have", "do you sell",
    "any", "list", "catalog", "catalogue", "what products", "what do you sell",
    "image", "images", "photo", "photos", "picture", "pictures", "look like",
    # Referential phrases — user referring to previously shown/discussed products
    "both", "these", "those", "that one", "this one", "the one", "them",
    "all of them", "both of them", "you mentioned", "talked about", "showed",
}
_FAQ_KEYWORDS = {
    "policy", "return", "refund", "warranty", "exchange", "hours", "open",
    "contact", "support", "help", "phone", "email", "address", "location",
    "payment", "method", "shipping policy", "delivery time",
}
_KB_KEYWORDS = {
    "how to", "how do", "guide", "tutorial", "instructions", "setup",
    "install", "manual", "documentation", "troubleshoot",
}
_COMPANY_KEYWORDS = {
    "you", "your company", "who are you", "about you", "your business",
    "your team", "where are you", "your hours",
}
_SMALL_TALK_EXACT = {
    "how are you",
    "how r u",
    "how are u",
    "how you doing",
    "how are you doing",
    "how is it going",
    "how's it going",
}

# Sources scoring below this floor are skipped entirely (saves a round trip).
_INCLUSION_FLOOR = 0.15
# Two sources within this band of the top score → ambiguous → orchestrator may
# call the LLM fallback.
_AMBIGUITY_BAND = 0.05


@dataclass
class RoutingDecision:
    sources: list[SourceType]
    scores: dict[SourceType, float]
    ambiguous: bool
    low_value: bool = False
    direct_intent: str = ""
    direct_response: str = ""


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().strip())


def _score_keywords(text: str, keywords: set[str]) -> float:
    if not text or not keywords:
        return 0.0
    hits = sum(1 for kw in keywords if kw in text)
    if hits == 0:
        return 0.0
    return min(1.0, 0.25 + 0.18 * hits)


def is_conversational_message(query: str) -> bool:
    text = _normalise(query)
    plain = re.sub(r"[^a-z0-9\s']", " ", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    return bool(text and (is_low_value_message(text) or plain in _SMALL_TALK_EXACT))


def _direct_conversational_response(query: str) -> tuple[str, str]:
    text = _normalise(query)
    plain = re.sub(r"[^a-z0-9\s']", " ", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    if plain in _SMALL_TALK_EXACT:
        return "social", "I'm doing well, thanks for asking. What can I help you with today?"
    routed = lightweight_route_message(query)
    intent = str((routed or {}).get("intent") or "").strip().lower()
    if intent == "greeting":
        return "greeting", "Hi! Thanks for reaching out. What can I help you with today?"
    if intent == "social":
        return "social", "I'm doing well, thanks for asking. What can I help you with today?"
    if intent == "gratitude":
        return "low_value", "Glad to help. Let me know if there's anything else I can do for you."
    if intent == "acknowledgement":
        normalized = re.sub(r"[^a-z0-9\s]", " ", text)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if normalized in {"no", "nope"}:
            return "low_value", "Understood. I will not continue unless you ask for something else."
        return "low_value", "Understood. Tell me what you would like to do next."
    return "low_value", "Thanks. What can I help you with next?"


def score(query: str) -> RoutingDecision:
    text = _normalise(query)
    if is_conversational_message(text):
        direct_intent, direct_response = _direct_conversational_response(text)
        return RoutingDecision(
            sources=[],
            scores={
                "company_data": 0.0,
                "product": 0.0,
                "faq": 0.0,
                "knowledge_base": 0.0,
            },
            ambiguous=False,
            low_value=True,
            direct_intent=direct_intent,
            direct_response=direct_response,
        )

    scores: dict[SourceType, float] = {
        "company_data": _score_keywords(text, _COMPANY_KEYWORDS),
        "product": _score_keywords(text, _PRODUCT_KEYWORDS),
        "faq": _score_keywords(text, _FAQ_KEYWORDS),
        "knowledge_base": _score_keywords(text, _KB_KEYWORDS),
    }
    # company_data is always queried — it's tiny and almost always useful as
    # ambient context. Pin its floor to ensure it's selected.
    scores["company_data"] = max(scores["company_data"], _INCLUSION_FLOOR + 0.01)

    selected = [src for src, s in scores.items() if s >= _INCLUSION_FLOOR]
    if not selected:
        # Empty/garbled queries: fall back to company_data + KB so we can still
        # answer "I don't know" gracefully with some context.
        selected = ["company_data", "knowledge_base"]

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    ambiguous = (
        len(ranked) >= 2
        and ranked[0][1] > 0
        and (ranked[0][1] - ranked[1][1]) < _AMBIGUITY_BAND
    )

    # Order the returned list highest score first so callers can use it as a
    # priority order for retrieval timeouts.
    selected_in_order = [src for src, _ in ranked if src in selected]
    return RoutingDecision(sources=selected_in_order, scores=scores, ambiguous=ambiguous)
