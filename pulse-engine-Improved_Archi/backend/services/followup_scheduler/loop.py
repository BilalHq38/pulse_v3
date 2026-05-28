"""Periodic worker that turns scheduled `ai_followups` rows into outbound turns.

The loop is registered alongside the existing data_pipeline scheduler at app
startup. Every `POLL_INTERVAL_SECONDS` it:
  1. Claims one due row (FOR UPDATE SKIP LOCKED — two workers never race).
  2. Calls `orchestrator.run_proactive_turn` to synthesise the outbound text.
  3. Persists the result as a new `messages` row scoped to the original
     conversation, then broadcasts it through the channel layer.
  4. Marks the follow-up `running` so the inbound-reply branch in the webhook
     routes the customer's next message into `on_customer_reply`.
  5. Writes an `automation_log` row for telemetry.

Cancellation is cooperative through an `asyncio.Event`. Stopping the loop
flips the event and waits for the current iteration to drain.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from core.utils import make_id
from services.conversation_engine import run_proactive_turn
from services.followup_scheduler.scheduler import claim_due_followup, mark_followup_outcome
from shared.database import platform_admin_context

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = int(os.environ.get("AI_FOLLOWUP_POLL_INTERVAL_SECONDS", "60") or 60)
# Cap the per-iteration work so a backlog doesn't starve other scheduler loops
# on the same event loop.
MAX_DISPATCHES_PER_ITERATION = int(os.environ.get("AI_FOLLOWUP_MAX_PER_ITERATION", "10") or 10)


@dataclass(slots=True)
class FollowupSchedulerHandle:
    task: asyncio.Task
    stop_event: asyncio.Event


async def _lookup_conversation(db, *, company_id: str, session_id: str) -> dict | None:
    if not company_id or not session_id:
        return None
    try:
        row = await db.fetchrow(
            "SELECT id, company_id, customer_id FROM conversations "
            "WHERE company_id = $1 AND session_id = $2 LIMIT 1",
            company_id,
            session_id,
        )
    except Exception as exc:
        logger.warning("followup_loop_convo_lookup_failed error=%s", exc)
        return None
    return dict(row) if row else None


async def _persist_outbound_ai_message(
    db,
    *,
    company_id: str,
    conversation_id: str,
    text: str,
    confidence: float,
) -> str:
    msg_id = make_id()
    try:
        await db.execute(
            "INSERT INTO messages "
            "(id, company_id, conversation_id, content, sender_type, sender_id, sender_name, "
            " ai_confidence, delivery_status, read, created_at) "
            "VALUES ($1, $2, $3, $4, 'ai', 'ai-assistant', 'AI Assistant', $5, 'sent', FALSE, NOW())",
            msg_id, company_id, conversation_id, text, float(confidence or 0.0),
        )
        await db.execute(
            "UPDATE conversations SET last_message = $1, last_message_at = NOW(), "
            "message_count = message_count + 1 WHERE id = $2",
            text[:200],
            conversation_id,
        )
    except Exception as exc:
        logger.warning(
            "followup_loop_persist_failed conversation_id=%s error=%s",
            conversation_id, exc,
        )
    return msg_id


async def _log_automation(
    db,
    *,
    company_id: str,
    workflow_kind: str,
    followup_id: str,
    order_id: str,
    customer_id: str,
    outcome: str,
    duration_ms: int,
) -> None:
    try:
        await db.execute(
            "INSERT INTO automation_log "
            "(id, company_id, workflow_kind, followup_id, order_id, customer_id, outcome, duration_ms) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            make_id(), company_id, workflow_kind, followup_id, order_id, customer_id, outcome, duration_ms,
        )
    except Exception as exc:
        logger.debug("automation_log_insert_failed followup_id=%s error=%s", followup_id, exc)


async def _emit_socket_message(conversation_id: str, message_id: str) -> None:
    """Best-effort socket broadcast — never raise (loop must keep running)."""
    try:
        # Late import: the socket helper depends on an asyncio loop context
        # that's only guaranteed inside the running app.
        from core.socket import emit_new_message

        await emit_new_message(conversation_id, {"id": message_id})
    except Exception as exc:
        logger.debug("followup_loop_emit_failed conversation_id=%s error=%s", conversation_id, exc)


async def dispatch_one_followup(db, claimed: dict) -> dict[str, Any]:
    """Run the engine for one claimed follow-up row and persist the result.

    Exposed at module level so it can be called from tests with a fake DB +
    a monkeypatched run_proactive_turn.
    """
    started_at = time.monotonic()
    followup_id = str(claimed.get("id") or "")
    company_id = str(claimed.get("company_id") or "")
    customer_id = str(claimed.get("customer_id") or "")
    session_id = str(claimed.get("session_id") or "")
    order_id = str(claimed.get("order_id") or "")
    workflow_kind = str(claimed.get("workflow_kind") or "post_delivery_feedback")

    convo = await _lookup_conversation(db, company_id=company_id, session_id=session_id)
    if convo is None:
        # No live conversation to attach to — mark the row expired so the
        # loop doesn't keep claiming it on every iteration.
        await mark_followup_outcome(
            db, followup_id=followup_id, status="expired", outcome="conversation_missing",
        )
        await _log_automation(
            db, company_id=company_id, workflow_kind=workflow_kind, followup_id=followup_id,
            order_id=order_id, customer_id=customer_id, outcome="expired_no_conversation",
            duration_ms=int((time.monotonic() - started_at) * 1000),
        )
        return {"dispatched": False, "reason": "conversation_missing"}

    try:
        result = await run_proactive_turn(
            db,
            company_id=company_id,
            session_id=session_id,
            customer_id=customer_id,
            order_id=order_id,
            workflow_kind=workflow_kind,
        )
    except Exception as exc:
        logger.warning(
            "followup_loop_dispatch_failed followup_id=%s error=%s",
            followup_id, exc,
        )
        await mark_followup_outcome(
            db, followup_id=followup_id, status="expired", outcome="engine_exception",
            outcome_notes=str(exc)[:200],
        )
        return {"dispatched": False, "reason": "engine_exception"}

    answer = (result.answer or "").strip()
    if not answer:
        # Engine returned an empty answer (probably the static fallback path
        # failed). Don't send anything; mark the row so it's not retried in a
        # tight loop.
        await mark_followup_outcome(
            db, followup_id=followup_id, status="expired", outcome="empty_response",
        )
        return {"dispatched": False, "reason": "empty_response"}

    convo_id = str(convo.get("id") or "")
    message_id = await _persist_outbound_ai_message(
        db,
        company_id=company_id,
        conversation_id=convo_id,
        text=answer,
        confidence=result.confidence,
    )
    await _emit_socket_message(convo_id, message_id)

    duration_ms = int((time.monotonic() - started_at) * 1000)
    await _log_automation(
        db, company_id=company_id, workflow_kind=workflow_kind, followup_id=followup_id,
        order_id=order_id, customer_id=customer_id, outcome="dispatched",
        duration_ms=duration_ms,
    )
    logger.info(
        "followup_dispatched followup_id=%s workflow_kind=%s conversation_id=%s message_id=%s duration_ms=%s",
        followup_id, workflow_kind, convo_id, message_id, duration_ms,
    )
    return {
        "dispatched": True,
        "message_id": message_id,
        "conversation_id": convo_id,
        "engine_turn_id": result.turn_id,
    }


async def _loop_body(db, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        dispatched = 0
        for _ in range(MAX_DISPATCHES_PER_ITERATION):
            if stop_event.is_set():
                break
            async with platform_admin_context(db):
                claimed = await claim_due_followup(db)
            if claimed is None:
                break  # nothing due right now
            try:
                async with platform_admin_context(db):
                    await dispatch_one_followup(db, claimed)
                dispatched += 1
            except Exception:
                # Defensive: dispatch_one_followup already swallows its own
                # errors, but the outer guard keeps the loop alive in case a
                # future helper raises through.
                logger.exception("followup_loop_iteration_failed claimed=%s", claimed)
        if dispatched == 0:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                continue


async def start_followup_scheduler(db) -> FollowupSchedulerHandle:
    stop_event = asyncio.Event()
    task = asyncio.create_task(_loop_body(db, stop_event), name="ai-followup-scheduler")
    return FollowupSchedulerHandle(task=task, stop_event=stop_event)


async def stop_followup_scheduler(handle: FollowupSchedulerHandle | None) -> None:
    if handle is None:
        return
    handle.stop_event.set()
    handle.task.cancel()
    try:
        await handle.task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("followup scheduler shutdown failed")
