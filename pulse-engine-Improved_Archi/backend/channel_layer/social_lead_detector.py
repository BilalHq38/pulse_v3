"""
channel_layer.social_lead_detector — heuristic lead detection for social signals.

Inputs are arbitrary text (a comment body, a DM, a postback payload, etc.).
Output is a small dict describing whether this looks like a lead and which
tags should be attached to the captured customer/lead record.

Kept intentionally minimal and dependency-free so it can run inline inside
webhook handlers without touching the AI service. If a tenant has the AI
service wired up, the orchestrator can still re-score later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


INTEREST_KEYWORDS: tuple[str, ...] = (
    "price",
    "pricing",
    "cost",
    "quote",
    "interested",
    "interest",
    "buy",
    "purchase",
    "order",
    "book",
    "available",
    "stock",
    "details",
    "info",
    "information",
    "send me",
    "dm me",
    "dm",
    "contact",
    "reach out",
    "call me",
    "demo",
    "trial",
    "signup",
    "sign up",
    "discount",
    "offer",
    "deal",
    "how much",
    "where to buy",
    "shipping",
    "delivery",
    "when available",
)

HOT_LEAD_KEYWORDS: tuple[str, ...] = (
    "buy now",
    "ready to buy",
    "purchase today",
    "order now",
    "take my money",
    "send payment link",
    "how do i pay",
    "how do i order",
    "i want to buy",
    "book a call",
    "schedule a demo",
    "proceed",
)

NEGATIVE_KEYWORDS: tuple[str, ...] = (
    "scam",
    "spam",
    "fake",
    "fraud",
    "do not contact",
    "unsubscribe",
    "stop messaging",
)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# Permissive international-style phone. Picks up 10-digit or +country formats
# while avoiding most noise (currencies, order ids, etc.).
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().\-]{8,}\d)")
HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]{2,50})")


@dataclass
class SocialLeadSignal:
    is_lead: bool = False
    is_hot: bool = False
    is_negative: bool = False
    matched_keywords: list[str] = field(default_factory=list)
    matched_hashtags: list[str] = field(default_factory=list)
    contact_emails: list[str] = field(default_factory=list)
    contact_phones: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "is_lead": self.is_lead,
            "is_hot": self.is_hot,
            "is_negative": self.is_negative,
            "matched_keywords": list(self.matched_keywords),
            "matched_hashtags": list(self.matched_hashtags),
            "contact_emails": list(self.contact_emails),
            "contact_phones": list(self.contact_phones),
            "tags": list(self.tags),
        }


def _any_keyword(haystack: str, needles: tuple[str, ...]) -> list[str]:
    if not haystack:
        return []
    low = haystack.lower()
    return [kw for kw in needles if kw in low]


def detect(
    text: str,
    *,
    platform: str,
    interest_hashtags: tuple[str, ...] | set[str] | None = None,
) -> SocialLeadSignal:
    """Classify a free-form social comment/DM.

    ``platform`` must be one of ``facebook`` / ``instagram`` / ``web_chat`` /
    ``whatsapp`` / ``email``; it is used purely to set the platform tag.

    ``interest_hashtags`` is an optional per-tenant allowlist; if any of the
    message's hashtags match, the message is treated as a lead even without a
    keyword hit.
    """
    signal = SocialLeadSignal()
    body = (text or "").strip()
    if not body:
        return signal

    low = body.lower()

    matched = _any_keyword(low, INTEREST_KEYWORDS)
    hot_matched = _any_keyword(low, HOT_LEAD_KEYWORDS)
    neg_matched = _any_keyword(low, NEGATIVE_KEYWORDS)

    hashtags = [h.lower() for h in HASHTAG_RE.findall(body)]
    allowed_tags = {h.lower().lstrip("#") for h in (interest_hashtags or set())}
    hashtag_hits = [h for h in hashtags if h in allowed_tags]

    emails = list(dict.fromkeys(EMAIL_RE.findall(body)))
    phones_raw = [match for match in PHONE_RE.findall(body)]
    phones: list[str] = []
    for raw in phones_raw:
        digits = re.sub(r"\D", "", raw)
        if 10 <= len(digits) <= 15:
            phones.append(raw.strip())

    signal.matched_keywords = matched + hot_matched
    signal.matched_hashtags = hashtag_hits
    signal.contact_emails = emails
    signal.contact_phones = phones
    signal.is_negative = bool(neg_matched)
    signal.is_hot = bool(hot_matched)

    if signal.is_negative:
        # Negative signals override interest classification.
        signal.tags = ["social_negative", platform] if platform else ["social_negative"]
        return signal

    signal.is_lead = bool(matched or hot_matched or hashtag_hits or emails or phones)

    tags: list[str] = []
    if signal.is_lead:
        tags.append("social_lead")
        if platform:
            tags.append(platform)
        tags.append("interested")
    if signal.is_hot:
        tags.append("hot_lead")
    if emails or phones:
        tags.append("contact_shared")

    # De-duplicate preserving order.
    seen: set[str] = set()
    signal.tags = [t for t in tags if not (t in seen or seen.add(t))]
    return signal


def tags_for_channel_message(
    text: str,
    platform: str,
    *,
    interest_hashtags: tuple[str, ...] | set[str] | None = None,
) -> list[str]:
    """Convenience wrapper used by the webhook capture path."""
    sig = detect(text, platform=platform, interest_hashtags=interest_hashtags)
    return sig.tags
