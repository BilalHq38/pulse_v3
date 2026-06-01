"""Rule-based sentiment classifier for proactive-followup replies.

Used by `on_customer_reply` to decide whether to schedule an upsell turn.
The cost-to-signal ratio of an LLM call is too low to justify here — the
keyword lists are easy to extend from observed production traffic.

Negative signals always win: never upsell to a dissatisfied customer.
"""

from __future__ import annotations

import re
from typing import Literal

Sentiment = Literal["positive", "neutral", "negative"]


POSITIVE_SIGNALS: tuple[str, ...] = (
    "great",
    "good",
    "excellent",
    "loved",
    "love it",
    "loving",
    "happy",
    "satisfied",
    "amazing",
    "awesome",
    "perfect",
    "thanks",
    "thank you",
    "wonderful",
    "fantastic",
    "works well",
    "no issues",
    "all good",
    "very pleased",
)

NEGATIVE_SIGNALS: tuple[str, ...] = (
    "bad",
    "broken",
    "doesn't work",
    "doesn t work",
    "does not work",
    "not working",
    "disappointed",
    "disappointing",
    "want a refund",
    "need a refund",
    "requesting refund",
    "complaint",
    "have an issue",
    "got an issue",
    "there's an issue",
    "there is an issue",
    "have a problem",
    "got a problem",
    "there's a problem",
    "defective",
    "item damaged",
    "arrived damaged",
    "something missing",
    "item missing",
    "parts missing",
    "wrong item",
    "wrong product",
    "arrived late",
    "still not arrived",
    "never arrived",
    "unhappy",
    "terrible",
    "awful",
)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().strip())


def _any_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    """Match keywords with simple boundary check to avoid false positives.
    'issue' must not match 'no issues'. Check preceded/followed by non-word char or boundary."""
    for kw in keywords:
        idx = text.find(kw)
        if idx == -1:
            continue
        before_ok = idx == 0 or not text[idx - 1].isalpha()
        after_ok = (idx + len(kw)) >= len(text) or not text[idx + len(kw)].isalpha()
        if before_ok and after_ok:
            return True
    return False


def classify_sentiment(text: str) -> Sentiment:
    """Return positive/neutral/negative.

    Uses all-MiniLM-L6-v2 for semantic accuracy when available.
    Falls back to keyword matching so we never upsell to an unhappy customer
    even if the model is unavailable.
    Negative always wins ties in the keyword fallback.
    """
    if not _normalise(text):
        return "neutral"

    # MiniLM primary
    try:
        from services.conversation_engine.local_ml import classify_sentiment as _ml  # noqa: PLC0415
        result = _ml(text or "")
        label = result.get("label", "neutral")
        if result.get("model_available", True) and label in {"positive", "neutral", "negative"}:
            return label  # type: ignore[return-value]
    except Exception:
        pass

    # Keyword fallback
    normalised = _normalise(text)
    if _any_keyword(normalised, NEGATIVE_SIGNALS):
        return "negative"
    if _any_keyword(normalised, POSITIVE_SIGNALS):
        return "positive"
    return "neutral"
