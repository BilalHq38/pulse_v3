"""Centralized lead stage transition rules.

The CRM already stores lead stage in ``leads.status`` and audit-like lead
events in ``lead_activities``. This module keeps stage automation in one place
without adding another table for the current workflow.
"""

from __future__ import annotations

import json
import re
from typing import Any

from core.utils import make_id
from models.reference_data import resolve_company_reference_id


ALLOWED_LEAD_STAGES = (
    "new",
    "contacted",
    "qualified",
    "proposal",
    "negotiation",
    "converted",
    "won",
    "lost",
)

STAGE_RANK = {stage: index for index, stage in enumerate(ALLOWED_LEAD_STAGES)}
MANUAL_STAGE_SOURCES = {"manual_update", "user"}
TERMINAL_STAGES = {"converted", "won", "lost"}
LOCKED_TERMINAL_STAGES = {"won", "lost"}

QUALIFIED_KEYWORDS = (
    "price",
    "pricing",
    "details",
    "available",
    "how much",
    "interested",
    "interest",
    "want",
    "need",
    "product",
    "service",
    "quote",
    "quotation",
    "package",
    "plan",
    "cost",
)
PROPOSAL_KEYWORDS = (
    "proposal",
    "quotation",
    "quote",
    "offer",
    "package",
    "pricing plan",
    "formal recommendation",
    "recommendation",
    "estimate",
)
NEGOTIATION_KEYWORDS = (
    "discount",
    "final price",
    "negotiate",
    "negotiation",
    "reduce",
    "cheaper",
    "lower price",
    "budget",
    "deal terms",
    "timeline",
    "custom requirement",
    "custom requirements",
    "too expensive",
)
LOST_KEYWORDS = (
    "not interested",
    "no thanks",
    "no thank you",
    "stop",
    "unsubscribe",
    "do not contact",
    "don't contact",
    "no need",
)
WON_KEYWORDS = (
    "paid",
    "payment done",
    "payment completed",
    "confirmed",
    "purchase confirmed",
    "deal done",
    "closed won",
)


def normalize_lead_stage(value: Any) -> str:
    stage = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if stage not in ALLOWED_LEAD_STAGES:
        raise ValueError(f"Unsupported lead stage: {value}")
    return stage


def r(row) -> dict | None:
    return dict(row) if row else None


def rs(rows) -> list[dict]:
    return [dict(row) for row in (rows or [])]


def _safe_current_stage(value: Any) -> str:
    try:
        return normalize_lead_stage(value)
    except ValueError:
        return "new"


def _text_has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def infer_stage_from_message_text(
    message_text: str,
    *,
    direction: str,
) -> tuple[str, str, float] | None:
    """Return a deterministic stage signal for a message, or ``None``.

    This is the rule-based fallback used whether or not AI output is available.
    Strong terminal signals are checked before softer engagement signals.
    """

    text = re.sub(r"\s+", " ", str(message_text or "").strip().lower())
    if not text:
        return None

    normalized_direction = str(direction or "").strip().lower()
    is_outbound = normalized_direction in {"outbound", "agent", "message_sent", "proposal_sent"}

    if _text_has_any(text, LOST_KEYWORDS):
        return "lost", "Explicit rejection or opt-out language", 0.95
    if _text_has_any(text, WON_KEYWORDS):
        return "won", "Successful close or payment confirmation", 0.95
    if _text_has_any(text, NEGOTIATION_KEYWORDS):
        return "negotiation", "Pricing, timeline, objection, or deal-term discussion", 0.82
    if _text_has_any(text, PROPOSAL_KEYWORDS):
        return "proposal", "Proposal, quotation, offer, or recommendation mentioned", 0.80
    if _text_has_any(text, QUALIFIED_KEYWORDS):
        return "qualified", "Customer interest or product/pricing inquiry", 0.72
    if is_outbound:
        return "contacted", "Auto-updated after first outbound message", 0.80
    return None


