"""Adaptive qualification helper.

Progressive, per-interaction collection of qualification fields
(budget, timeline, need, role) using lightweight regex/keyword extraction
plus the entities returned by the intent classifier.

State is persisted on ``leads.metadata -> qualification`` (JSONB) so no
additional table is required. When all required fields are collected the
helper returns ``ready_for_scoring=True`` and the QualificationAgent is
allowed to invoke ``generate_lead_score``. Until then the next best
question is returned so the SupportAgent can ask it.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


REQUIRED_FIELDS: tuple[str, ...] = ("need", "budget", "timeline", "role")


FIELD_QUESTIONS: dict[str, str] = {
    "need": "To help you best, what is the main problem or need you're trying to solve?",
    "budget": "Do you have a rough budget in mind for this? (even a range is fine)",
    "timeline": "When are you hoping to have this in place? (this week, this month, this quarter, etc.)",
    "role": "What's your role, and are you the one making the final decision on this?",
}


# Simple keyword patterns — deliberately conservative. The intent classifier's
# entities (when available) are preferred.
_BUDGET_PATTERNS = [
    re.compile(r"\$\s?\d[\d,\.]*", re.I),
    re.compile(r"\b\d+\s?(?:k|K|m|M)\b"),
    re.compile(r"\b(?:budget|spend|pay|cost|price)\b", re.I),
    re.compile(r"\busd\b|\bpkr\b|\beur\b|\bgbp\b", re.I),
]

_TIMELINE_PATTERNS = [
    re.compile(
        r"\b(?:today|tomorrow|this\s+week|next\s+week|this\s+month|next\s+month|"
        r"this\s+quarter|next\s+quarter|asap|urgent|immediately|in\s+\d+\s+(?:days?|weeks?|months?))\b",
        re.I,
    ),
    re.compile(r"\bQ[1-4]\b"),
    re.compile(r"\b20\d{2}\b"),
]

_ROLE_PATTERNS = [
    re.compile(
        r"\b(?:ceo|cto|cfo|founder|co-?founder|owner|director|manager|head|lead|"
        r"engineer|developer|marketer|analyst|student|researcher|admin|vp)\b",
        re.I,
    ),
    re.compile(r"\bi\s+(?:am|'m)\s+(?:the\s+)?\w+", re.I),
    re.compile(r"\bdecision[\s-]?maker\b", re.I),
]

_NEED_PATTERNS = [
    re.compile(
        r"\b(?:need|want|looking\s+for|interested\s+in|help\s+with|problem|issue|use\s+case|"
        r"integration|support|crm|automation|chat|lead|campaign|analytics|reporting|onboarding)\b",
        re.I,
    ),
]


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _extract_from_message(message: str) -> dict[str, str]:
    """Best-effort field extraction from a free-text message."""
    if not message:
        return {}
    found: dict[str, str] = {}

    def _first_match(patterns: list[re.Pattern[str]]) -> str:
        for pat in patterns:
            m = pat.search(message)
            if m:
                return m.group(0)
        return ""

    budget = _first_match(_BUDGET_PATTERNS)
    if budget:
        found["budget"] = budget.strip()

    timeline = _first_match(_TIMELINE_PATTERNS)
    if timeline:
        found["timeline"] = timeline.strip()

    role = _first_match(_ROLE_PATTERNS)
    if role:
        found["role"] = role.strip()

    need = _first_match(_NEED_PATTERNS)
    if need and len(message.strip()) > 12:
        # Use the whole message as the need summary if a need-keyword fired,
        # capped to a sensible length.
        found["need"] = message.strip()[:240]
    return found


def _merge_from_intent_entities(
    current: dict[str, Any],
    intent: dict[str, Any] | None,
) -> dict[str, Any]:
    if not intent:
        return current
    entities = intent.get("entities") if isinstance(intent.get("entities"), dict) else {}
    if not entities:
        return current

    # Accept direct keys if the classifier surfaced them.
    for key in REQUIRED_FIELDS:
        val = entities.get(key)
        if isinstance(val, str) and val.strip() and not current.get(key):
            current[key] = val.strip()
    # Common aliases.
    alias_map = {
        "price": "budget",
        "money": "budget",
        "when": "timeline",
        "deadline": "timeline",
        "urgency": "timeline",
        "use_case": "need",
        "goal": "need",
        "position": "role",
        "title": "role",
    }
    for alias, target in alias_map.items():
        val = entities.get(alias)
        if isinstance(val, str) and val.strip() and not current.get(target):
            current[target] = val.strip()
    return current


def _next_question(answers: dict[str, Any], asked: list[str]) -> tuple[str, str]:
    """Return (field, question) for the next best missing field, or ('', '')."""
    for field in REQUIRED_FIELDS:
        if not str(answers.get(field) or "").strip():
            # Allow re-asking once at most; after two asks without an answer, move on.
            ask_count = sum(1 for a in asked if a == field)
            if ask_count < 2:
                return field, FIELD_QUESTIONS[field]
    return "", ""


async def _load_lead_qualification(db: Any, lead_id: str) -> dict[str, Any]:
    if not lead_id:
        return {}
    try:
        row = await db.fetchval(
            "SELECT metadata FROM leads WHERE id=$1",
            lead_id,
        )
    except Exception as exc:
        logger.debug("adaptive_qualification: could not read leads.metadata: %s", exc)
        return {}
    metadata = _to_dict(row)
    return _to_dict(metadata.get("qualification"))


async def _save_lead_qualification(
    db: Any,
    lead_id: str,
    company_id: str,
    qualification: dict[str, Any],
) -> None:
    if not lead_id or not company_id:
        return
    try:
        # Upsert the qualification sub-document while preserving siblings.
        await db.execute(
            """
            UPDATE leads
               SET metadata = jsonb_set(
                       COALESCE(metadata, '{}'::jsonb),
                       '{qualification}',
                       $3::jsonb,
                       true
                   ),
                   updated_at = NOW()
             WHERE id = $1 AND company_id = $2
            """,
            lead_id,
            company_id,
            json.dumps(qualification, default=str),
        )
    except Exception as exc:
        logger.debug(
            "adaptive_qualification: could not persist qualification for lead %s: %s",
            lead_id,
            exc,
        )


async def advance_adaptive_qualification(
    *,
    db: Any,
    company_id: str,
    lead: dict[str, Any],
    message_text: str,
    intent: dict[str, Any] | None,
) -> dict[str, Any]:
    """Process the latest message, update tracked answers, return next question.

    Returns a payload suitable for inclusion on the capture agent result:
    {
      "answers": {...},
      "asked_questions": [...],
      "missing_fields": [...],
      "completed_fields": [...],
      "ready_for_scoring": bool,
      "next_question": str,
      "next_field": str
    }
    """
    lead = lead or {}
    lead_id = str(lead.get("id") or "").strip()

    current = await _load_lead_qualification(db, lead_id) if lead_id else {}
    answers = _to_dict(current.get("answers"))
    asked = list(current.get("asked_questions") or [])

    # Extract from latest message + intent entities.
    extracted = _extract_from_message(message_text or "")
    for key, val in extracted.items():
        if not answers.get(key):
            answers[key] = val
    answers = _merge_from_intent_entities(answers, intent)

    # Determine next question (or declare ready).
    missing = [f for f in REQUIRED_FIELDS if not str(answers.get(f) or "").strip()]
    completed = [f for f in REQUIRED_FIELDS if str(answers.get(f) or "").strip()]

    next_field, next_question = ("", "")
    ready_for_scoring = not missing
    if not ready_for_scoring:
        next_field, next_question = _next_question(answers, asked)
        if next_field and next_field not in asked:
            asked.append(next_field)

    qualification = {
        "answers": answers,
        "asked_questions": asked,
        "missing_fields": missing,
        "completed_fields": completed,
        "ready_for_scoring": ready_for_scoring,
        "next_question": next_question,
        "next_field": next_field,
    }

    # Persist if we have a lead row.
    if lead_id:
        await _save_lead_qualification(db, lead_id, company_id, qualification)

    return qualification
