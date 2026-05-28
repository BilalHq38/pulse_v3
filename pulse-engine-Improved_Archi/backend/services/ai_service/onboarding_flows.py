"""Structured onboarding flow for SupportAgent.

Runs a small, deterministic step machine for customers whose
``lifecycle_stage = 'new_customer'`` before falling back to RAG or LLM
support. Flow state is stored on ``customers.metadata -> onboarding``
(JSONB) so no extra table is required.

Steps:
    welcome          -> greet + ask about goal
    collect_goal     -> store goal, ask about usage scale
    collect_usage    -> store usage scale, confirm tailored plan
    confirm          -> summarize, mark complete
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


FLOW_STEPS: tuple[str, ...] = (
    "welcome",
    "collect_goal",
    "collect_usage",
    "confirm",
)


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


async def _load_onboarding_state(db: Any, customer_id: str) -> dict[str, Any]:
    if not customer_id:
        return {}
    try:
        row = await db.fetchval(
            "SELECT metadata FROM customers WHERE id = $1",
            customer_id,
        )
    except Exception as exc:
        logger.debug("onboarding_flows: could not read customers.metadata: %s", exc)
        return {}
    metadata = _to_dict(row)
    return _to_dict(metadata.get("onboarding"))


async def _save_onboarding_state(
    db: Any,
    customer_id: str,
    company_id: str,
    state: dict[str, Any],
) -> None:
    if not customer_id or not company_id:
        return
    try:
        await db.execute(
            """
            UPDATE customers
               SET metadata = jsonb_set(
                       COALESCE(metadata, '{}'::jsonb),
                       '{onboarding}',
                       $3::jsonb,
                       true
                   ),
                   updated_at = NOW()
             WHERE id = $1 AND company_id = $2
            """,
            customer_id,
            company_id,
            json.dumps(state, default=str),
        )
    except Exception as exc:
        logger.debug(
            "onboarding_flows: could not persist onboarding state for customer %s: %s",
            customer_id,
            exc,
        )


def _greeting_name(customer_name: str) -> str:
    name = (customer_name or "").strip()
    if not name:
        return "there"
    return name.split()[0]


def _message_for_step(step: str, state: dict[str, Any], customer_name: str) -> str:
    first = _greeting_name(customer_name)
    goal = str(state.get("goal") or "").strip()
    usage = str(state.get("usage") or "").strip()

    if step == "welcome":
        return (
            f"Hi {first}! Welcome to Pulse Engine. I'm here to help you get set up quickly. "
            "What's the main goal you're trying to achieve with us?"
        )
    if step == "collect_goal":
        return "Got it — thanks for sharing. Roughly how many customers or conversations do you handle per day?"
    if step == "collect_usage":
        return (
            f'Perfect. Based on "{goal or "your goal"}" and about {usage or "your volume"} '
            "per day, I'll queue up a tailored quickstart. Shall I go ahead and finalize this?"
        )
    if step == "confirm":
        return (
            f"You're all set, {first}! I've noted your goal ({goal or 'not specified'}) "
            f"and volume ({usage or 'not specified'}). A teammate will follow up shortly "
            "with a tailored playbook. You can start exploring the dashboard now."
        )
    return ""


def _extract_free_text(message: str) -> str:
    value = (message or "").strip()
    if len(value) > 240:
        return value[:240].rstrip() + "…"
    return value


async def advance_onboarding(
    db: Any,
    *,
    customer_id: str,
    company_id: str,
    message_text: str,
    customer_name: str = "",
) -> dict[str, Any]:
    """Advance the onboarding state machine by one step based on the latest message.

    Returns a dict with:
        step:        the current step label
        response:    the agent's reply to send to the customer
        complete:    True once the flow finishes (then SupportAgent can fall through)
    """
    state = await _load_onboarding_state(db, customer_id)
    current_step = str(state.get("step") or "").strip().lower()
    if current_step not in FLOW_STEPS:
        current_step = "welcome"

    # Don't keep greeting indefinitely — if the flow was marked complete, skip.
    if state.get("complete"):
        return {"step": "complete", "response": "", "complete": True}

    # Progress based on current step.
    text = _extract_free_text(message_text)
    if current_step == "welcome":
        # The customer just sent their first message; treat that as the goal
        # so we don't lose it, then advance.
        if text:
            state["goal"] = text
        next_step = "collect_goal"
        response = _message_for_step(next_step, state, customer_name)
    elif current_step == "collect_goal":
        if text and not state.get("goal"):
            state["goal"] = text
        elif text:
            # Customer expanded on goal — append a summary.
            state["goal"] = f"{state.get('goal', '')}; {text}".strip("; ")
        next_step = "collect_usage"
        response = _message_for_step(next_step, state, customer_name)
    elif current_step == "collect_usage":
        if text:
            state["usage"] = text
        next_step = "confirm"
        response = _message_for_step(next_step, state, customer_name)
    else:  # confirm -> complete
        next_step = "confirm"
        response = _message_for_step("confirm", state, customer_name)
        state["complete"] = True

    state["step"] = next_step
    await _save_onboarding_state(db, customer_id, company_id, state)

    return {
        "step": next_step,
        "response": response,
        "complete": bool(state.get("complete")),
        "goal": state.get("goal", ""),
        "usage": state.get("usage", ""),
    }
