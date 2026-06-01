"""Rule-based opt-out detector for proactive follow-up conversations.

Pure-Python, deterministic, easy to unit-test and extend. The intent is to
catch the obvious "stop messaging me" replies during a proactive turn. False
negatives degrade to whatever the next pass through the pipeline decides; we
prefer false negatives over false positives because mislabelling a legitimate
question as a disengagement would silence a happy customer.

A TODO marker below points at where to plug an LLM intent classifier if the
false-negative rate becomes a problem in production.
"""

from __future__ import annotations

import re

# Phrases are matched against a normalised (lowercased, whitespace-collapsed)
# version of the user reply. The list is bounded — adding a phrase is one line.
DISENGAGEMENT_KEYWORDS: tuple[str, ...] = (
    "stop",
    "stop messaging",
    "stop messaging me",
    "leave me alone",
    "no more messages",
    "no more emails",
    "unsubscribe",
    "remove me",
    "not interested anymore",
    "not interested thanks",
    "not interested, stop",
    "no longer interested",
    "don't contact",
    "do not contact",
    "please don't contact",
    "don't reach out",
    "do not reach out",
    "stop contacting",
    "opt out",
    "opt-out",
    "opting out",
    "no thanks bye",
    "please stop",
    "stop sending",
    "don't message me",
    "do not message me",
)

# Compiled patterns ensure phrases like "stop" only match as full words, not as
# substrings of "stopping". The non-letter boundaries on both sides are
# explicit so we don't fire on "stopwatch".
_PATTERNS = tuple(
    re.compile(rf"(?:^|[^a-z]){re.escape(kw)}(?:$|[^a-z])", re.IGNORECASE)
    for kw in DISENGAGEMENT_KEYWORDS
)


def detect_disengagement(text: str) -> bool:
    """Return True iff the customer reply reads as a clear opt-out request."""
    if not text:
        return False
    normalised = re.sub(r"\s+", " ", str(text).lower().strip())
    if not normalised:
        return False
    return any(pat.search(normalised) for pat in _PATTERNS)
    # TODO: fall back to an LLM intent classifier if false-negative rate
    # becomes a problem (e.g. "please don't reach out again, I'm overwhelmed").
