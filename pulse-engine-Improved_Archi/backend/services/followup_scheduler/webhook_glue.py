"""Webhook-side glue for proactive follow-up reply routing.

`maybe_handle_followup_reply(db, company_id, session_id, text)` is the single
call site the inbound webhook needs. It returns `None` when the inbound
message is *not* a reply to a running follow-up — caller continues normal
reactive flow. Otherwise it returns a dict describing what happened and the
canned acknowledgement text the webhook should send as the AI message.

Kept separate from the router so it can be unit-tested without importing
FastAPI's router graph.
"""

from __future__ import annotations

import logging
from typing import Any

from services.followup_scheduler.replies import ack_message_for, on_customer_reply

logger = logging.getLogger(__name__)


async def _fetch_running_followup(db, *, company_id: str, session_id: str) -> dict | None:
    if not company_id or not session_id:
        return None
    try:
        row = await db.fetchrow(
            "SELECT id, company_id, order_id, customer_id, session_id, workflow_kind "
            "FROM ai_followups "
            "WHERE company_id = $1 AND session_id = $2 AND status = 'running' "
            "ORDER BY scheduled_for DESC LIMIT 1",
            company_id,
            session_id,
        )
    except Exception as exc:
        logger.warning(
            "followup_lookup_failed company_id=%s session_id=%s error=%s",
            company_id, session_id, exc,
        )
        return None
    if not row:
        return None
    return dict(row)


async def maybe_handle_followup_reply(
    db,
    *,
    company_id: str,
    session_id: str,
    text: str,
) -> dict[str, Any] | None:
    """Returns None when no running follow-up matches the session.

    Otherwise persists feedback / opt-out / upsell scheduling via
    `on_customer_reply` and returns a dict with:
      - action:        "opt_out_acknowledged" | "feedback_recorded"
      - sentiment:     "" | "positive" | "neutral" | "negative" | "opted_out"
      - ack_message:   the canned reply text the webhook should send
      - schedule_upsell:  bool
      - followup_id:   the row that was acted on
      - workflow_kind: "post_delivery_feedback" | "upsell"
    """
    followup = await _fetch_running_followup(db, company_id=company_id, session_id=session_id)
    if followup is None:
        return None
    outcome = await on_customer_reply(db, followup=followup, text=text)
    return {
        **outcome,
        "ack_message": ack_message_for(str(outcome.get("action") or "")),
        "followup_id": followup.get("id"),
    }
