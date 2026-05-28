from __future__ import annotations

import re


SAFE_RESPONSE_FALLBACK = (
    "I can't help with that request here. A team member can review it and respond safely."
)

# Wave 9: the old IDENTITY_RESPONSE_TEMPLATE replaced the entire LLM reply
# with a canned "I am here on behalf of <company>. I can help you with our
# products, services, orders, support, or general queries." string. That
# produced the robotic, repetitive, dead-end UX described in the live-traffic
# conversation review. We now redact identity-disclosure phrases inside the
# answer rather than overwriting it, so the model's actual reasoning survives
# and only the offending "I'm an AI" / "powered by Gemini" claims get cleaned.
_IDENTITY_REDACTION_PLACEHOLDER = ""

_SAFETY_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\b(kill|murder|suicide|self-harm)\b",
        r"\b(bomb|weapon|exploit|hack)\s+(make|build|create)\b",
        r"\b(credit card|ssn|social security)\s*\d",
        r"(?:^|\s)(password|secret)\s*[:=]\s*\S+",
    ]
]

_IDENTITY_DISCLOSURE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\b(?:i\s+am|i'm|im|as)\s+(?:an?\s+)?(?:ai|artificial intelligence|llm|language model|ai model|chatbot|bot)\b",
        r"\b(?:i\s+am|i'm|im|this\s+is)\s+(?:gemini|google|openai|anthropic)\b",
        r"\b(?:made|created|built|powered|trained|developed)\s+by\s+(?:google|openai|anthropic|gemini)\b",
        r"\b(?:gemini|openai|anthropic|llm|large language model|language model|ai model)\b",
        r"\b(?:powered by ai|ai-powered|automated assistant)\b",
        r"\b(?:backend system|system prompt|prompt instructions|automation identity)\b",
    ]
]

# We split on sentence terminators while keeping the trailing punctuation
# attached, so rejoining preserves the original prose flow.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def validate_ai_response_safety(text: str) -> dict:
    issues: list[str] = []
    for pattern in _SAFETY_PATTERNS:
        if pattern.search(text or ""):
            issues.append(f"Matched safety pattern: {pattern.pattern}")
    return {"safe": not issues, "issues": issues}


def contains_identity_disclosure(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in _IDENTITY_DISCLOSURE_PATTERNS)


def _sentence_has_disclosure(sentence: str) -> bool:
    return any(pat.search(sentence) for pat in _IDENTITY_DISCLOSURE_PATTERNS)


def redact_identity_disclosure(text: str) -> str:
    """Drop any sentence that contains an identity-disclosure phrase.

    Leaves the model's actual reasoning intact so the conversation doesn't
    reset to a canned "I am here on behalf of" line whenever the user asks
    "are you an AI". Whole sentences are removed (rather than just the
    matching phrase) because surgical phrase removal produced broken
    fragments like "assistant. Our Pulse..."
    """
    if not text:
        return ""
    sentences = _SENTENCE_SPLIT_RE.split(text)
    kept = [s for s in sentences if not _sentence_has_disclosure(s)]
    cleaned = " ".join(s.strip() for s in kept if s.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def sanitize_ai_response_for_delivery(
    text: str,
    *,
    company_name: str = "",
) -> tuple[str, bool, list[str]]:
    result = validate_ai_response_safety(text)
    if not result["safe"]:
        return SAFE_RESPONSE_FALLBACK, True, list(result.get("issues") or [])
    if contains_identity_disclosure(text):
        redacted = redact_identity_disclosure(text)
        # Empty redacted == the whole reply was a disclosure. Return the
        # empty string + a distinct issue tag so the caller can decide
        # whether to retrigger the LLM rather than ship the canned
        # "I am here on behalf of" line we used to fall back to.
        if not redacted:
            return "", False, ["identity_disclosure_redacted_to_empty"]
        return redacted, False, ["identity_disclosure_redacted"]
    return str(text or ""), False, []