def _stage_activity_payload(
    *,
    previous_stage: str,
    new_stage: str,
    reason: str,
    source: str,
    confidence: float,
    changed_by_user_id: str,
    event_id: str,
    automatic: bool,
) -> str:
    return json.dumps(
        {
            "previous_stage": previous_stage,
            "new_stage": new_stage,
            "reason": reason,
            "source": source,
            "confidence": confidence,
            "changed_by_user_id": changed_by_user_id,
            "event_id": event_id,
            "automatic": automatic,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def parse_stage_activity(row: dict) -> dict:
    parsed: dict[str, Any] = {}
    content = str(row.get("content") or "")
    if content:
        try:
            loaded = json.loads(content)
            if isinstance(loaded, dict):
                parsed = loaded
        except json.JSONDecodeError:
            parsed = {"reason": content}
    return {
        "id": row.get("id", ""),
        "company_id": row.get("company_id", ""),
        "lead_id": row.get("lead_id", ""),
        "previous_stage": parsed.get("previous_stage", ""),
        "new_stage": parsed.get("new_stage") or row.get("stage", ""),
        "reason": parsed.get("reason", ""),
        "source": parsed.get("source", ""),
        "confidence": parsed.get("confidence"),
        "changed_by_user_id": parsed.get("changed_by_user_id", ""),
        "event_id": parsed.get("event_id", ""),
        "automatic": parsed.get("automatic"),
        "created_at": row.get("created_at"),
    }


async def get_lead_stage_history(db, lead_id: str, company_id: str) -> list[dict]:
    rows = rs(
        await db.fetch(
            "SELECT * FROM lead_activities WHERE lead_id=$1 AND company_id=$2 AND type='stage_changed' "
            "ORDER BY created_at DESC",
            lead_id,
            company_id,
        )
    )
    return [parse_stage_activity(row) for row in rows]


async def latest_stage_history_entry(db, lead_id: str, company_id: str) -> dict | None:
    row = r(
        await db.fetchrow(
            "SELECT * FROM lead_activities WHERE lead_id=$1 AND company_id=$2 AND type='stage_changed' "
            "ORDER BY created_at DESC LIMIT 1",
            lead_id,
            company_id,
        )
    )
    return parse_stage_activity(row) if row else None


async def transition_lead_stage(
    db,
    lead: dict,
    new_stage: str,
    *,
    reason: str,
    source: str,
    confidence: float = 1.0,
    changed_by_user_id: str = "",
    event_id: str = "",
    automatic: bool | None = None,
) -> dict:
    """Validate, apply, and audit a lead stage transition.

    Automatic transitions do not move backward, do not overwrite locked terminal
    stages, and avoid weak changes immediately after a manual override.
    """

    if not lead:
        return {"changed": False, "reason": "lead_missing", "lead": None}

    company_id = str(lead.get("company_id") or "").strip()
    lead_id = str(lead.get("id") or "").strip()
    target_stage = normalize_lead_stage(new_stage)
    previous_stage = _safe_current_stage(lead.get("status") or "new")
    source_value = str(source or "system").strip().lower() or "system"
    is_automatic = source_value not in MANUAL_STAGE_SOURCES if automatic is None else bool(automatic)
    try:
        confidence_value = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence_value = 0.0

    if not company_id or not lead_id:
        return {"changed": False, "reason": "lead_scope_missing", "lead": lead}
    if target_stage == previous_stage:
        return {"changed": False, "reason": "already_at_stage", "lead": lead}

    if is_automatic:
        if previous_stage in LOCKED_TERMINAL_STAGES and source_value not in {"conversion", "payment"}:
            return {"changed": False, "reason": "terminal_stage_locked", "lead": lead}
        if previous_stage == "won" and target_stage != "won":
            return {"changed": False, "reason": "won_stage_locked", "lead": lead}
        if previous_stage == "converted" and target_stage != "won":
            return {"changed": False, "reason": "converted_stage_locked", "lead": lead}
        if STAGE_RANK[target_stage] < STAGE_RANK[previous_stage] and target_stage not in TERMINAL_STAGES:
            return {"changed": False, "reason": "automatic_backward_transition_blocked", "lead": lead}
        latest = await latest_stage_history_entry(db, lead_id, company_id)
        if (
            latest
            and latest.get("source") in MANUAL_STAGE_SOURCES
            and target_stage not in TERMINAL_STAGES
            and confidence_value < 0.85
        ):
            return {"changed": False, "reason": "manual_override_respected", "lead": lead}

    status_id = ""
    try:
        status_id = await resolve_company_reference_id(
            db,
            company_id,
            "lead_statuses",
            "status_name",
            target_stage,
            {"description": "Lead status", "order_index": STAGE_RANK[target_stage] + 1},
        )
    except Exception:
        status_id = ""

    await db.execute(
        "UPDATE leads SET status=$1,status_id=$2,updated_at=NOW() WHERE id=$3 AND company_id=$4",
        target_stage,
        status_id,
        lead_id,
        company_id,
    )
    await db.execute(
        "INSERT INTO lead_activities(id,lead_id,company_id,type,content,stage,created_at) "
        "VALUES($1,$2,$3,'stage_changed',$4,$5,NOW())",
        make_id(),
        lead_id,
        company_id,
        _stage_activity_payload(
            previous_stage=previous_stage,
            new_stage=target_stage,
            reason=str(reason or "").strip(),
            source=source_value,
            confidence=confidence_value,
            changed_by_user_id=str(changed_by_user_id or "").strip(),
            event_id=str(event_id or "").strip(),
            automatic=is_automatic,
        ),
        target_stage,
    )
    updated = r(
        await db.fetchrow(
            "SELECT * FROM leads WHERE id=$1 AND company_id=$2 LIMIT 1",
            lead_id,
            company_id,
        )
    )
    return {
        "changed": True,
        "reason": "stage_changed",
        "previous_stage": previous_stage,
        "new_stage": target_stage,
        "lead": updated or {**lead, "status": target_stage, "status_id": status_id},
    }


async def find_lead_for_customer(db, company_id: str, customer_id: str) -> dict | None:
    customer = r(
        await db.fetchrow(
            "SELECT * FROM customers WHERE id=$1 AND company_id=$2 LIMIT 1",
            customer_id,
            company_id,
        )
    )
    if not customer:
        return None

    lead_id = str(customer.get("lead_id") or "").strip()
    if lead_id:
        lead = r(
            await db.fetchrow(
                "SELECT * FROM leads WHERE id=$1 AND company_id=$2 LIMIT 1",
                lead_id,
                company_id,
            )
        )
        if lead:
            return lead

    email = str(customer.get("email") or "").strip().lower()
    phone = str(customer.get("phone") or "").strip()
    where = []
    args: list[Any] = [company_id]
    if email:
        where.append(f"LOWER(email)=${len(args) + 1}")
        args.append(email)
    if phone:
        where.append(f"phone=${len(args) + 1}")
        args.append(phone)
    if not where:
        return None
    return r(
        await db.fetchrow(
            f"SELECT * FROM leads WHERE company_id=$1 AND ({' OR '.join(where)}) ORDER BY updated_at DESC LIMIT 1",
            *args,
        )
    )


async def apply_message_stage_transition(
    db,
    *,
    company_id: str,
    message_text: str,
    direction: str,
    source: str,
    lead_id: str = "",
    customer_id: str = "",
    event_id: str = "",
    changed_by_user_id: str = "",
) -> dict:
    lead = None
    if lead_id:
        lead = r(
            await db.fetchrow(
                "SELECT * FROM leads WHERE id=$1 AND company_id=$2 LIMIT 1",
                lead_id,
                company_id,
            )
        )
    if not lead and customer_id:
        lead = await find_lead_for_customer(db, company_id, customer_id)
    if not lead:
        return {"changed": False, "reason": "lead_not_found", "lead": None}

    signal = infer_stage_from_message_text(message_text, direction=direction)
    if not signal:
        return {"changed": False, "reason": "no_stage_signal", "lead": lead}
    target_stage, reason, confidence = signal
    return await transition_lead_stage(
        db,
        lead,
        target_stage,
        reason=reason,
        source=source,
        confidence=confidence,
        changed_by_user_id=changed_by_user_id,
        event_id=event_id,
        automatic=True,
    )
