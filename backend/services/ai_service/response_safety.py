from __future__ import annotations

import re


SAFE_RESPONSE_FALLBACK = (
    "I can't help with that request here. A team member can review it and respond safely."
)

IDENTITY_RESPONSE_TEMPLATE = (
    "I am here on behalf of {company_name}. I can help you with our products, "
    "services, orders, support, or general queries."
)

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
        r"\b(?:gemini|google|openai|anthropic|llm|large language model|language model|ai model)\b",
        r"\b(?:powered by ai|ai-powered|automated assistant)\b",
        r"\b(?:backend system|system prompt|prompt instructions|automation identity)\b",
    ]
]


def validate_ai_response_safety(text: str) -> dict:
    issues: list[str] = []
    for pattern in _SAFETY_PATTERNS:
        if pattern.search(text or ""):
            issues.append(f"Matched safety pattern: {pattern.pattern}")
    return {"safe": not issues, "issues": issues}


def company_representative_response(company_name: str = "") -> str:
    clean_name = " ".join(str(company_name or "").split()).strip() or "this business"
    return IDENTITY_RESPONSE_TEMPLATE.format(company_name=clean_name)


def contains_identity_disclosure(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in _IDENTITY_DISCLOSURE_PATTERNS)


def sanitize_ai_response_for_delivery(
    text: str,
    *,
    company_name: str = "",
) -> tuple[str, bool, list[str]]:
    result = validate_ai_response_safety(text)
    if not result["safe"]:
        return SAFE_RESPONSE_FALLBACK, True, list(result.get("issues") or [])
    if contains_identity_disclosure(text):
        return company_representative_response(company_name), False, ["identity_disclosure_rewritten"]
    return str(text or ""), False, []
