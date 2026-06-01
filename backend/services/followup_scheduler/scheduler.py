"""Follow-up scheduler core.

Three responsibilities:
  1. evaluate_order_event — react to an order lifecycle event by inserting an
     ai_followups row (idempotent via uq_followups_idempotency_key).
  2. claim_due_followup — atomically transition the next due row from
     scheduled → running using FOR UPDATE SKIP LOCKED so two workers can't
     claim the same row.
  3. transition helpers — mark a follow-up completed / declined / failed.

The orchestrator and the loop module call into these. The loop module lives
separately so it can be opted-in via a startup hook without forcing every
service that touches the scheduler module to import asyncio loop scaffolding.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, Literal

logger = logging.getLogger(__name__)

WorkflowKind = Literal["post_delivery_feedback", "upsell", "order_confirmed"]
_ORDER_CONFIRMED_STATUSES = {"admin_review", "placed", "confirmed"}
_DELIVERY_COMPLETION_STATUSES = {"delivered", "completed", "delivery_completed", "order_completed"}

# Defaults; overridable via env vars without redeploy.
_FEEDBACK_DELAY_SECONDS = int(os.environ.get("AI_FOLLOWUP_FEEDBACK_DELAY_SECONDS", "3600") or 3600)
_UPSELL_DELAY_SECONDS = int(os.environ.get("AI_FOLLOWUP_UPSELL_DELAY_SECONDS", "86400") or 86400)
_ORDER_CONFIRMED_DELAY_SECONDS = int(os.environ.get("AI_FOLLOWUP_ORDER_CONFIRMED_DELAY_SECONDS", "300") or 300)
_COOLDOWN_DAYS = int(os.environ.get("AI_FOLLOWUP_COOLDOWN_DAYS", "7") or 7)
_MAX_PER_MONTH = int(os.environ.get("AI_FOLLOWUP_MAX_PER_MONTH", "4") or 4)


def _new_id(prefix: str, seed: str) -> str:
    suffix = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{int(time.time() * 1000):x}_{suffix}"


def _idempotency_key(order_id: str, workflow_kind: str, to_status: str) -> str:
    return hashlib.sha256(f"{order_id}:{workflow_kind}:{to_status}".encode("utf-8")).hexdigest()


def _normalize_trigger_status(to_status: str) -> str:
    normalized = str(to_status or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "delivery_complete": "delivery_completed",
        "delivery_completed": "delivery_completed",
        "order_complete": "order_completed",
        "order_completed": "order_completed",
        "delivered": "delivered",
        "completed": "completed",
        "admin_review": "admin_review",
        "placed": "placed",
        "confirmed": "confirmed",
    }
    return aliases.get(normalized, normalized)


def _log_post_conversation_trigger(
    *,
    trigger_status: str,
    order_id: str,
    customer_id: str,
    workflow_kind: str = "",
    followup_id: str = "",
    passed: bool,
    reason: str = "",
) -> None:
    logger.info(
        "post_conversation_trigger_evaluated trigger=%s order_id=%s customer_id=%s "
        "workflow_kind=%s followup_id=%s trigger_result=%s reason=%s",
        trigger_status,
        order_id,
        customer_id,
        workflow_kind,
        followup_id,
        "pass" if passed else "fail",
        reason,
    )


async def _engagement_ok(db, *, company_id: str, customer_id: str) -> tuple[bool, str]:
    """Returns (allowed, reason). reason is empty when allowed=True."""
    if not customer_id:
        # Anonymous customers — no engagement row, no opt-out tracking. Allow
        # scheduling; admin can deal with the lone follow-up if it's an issue.
        return True, ""
    try:
        row = await db.fetchrow(
            "SELECT opted_out, followup_count, last_contacted_at "
            "FROM customer_engagement WHERE company_id = $1 AND customer_id = $2",
            company_id,
            customer_id,
        )
    except Exception:
        return True, ""
    if not row:
        return True, ""
    record = dict(row)
    if record.get("opted_out"):
        return False, "opted_out"
    if int(record.get("followup_count") or 0) >= _MAX_PER_MONTH:
        # Naive monthly limit — the column tracks lifetime count rather than
        # per-month, which is good enough for a v1 safety net; we can tighten
        # this later by querying ai_followups within a rolling window.
        return False, "rate_limited"
    return True, ""


async def _record_engagement_contact(db, *, company_id: str, customer_id: str, context: str) -> None:
    if not customer_id:
        return
    try:
        await db.execute(
            "UPDATE customer_engagement "
            "SET followup_count = COALESCE(followup_count, 0) + 1, "
            "    last_contacted_at = NOW() "
            "WHERE company_id = $1 AND customer_id = $2",
            company_id,
            customer_id,
        )
        await db.execute(
            "INSERT INTO customer_engagement (company_id, customer_id, followup_count, last_contacted_at) "
            "SELECT $1, $2, 1, NOW() "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM customer_engagement WHERE company_id = $1 AND customer_id = $2"
            ")",
            company_id,
            customer_id,
        )
    except Exception as exc:
        logger.debug(
            "%s_followup_count_increment_failed customer_id=%s error=%s",
            context,
            customer_id,
            exc,
        )


async def evaluate_order_event(
    db,
    *,
    company_id: str,
    order_id: str,
    customer_id: str,
    session_id: str,
    to_status: str,
) -> dict[str, Any]:
    """Decide whether the lifecycle transition should schedule a follow-up.

    Returns a small dict describing the outcome:
      {"scheduled": True, "followup_id": "...", "workflow_kind": "..."}
      {"scheduled": False, "reason": "not_a_followup_status"}  / "opted_out" / etc.
    Idempotent — replays of the same (order_id, workflow_kind, to_status)
    are no-ops because of uq_followups_idempotency_key.
    """
    raw_status = str(to_status or "").strip().lower()
    normalized_status = _normalize_trigger_status(to_status)
    if normalized_status not in (_ORDER_CONFIRMED_STATUSES | _DELIVERY_COMPLETION_STATUSES):
        _log_post_conversation_trigger(
            trigger_status=raw_status,
            order_id=order_id,
            customer_id=customer_id,
            passed=False,
            reason="not_a_followup_status",
        )
        return {"scheduled": False, "reason": "not_a_followup_status"}

    allowed, reason = await _engagement_ok(db, company_id=company_id, customer_id=customer_id)
    if not allowed:
        _log_post_conversation_trigger(
            trigger_status=normalized_status,
            order_id=order_id,
            customer_id=customer_id,
            passed=False,
            reason=reason,
        )
        return {"scheduled": False, "reason": reason}

    if normalized_status in _ORDER_CONFIRMED_STATUSES:
        workflow_kind: WorkflowKind = "order_confirmed"
        delay = _ORDER_CONFIRMED_DELAY_SECONDS
        canonical_status = "confirmed"
    else:
        workflow_kind = "post_delivery_feedback"
        delay = _FEEDBACK_DELAY_SECONDS
        canonical_status = "delivered"
    idem = _idempotency_key(order_id, workflow_kind, canonical_status)
    followup_id = _new_id("fu", f"{order_id}:{workflow_kind}")

    try:
        row = await db.fetchrow(
            "INSERT INTO ai_followups "
            "(id, company_id, order_id, customer_id, session_id, workflow_kind, "
            " status, scheduled_for, idempotency_key) "
            "VALUES ($1, $2, $3, $4, $5, $6, 'scheduled', NOW() + ($7 || ' seconds')::interval, $8) "
            "ON CONFLICT (idempotency_key) WHERE BTRIM(idempotency_key) <> '' DO NOTHING "
            "RETURNING id",
            followup_id,
            company_id,
            order_id,
            customer_id or "",
            session_id or "",
            workflow_kind,
            str(delay),
            idem,
        )
    except Exception as exc:
        # The uq_followups_one_active_per_order partial index can also fire
        # when an earlier follow-up for this order is still active. Treat that
        # as "already scheduled" rather than an error.
        logger.info(
            "followup_insert_failed order_id=%s workflow_kind=%s error=%s",
            order_id, workflow_kind, exc,
        )
        _log_post_conversation_trigger(
            trigger_status=normalized_status,
            order_id=order_id,
            customer_id=customer_id,
            workflow_kind=workflow_kind,
            passed=False,
            reason="already_active_or_db_error",
        )
        return {"scheduled": False, "reason": "already_active_or_db_error"}

    if not row:
        _log_post_conversation_trigger(
            trigger_status=normalized_status,
            order_id=order_id,
            customer_id=customer_id,
            workflow_kind=workflow_kind,
            passed=False,
            reason="idempotent_replay",
        )
        return {"scheduled": False, "reason": "idempotent_replay"}

    inserted_id = str(dict(row).get("id") or followup_id)
    await _record_engagement_contact(
        db,
        company_id=company_id,
        customer_id=customer_id or "",
        context="post_delivery",
    )
    logger.info(
        "followup_scheduled order_id=%s customer_id=%s workflow_kind=%s followup_id=%s delay_seconds=%s",
        order_id, customer_id, workflow_kind, inserted_id, delay,
    )
    _log_post_conversation_trigger(
        trigger_status=normalized_status,
        order_id=order_id,
        customer_id=customer_id,
        workflow_kind=workflow_kind,
        followup_id=inserted_id,
        passed=True,
    )
    return {"scheduled": True, "followup_id": inserted_id, "workflow_kind": workflow_kind}


async def claim_due_followup(db) -> dict[str, Any] | None:
    """Atomically transition the next due row from scheduled → running.

    Uses FOR UPDATE SKIP LOCKED so two workers polling at the same instant
    cannot both pick up the same row. Returns the claimed row as a dict, or
    None when there is nothing due.
    """
    try:
        row = await db.fetchrow(
            "UPDATE ai_followups "
            "   SET status = 'running', updated_at = NOW() "
            " WHERE id = ( "
            "   SELECT id FROM ai_followups "
            "    WHERE status = 'scheduled' AND scheduled_for <= NOW() "
            "    ORDER BY scheduled_for ASC "
            "    LIMIT 1 "
            "    FOR UPDATE SKIP LOCKED "
            " ) "
            "RETURNING id, company_id, order_id, customer_id, session_id, workflow_kind"
        )
    except Exception as exc:
        logger.warning("claim_due_followup_failed error=%s", exc)
        return None
    if not row:
        return None
    return dict(row)


async def mark_followup_outcome(
    db,
    *,
    followup_id: str,
    status: Literal["completed", "customer_declined", "expired", "cancelled"],
    outcome: str = "",
    outcome_notes: str = "",
) -> None:
    try:
        await db.execute(
            "UPDATE ai_followups "
            "SET status = $1, outcome = $2, outcome_notes = $3, updated_at = NOW() "
            "WHERE id = $4",
            status,
            outcome,
            outcome_notes,
            followup_id,
        )
    except Exception as exc:
        logger.warning("mark_followup_outcome_failed followup_id=%s error=%s", followup_id, exc)


async def schedule_upsell_followup(
    db,
    *,
    company_id: str,
    order_id: str,
    customer_id: str,
    session_id: str,
) -> dict[str, Any]:
    """Schedule the upsell turn after a positive feedback reply. Shares the
    same idempotency / engagement guards as evaluate_order_event."""
    allowed, reason = await _engagement_ok(db, company_id=company_id, customer_id=customer_id)
    if not allowed:
        return {"scheduled": False, "reason": reason}
    workflow_kind: WorkflowKind = "upsell"
    idem = _idempotency_key(order_id, workflow_kind, "delivered")
    followup_id = _new_id("fu", f"{order_id}:{workflow_kind}")
    try:
        row = await db.fetchrow(
            "INSERT INTO ai_followups "
            "(id, company_id, order_id, customer_id, session_id, workflow_kind, "
            " status, scheduled_for, idempotency_key) "
            "VALUES ($1, $2, $3, $4, $5, $6, 'scheduled', NOW() + ($7 || ' seconds')::interval, $8) "
            "ON CONFLICT (idempotency_key) WHERE BTRIM(idempotency_key) <> '' DO NOTHING "
            "RETURNING id",
            followup_id,
            company_id,
            order_id,
            customer_id or "",
            session_id or "",
            workflow_kind,
            str(_UPSELL_DELAY_SECONDS),
            idem,
        )
    except Exception as exc:
        logger.info("upsell_followup_insert_failed order_id=%s error=%s", order_id, exc)
        return {"scheduled": False, "reason": "already_active_or_db_error"}
    if not row:
        return {"scheduled": False, "reason": "idempotent_replay"}
    await _record_engagement_contact(
        db,
        company_id=company_id,
        customer_id=customer_id or "",
        context="upsell",
    )
    return {"scheduled": True, "followup_id": str(dict(row).get("id") or followup_id), "workflow_kind": workflow_kind}
