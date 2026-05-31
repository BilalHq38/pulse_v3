"""Glue that lets the web-chat webhook source its response from the new
conversation engine while keeping the legacy workflow as the metadata
authority (sentiment, intent, qualification, escalation).

The helpers here are kept in a standalone module so they can be unit-tested
without importing the full FastAPI router graph.
"""

from __future__ import annotations

from typing import Iterable


async def company_uses_conversation_engine(db, company_id: str) -> bool:
    """Read the per-company opt-in flag.

    Returns False on any error so the default behaviour stays the legacy
    workflow — a missing column / row / DB connection must not silently flip
    a tenant onto an unproven path.
    """
    if not company_id:
        return False
    try:
        row = await db.fetchrow(
            "SELECT COALESCE(ai_use_conversation_engine, FALSE) AS v "
            "FROM company_settings WHERE company_id = $1",
            company_id,
        )
    except Exception:
        return False
    return bool(row and row.get("v"))


def _is_active_order_flow_plan(support_plan: dict) -> bool:
    plan = dict(support_plan or {})
    if not str(plan.get("response") or "").strip():
        return False
    if not plan.get("deliver_response", False):
        return False
    if str(plan.get("conversation_stage") or "").strip().lower() == "order_flow":
        return True
    if str(plan.get("model_name") or "").strip().lower() == "deterministic-order-flow":
        return True
    return str(plan.get("next_action") or "").strip().lower() in {
        "awaiting_product_selection",
        "clarify_selected_product",
        "collect_order_details",
        "request_order_confirmation",
        "order_placed",
        "order_cancelled",
        "order_already_placed",
    }


def apply_engine_response_to_support_plan(
    *,
    support_plan: dict,
    capture: dict,
    engine_answer: str,
    engine_confidence: float,
    engine_turn_id: str,
    engine_product_links: Iterable,
    engine_sources_used: Iterable[str] | None = None,
) -> dict:
    """Synthesise a support_plan that drives the webhook's existing send path
    using the engine's answer.

    Escalation is sourced from the legacy capture's sentiment_gate so we keep
    the existing safety net (toxicity / handoff request / api exhaustion) even
    when the engine produces the actual reply.
    """
    sentiment_gate = dict((capture or {}).get("sentiment_gate") or {})
    ai_response_allowed = sentiment_gate.get("ai_response_allowed")
    # Only escalate on an explicit False — None means "no opinion".
    should_escalate = ai_response_allowed is False
    merged = dict(support_plan or {})
    if _is_active_order_flow_plan(merged):
        merged["engine_override_skipped_reason"] = "active_order_flow"
        if engine_turn_id:
            merged["engine_turn_id"] = engine_turn_id
        return merged
    merged.update(
        {
            "response": engine_answer,
            "confidence": float(engine_confidence or 0.0),
            "deliver_response": (not should_escalate) and bool(engine_answer),
            "escalate": should_escalate,
            "escalation_reason": str(sentiment_gate.get("recommended_action") or "")
            if should_escalate
            else "",
            "next_action": "manual_review" if should_escalate else "conversation_engine_reply",
            "api_error": False,
            "engine_turn_id": engine_turn_id,
            "sources_used": list(engine_sources_used or []),
            "product_links": [
                {
                    "product_id": pl.product_id,
                    "url": pl.url,
                    "name": getattr(pl, "name", ""),
                    "image_url": getattr(pl, "image_url", ""),
                }
                for pl in (engine_product_links or [])
            ],
        }
    )
    return merged
