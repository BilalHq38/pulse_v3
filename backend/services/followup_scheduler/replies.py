"""Reply handler for in-flight proactive follow-ups.

Called from the inbound webhook when an `ai_followups` row in `running` state
matches the inbound session_id. Responsibilities:

  1. Run the conversational opt-out check — fires first so a "stop messaging
     me" reply always wins, regardless of the follow-up's workflow_kind.
  2. Persist the reply into `customer_feedback`.
  3. Transition the `ai_followups` row to `completed` or `customer_declined`.
  4. On a positive post-delivery reply, schedule the upsell turn (subject to
     the engagement opt-out / cooldown rules already enforced by
     `schedule_upsell_followup`).

Canned acknowledgement copy returned by `on_customer_reply`:
  - opt_out_acknowledged → "Understood — we won't reach out again."
  - feedback_recorded    → "Thanks — your feedback has been recorded."
The webhook persists this text as a normal `messages` row so the chat history
stays consistent without an extra Gemini call.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from services.followup_scheduler.disengagement import detect_disengagement
from services.followup_scheduler.scheduler import (
    mark_followup_outcome,
    schedule_upsell_followup,
)
from services.followup_scheduler.sentiment import classify_sentiment

logger = logging.getLogger(__name__)


# Canned acknowledgement copy the webhook persists after on_customer_reply
# returns. Kept module-level constants so the webhook glue and tests share the
# same strings.
ACK_OPT_OUT = "Understood — we won't reach out again."
ACK_FEEDBACK = "Thanks — your feedback has been recorded."


def ack_message_for(action: str) -> str:
    return ACK_OPT_OUT if action == "opt_out_acknowledged" else ACK_FEEDBACK


def _new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000):x}"


async def _persist_feedback(
    db,
    *,
    company_id: str,
    customer_id: str,
    order_id: str,
    session_id: str,
    followup_id: str,
    raw_response: str,
    sentiment: str,
) -> None:
    try:
        await db.execute(
            "INSERT INTO customer_feedback "
            "(id, company_id, customer_id, order_id, session_id, followup_id, sentiment, raw_response) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            _new_id("fb"),
            company_id,
            customer_id or "",
            order_id or "",
            session_id or "",
            followup_id or "",
            sentiment,
            raw_response or "",
        )
    except Exception as exc:
        logger.warning("customer_feedback_insert_failed followup_id=%s error=%s", followup_id, exc)


async def _flip_engagement_opt_out(db, *, company_id: str, customer_id: str) -> None:
    if not customer_id:
        return
    try:
        await db.execute(
            "INSERT INTO customer_engagement (company_id, customer_id, opted_out) "
            "VALUES ($1, $2, TRUE) "
            "ON CONFLICT (company_id, customer_id) DO UPDATE SET opted_out = TRUE",
            company_id,
            customer_id,
        )
    except Exception as exc:
        logger.warning("customer_engagement_opt_out_failed customer_id=%s error=%s", customer_id, exc)


async def on_customer_reply(
    db,
    *,
    followup: dict[str, Any],
    text: str,
) -> dict[str, Any]:
    """Handle a customer reply that landed against a running proactive turn.

    `followup` is the row claimed earlier (must include id, company_id,
    order_id, customer_id, session_id, workflow_kind). Returns a small dict
    describing the outcome so the webhook caller can decide what to send back.
    """
    company_id = str(followup.get("company_id") or "")
    customer_id = str(followup.get("customer_id") or "")
    followup_id = str(followup.get("id") or "")
    order_id = str(followup.get("order_id") or "")
    session_id = str(followup.get("session_id") or "")
    workflow_kind = str(followup.get("workflow_kind") or "post_delivery_feedback")
    text = (text or "").strip()

    if detect_disengagement(text):
        await _flip_engagement_opt_out(db, company_id=company_id, customer_id=customer_id)
        await _persist_feedback(
            db,
            company_id=company_id,
            customer_id=customer_id,
            order_id=order_id,
            session_id=session_id,
            followup_id=followup_id,
            raw_response=text,
            sentiment="opted_out",
        )
        await mark_followup_outcome(
            db,
            followup_id=followup_id,
            status="customer_declined",
            outcome="disengagement_detected",
        )
        return {
            "action": "opt_out_acknowledged",
            "workflow_kind": workflow_kind,
            "schedule_upsell": False,
        }

    sentiment = classify_sentiment(text)
    await _persist_feedback(
        db,
        company_id=company_id,
        customer_id=customer_id,
        order_id=order_id,
        session_id=session_id,
        followup_id=followup_id,
        raw_response=text,
        sentiment=sentiment,
    )
    await mark_followup_outcome(db, followup_id=followup_id, status="completed", outcome=sentiment)

    should_schedule_upsell = (
        workflow_kind == "post_delivery_feedback"
        and sentiment == "positive"
        and bool(order_id)
    )
    upsell_result: dict[str, Any] | None = None
    if should_schedule_upsell:
        upsell_result = await schedule_upsell_followup(
            db,
            company_id=company_id,
            order_id=order_id,
            customer_id=customer_id,
            session_id=session_id,
        )

    return {
        "action": "feedback_recorded",
        "workflow_kind": workflow_kind,
        "sentiment": sentiment,
        "schedule_upsell": should_schedule_upsell,
        "upsell": upsell_result,
    }
