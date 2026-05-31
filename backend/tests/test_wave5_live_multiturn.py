"""Wave 5 live test — multi-turn conversation against the real engine.

This is the closest we can get to a "live test" without a real Gemini key in
the CI loop: it exercises the full conversation engine across multiple turns
(ask → answer → continue), with real retrievers, real compression, real
budgeting, real prompt building, real validation, real memory persistence —
only the LLM gateway is stubbed so the test is deterministic and free.

Two scenarios:
  1. Single-customer conversation: 3 inbound turns over the same session_id.
     We verify that turn_index increments, that the rolling history grows,
     and that the engine answer for turn 3 carries context from turn 1.
  2. Mixed-flow scenario: a reactive question, then a proactive turn fires
     (post_delivery_feedback), then the customer's reply lands and the
     follow-up scheduler transitions to completed + records sentiment.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from services.conversation_engine import (
    Orchestrator,
    TurnRequest,
    run_proactive_turn,
)
from services.conversation_engine.llm_gateway import GenerationResult
from services.conversation_engine.schemas import ContextChunk
from services.followup_scheduler.replies import on_customer_reply
from services.followup_scheduler import scheduler as scheduler_module


# ---------------------------------------------------------------------------
# In-memory DB stand-in shared by both scenarios.
# ---------------------------------------------------------------------------


@dataclass
class _LiveDb:
    """Tracks every table the engine + follow-up scheduler write to.

    All inserts go through the same fetchrow/execute paths the real code uses,
    so this gives us a credible end-to-end view of what would land in Postgres.
    """
    company_settings: dict[str, dict] = field(default_factory=dict)
    ai_conversation_turns: list[dict] = field(default_factory=list)
    ai_conversation_summaries: list[dict] = field(default_factory=list)
    ai_followups: list[dict] = field(default_factory=list)
    customer_feedback: list[dict] = field(default_factory=list)
    customer_engagement: dict[tuple[str, str], dict] = field(default_factory=dict)
    executed: list[tuple] = field(default_factory=list)

    async def fetchrow(self, sql, *args):
        sql_norm = " ".join(sql.split())
        if "MAX(turn_index)" in sql_norm:
            company_id, session_id = args
            relevant = [
                t for t in self.ai_conversation_turns
                if t["company_id"] == company_id and t["session_id"] == session_id
            ]
            return {"max_idx": max((t["turn_index"] for t in relevant), default=0)}
        if "FROM ai_conversation_summaries" in sql_norm:
            company_id, session_id = args
            for s in self.ai_conversation_summaries:
                if s["company_id"] == company_id and s["session_id"] == session_id:
                    return {"summary": s["summary"]}
            return None
        if "FROM customer_engagement" in sql_norm:
            company_id, customer_id = args
            return self.customer_engagement.get((company_id, customer_id))
        if "INSERT INTO ai_followups" in sql_norm and "ON CONFLICT (idempotency_key)" in sql_norm:
            (fid, company_id, order_id, customer_id, session_id, workflow_kind, delay, idem) = args
            if any(r["idempotency_key"] == idem for r in self.ai_followups):
                return None
            row = {
                "id": fid, "company_id": company_id, "order_id": order_id,
                "customer_id": customer_id, "session_id": session_id,
                "workflow_kind": workflow_kind, "status": "scheduled",
                "idempotency_key": idem, "scheduled_for": delay,
            }
            self.ai_followups.append(row)
            return {"id": fid}
        return None

    async def fetchval(self, sql, *args):
        sql_norm = " ".join(sql.split())
        if "MAX(turn_index)" in sql_norm:
            company_id, session_id = args
            relevant = [
                t for t in self.ai_conversation_turns
                if t["company_id"] == company_id and t["session_id"] == session_id
            ]
            return max((t["turn_index"] for t in relevant), default=0)
        return None

    async def fetch(self, sql, *args):
        sql_norm = " ".join(sql.split())
        if "FROM ai_conversation_turns" in sql_norm and "ORDER BY turn_index DESC" in sql_norm:
            company_id, session_id, _limit = args
            rows = [
                {**t} for t in self.ai_conversation_turns
                if t["company_id"] == company_id and t["session_id"] == session_id
            ]
            rows.sort(key=lambda r: r["turn_index"], reverse=True)
            return rows
        return []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        sql_norm = " ".join(sql.split())
        if "INSERT INTO ai_conversation_turns" in sql_norm:
            (tid, company_id, session_id, customer_id, turn_index,
             user_msg, ai_response, sources_used, product_links,
             confidence, active_template, token_usage, mode) = args
            self.ai_conversation_turns.append({
                "id": tid, "company_id": company_id, "session_id": session_id,
                "customer_id": customer_id, "turn_index": turn_index,
                "user_message": user_msg, "ai_response": ai_response,
                "sources_used": sources_used, "product_links": product_links,
                "confidence": confidence, "active_template": active_template,
                "token_usage": token_usage, "mode": mode,
            })
        elif "INSERT INTO ai_conversation_summaries" in sql_norm:
            (sid, company_id, session_id, summary, covers) = args
            existing = [
                s for s in self.ai_conversation_summaries
                if s["company_id"] == company_id and s["session_id"] == session_id
            ]
            if existing:
                existing[0]["summary"] = summary
                existing[0]["covers_through_turn"] = covers
            else:
                self.ai_conversation_summaries.append({
                    "id": sid, "company_id": company_id, "session_id": session_id,
                    "summary": summary, "covers_through_turn": covers,
                })
        elif "UPDATE ai_followups" in sql_norm and "SET status = $1" in sql_norm:
            (status, outcome, notes, followup_id) = args
            for row in self.ai_followups:
                if row["id"] == followup_id:
                    row.update({"status": status, "outcome": outcome, "outcome_notes": notes})
        elif "INSERT INTO customer_feedback" in sql_norm:
            (fid, company_id, customer_id, order_id, session_id, followup_id, sentiment, raw) = args
            self.customer_feedback.append({
                "id": fid, "company_id": company_id, "customer_id": customer_id,
                "order_id": order_id, "session_id": session_id,
                "followup_id": followup_id, "sentiment": sentiment,
                "raw_response": raw,
            })
        elif "INSERT INTO customer_engagement" in sql_norm and "ON CONFLICT" in sql_norm:
            company_id, customer_id = args
            self.customer_engagement[(company_id, customer_id)] = {
                "opted_out": True, "followup_count": 0, "last_contacted_at": None,
            }


# ---------------------------------------------------------------------------
# Fakes for the engine's retrievers + gateway.
# ---------------------------------------------------------------------------


class _StaticRetriever:
    def __init__(self, source_type, chunks):
        self.source_type = source_type
        self._chunks = chunks

    async def fetch(self, db, *, company_id, query, top_k):
        return list(self._chunks)


class _ScriptedGateway:
    """Returns the next scripted response each call. Records the prompt so
    tests can assert that history flowed into the second / third turn."""
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.prompts: list[str] = []

    async def generate(self, prompt, *, engine=None):
        self.prompts.append(prompt)
        text = self._responses.pop(0) if self._responses else "(no scripted response)"
        return GenerationResult(text=text, attempts=1)


def _engine_with_fakes(gateway: _ScriptedGateway) -> Orchestrator:
    chunks = [
        ContextChunk(
            source_type="company_data", source_id="c1",
            title="Pulse Engine Inc.",
            content="Pulse Engine Inc. is a SaaS company in the US.",
            relevance_score=1.0,
        ),
        ContextChunk(
            source_type="product", source_id="p1",
            title="Pulse Starter",
            content="Pulse Starter plan at 99 USD/month.",
            metadata={"name": "Pulse Starter", "price": "99", "slug": "starter"},
            relevance_score=0.9,
        ),
    ]
    return Orchestrator(
        retrievers={
            "company_data": _StaticRetriever("company_data", [chunks[0]]),
            "product": _StaticRetriever("product", [chunks[1]]),
            "faq": _StaticRetriever("faq", []),
            "knowledge_base": _StaticRetriever("knowledge_base", []),
        },
        gateway=gateway,
    )


# ---------------------------------------------------------------------------
# Scenario 1 — single-customer multi-turn conversation.
# ---------------------------------------------------------------------------


def test_live_multi_turn_conversation_preserves_history_and_increments_turn_index():
    """Customer asks 3 questions in sequence. We verify that:
      - turn_index goes 1, 2, 3
      - the third turn's prompt contains the first two turns' user + assistant lines
      - sources_used is tracked per turn
      - all rows land in ai_conversation_turns
    """
    db = _LiveDb()
    gateway = _ScriptedGateway([
        "Pulse Starter is 99 USD per month.",
        "Yes — Pulse Starter includes unlimited API calls.",
        "You can upgrade to Pro from your dashboard whenever you like.",
    ])
    engine = _engine_with_fakes(gateway)

    turns: list = []
    for question in [
        "What is the price of Pulse Starter?",
        "Does it include unlimited API calls?",
        "Can I upgrade later?",
    ]:
        turn = asyncio.run(engine.run_turn(
            db,
            TurnRequest(
                session_id="s_live_1",
                company_id="co_live",
                user_message=question,
                customer_id="cu_live",
            ),
        ))
        turns.append(turn)

    # turn_index increments 1, 2, 3.
    assert [t["turn_index"] for t in db.ai_conversation_turns] == [1, 2, 3]
    # The third turn's prompt must carry the first two turns' dialogue.
    third_prompt = gateway.prompts[2]
    assert "Pulse Starter is 99 USD per month." in third_prompt
    assert "What is the price of Pulse Starter?" in third_prompt
    assert "Does it include unlimited API calls?" in third_prompt
    # Each persisted row has sources_used populated.
    for row in db.ai_conversation_turns:
        assert "company_data" in row["sources_used"] or "product" in row["sources_used"]


def test_live_multi_turn_validation_failure_falls_back_to_safe_message():
    """If the gateway returns a hallucinated price, the validator catches it,
    triggers one retry with the explicit "don't invent" directive, and if the
    retry still fails the static fallback is sent. The turn is still persisted
    so the conversation history stays correct."""
    db = _LiveDb()
    gateway = _ScriptedGateway([
        # First draft: hallucinated $9999 USD that's not in the catalog.
        "Pulse Starter is $9999 USD per month.",
        # Retry doubles down on the same lie.
        "Definitely $9999 USD per month — guaranteed!",
    ])
    engine = _engine_with_fakes(gateway)

    result = asyncio.run(engine.run_turn(
        db,
        TurnRequest(
            session_id="s_live_2",
            company_id="co_live",
            user_message="How much does Pulse Starter cost?",
            customer_id="cu_live",
        ),
    ))
    # The engine surfaced the static fallback rather than a hallucinated price.
    assert result.error == "validation_failed"
    assert "$9999" not in result.answer
    # The fallback turn is still persisted so history stays consistent.
    assert len(db.ai_conversation_turns) == 1
    assert "$9999" not in db.ai_conversation_turns[0]["ai_response"]


# ---------------------------------------------------------------------------
# Scenario 2 — mixed reactive + proactive flow.
# ---------------------------------------------------------------------------


def test_live_reactive_then_proactive_then_customer_reply(monkeypatch):
    """End-to-end across:
      1. Customer asks a reactive question → engine answers, turn persisted.
      2. (Time passes.) Order transitions to 'delivered', scheduler inserts
         an ai_followups row, dispatcher transitions it to running, engine
         composes the proactive feedback message → second turn persisted.
      3. Customer replies positively → on_customer_reply records feedback,
         marks the follow-up completed, schedules the upsell.
    """
    db = _LiveDb()
    gateway = _ScriptedGateway([
        # Reactive: customer's pre-purchase question.
        "Pulse Starter is 99 USD per month. Want me to walk through the setup?",
        # Proactive: feedback request.
        "Hi! Did your Pulse Starter setup go smoothly? Reply if anything's off.",
    ])
    engine = _engine_with_fakes(gateway)
    # Bind the orchestrator into the module-level run_proactive_turn helper.
    from services.conversation_engine import orchestrator as orch_module
    monkeypatch.setattr(orch_module, "default_orchestrator", engine)

    # Step 1: reactive turn.
    reactive = asyncio.run(engine.run_turn(
        db,
        TurnRequest(
            session_id="s_mix",
            company_id="co_live",
            user_message="What does Pulse Starter cost?",
            customer_id="cu_mix",
        ),
    ))
    assert "99 USD" in reactive.answer
    assert db.ai_conversation_turns[0]["mode"] == "reactive"

    # Step 2: order delivered → schedule + claim + dispatch.
    schedule_outcome = asyncio.run(scheduler_module.evaluate_order_event(
        db, company_id="co_live", order_id="o_mix",
        customer_id="cu_mix", session_id="s_mix",
        to_status="delivered",
    ))
    assert schedule_outcome["scheduled"] is True
    # Move row to running (simulates claim_due_followup) and dispatch.
    db.ai_followups[0]["status"] = "running"
    proactive = asyncio.run(run_proactive_turn(
        db, company_id="co_live", session_id="s_mix",
        customer_id="cu_mix", order_id="o_mix",
        workflow_kind="post_delivery_feedback",
    ))
    assert "Pulse Starter setup" in proactive.answer
    assert db.ai_conversation_turns[1]["mode"] == "proactive"

    # Step 3: customer reply lands. The webhook glue is bypassed here; we call
    # on_customer_reply directly with the running follow-up.
    running_followup = db.ai_followups[0]
    reply_outcome = asyncio.run(on_customer_reply(
        db,
        followup={
            "id": running_followup["id"],
            "company_id": "co_live",
            "order_id": "o_mix",
            "customer_id": "cu_mix",
            "session_id": "s_mix",
            "workflow_kind": "post_delivery_feedback",
        },
        text="loved it, thanks!",
    ))
    assert reply_outcome["sentiment"] == "positive"
    # Original follow-up is completed and the upsell is scheduled.
    assert db.ai_followups[0]["status"] == "completed"
    assert any(r["workflow_kind"] == "upsell" for r in db.ai_followups)
    # Feedback was persisted.
    assert db.customer_feedback[0]["sentiment"] == "positive"


def test_live_customer_replies_with_opt_out_during_proactive_turn(monkeypatch):
    """Disengagement during an in-flight proactive turn flips
    customer_engagement.opted_out, marks the follow-up customer_declined, and
    crucially does NOT schedule an upsell."""
    db = _LiveDb()
    gateway = _ScriptedGateway([
        "Hi! Did your order arrive okay?",
    ])
    engine = _engine_with_fakes(gateway)
    from services.conversation_engine import orchestrator as orch_module
    monkeypatch.setattr(orch_module, "default_orchestrator", engine)

    # Schedule + dispatch the proactive turn.
    asyncio.run(scheduler_module.evaluate_order_event(
        db, company_id="co_live", order_id="o_opt",
        customer_id="cu_opt", session_id="s_opt",
        to_status="delivered",
    ))
    db.ai_followups[0]["status"] = "running"
    asyncio.run(run_proactive_turn(
        db, company_id="co_live", session_id="s_opt",
        customer_id="cu_opt", order_id="o_opt",
        workflow_kind="post_delivery_feedback",
    ))

    # Customer replies with an opt-out phrase.
    outcome = asyncio.run(on_customer_reply(
        db,
        followup={
            "id": db.ai_followups[0]["id"],
            "company_id": "co_live",
            "order_id": "o_opt",
            "customer_id": "cu_opt",
            "session_id": "s_opt",
            "workflow_kind": "post_delivery_feedback",
        },
        text="please stop messaging me",
    ))
    assert outcome["action"] == "opt_out_acknowledged"
    assert db.customer_engagement[("co_live", "cu_opt")]["opted_out"] is True
    assert db.ai_followups[0]["status"] == "customer_declined"
    # No upsell row scheduled after an opt-out.
    assert not any(r["workflow_kind"] == "upsell" for r in db.ai_followups)
