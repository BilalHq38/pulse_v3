"""Wave 4 — dispatcher loop tests.

Covers dispatch_one_followup with a fake DB and a monkeypatched
run_proactive_turn so no live engine call is made.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from services.conversation_engine.schemas import TokenUsage, TurnResult
from services.followup_scheduler import loop as loop_module


@dataclass
class _FakeDb:
    conversations: dict[str, dict] = field(default_factory=dict)
    followups: dict[str, dict] = field(default_factory=dict)
    messages: list[dict] = field(default_factory=list)
    automation_log: list[dict] = field(default_factory=list)
    executed: list[tuple] = field(default_factory=list)

    async def fetchrow(self, sql, *args):
        if "FROM conversations" in sql and "session_id" in sql:
            company_id, session_id = args
            for c in self.conversations.values():
                if c.get("company_id") == company_id and c.get("session_id") == session_id:
                    return {"id": c["id"], "company_id": company_id, "customer_id": c.get("customer_id", "")}
            return None
        return None

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        if "INSERT INTO messages" in sql:
            (mid, company_id, conversation_id, content, confidence) = args
            self.messages.append({
                "id": mid, "company_id": company_id, "conversation_id": conversation_id,
                "content": content, "confidence": confidence,
            })
        elif "INSERT INTO automation_log" in sql:
            (lid, company_id, workflow_kind, followup_id, order_id, customer_id, outcome, duration_ms) = args
            self.automation_log.append({
                "id": lid, "company_id": company_id, "workflow_kind": workflow_kind,
                "followup_id": followup_id, "order_id": order_id, "customer_id": customer_id,
                "outcome": outcome, "duration_ms": duration_ms,
            })
        elif "UPDATE ai_followups" in sql and "SET status = $1" in sql:
            (status, outcome, notes, fid) = args
            row = self.followups.get(fid, {})
            row.update({"status": status, "outcome": outcome, "outcome_notes": notes})
            self.followups[fid] = row


def _ok_turn_result(answer="Hi! Did your order arrive okay?") -> TurnResult:
    return TurnResult(
        answer=answer,
        session_id="s1",
        turn_id="t_x",
        sources_used=["company_data"],
        product_links=[],
        tokens_used=TokenUsage(prompt=10, completion=10, total=20),
        active_template="Friendly",
        confidence=0.7,
    )


def _claimed_row(workflow_kind="post_delivery_feedback"):
    return {
        "id": "fu1",
        "company_id": "c1",
        "order_id": "o1",
        "customer_id": "cu1",
        "session_id": "s1",
        "workflow_kind": workflow_kind,
    }


def test_dispatch_persists_message_and_logs_automation(monkeypatch):
    db = _FakeDb(conversations={
        "convo1": {"id": "convo1", "company_id": "c1", "session_id": "s1", "customer_id": "cu1"},
    })

    async def fake_turn(_db, **_kwargs):
        return _ok_turn_result()

    monkeypatch.setattr(loop_module, "run_proactive_turn", fake_turn)
    # Silence the socket broadcast — it's best-effort already, but we want
    # the test to be deterministic about not raising on missing socket setup.
    async def noop(*_a, **_kw):
        return None
    monkeypatch.setattr(loop_module, "_emit_socket_message", noop)

    outcome = asyncio.run(loop_module.dispatch_one_followup(db, _claimed_row()))
    assert outcome["dispatched"] is True
    assert len(db.messages) == 1
    assert db.messages[0]["conversation_id"] == "convo1"
    assert db.messages[0]["content"].startswith("Hi!")
    assert len(db.automation_log) == 1
    assert db.automation_log[0]["outcome"] == "dispatched"


def test_dispatch_marks_expired_when_conversation_missing(monkeypatch):
    db = _FakeDb()  # no conversation for session s1

    async def fake_turn(_db, **_kwargs):
        raise AssertionError("engine should not be called when convo is missing")

    monkeypatch.setattr(loop_module, "run_proactive_turn", fake_turn)

    outcome = asyncio.run(loop_module.dispatch_one_followup(db, _claimed_row()))
    assert outcome["dispatched"] is False
    assert outcome["reason"] == "conversation_missing"
    # No message persisted; automation_log records the expired outcome.
    assert not db.messages
    assert db.automation_log[0]["outcome"] == "expired_no_conversation"


def test_dispatch_marks_expired_on_empty_engine_answer(monkeypatch):
    db = _FakeDb(conversations={
        "convo1": {"id": "convo1", "company_id": "c1", "session_id": "s1", "customer_id": "cu1"},
    })

    async def fake_turn(_db, **_kwargs):
        return _ok_turn_result(answer="")

    monkeypatch.setattr(loop_module, "run_proactive_turn", fake_turn)
    async def noop(*_a, **_kw):
        return None
    monkeypatch.setattr(loop_module, "_emit_socket_message", noop)

    outcome = asyncio.run(loop_module.dispatch_one_followup(db, _claimed_row()))
    assert outcome["dispatched"] is False
    assert outcome["reason"] == "empty_response"
    assert not db.messages


def test_dispatch_marks_expired_on_engine_exception(monkeypatch):
    db = _FakeDb(conversations={
        "convo1": {"id": "convo1", "company_id": "c1", "session_id": "s1", "customer_id": "cu1"},
    })

    async def fake_turn(_db, **_kwargs):
        raise RuntimeError("LLM service down")

    monkeypatch.setattr(loop_module, "run_proactive_turn", fake_turn)

    outcome = asyncio.run(loop_module.dispatch_one_followup(db, _claimed_row()))
    assert outcome["dispatched"] is False
    assert outcome["reason"] == "engine_exception"
    # Followup row gets marked expired with the exception trace in outcome_notes.
    update_calls = [
        e for e in db.executed
        if e[0].lstrip().startswith("UPDATE ai_followups") and "SET status = $1" in e[0]
    ]
    assert update_calls
    args = update_calls[0][1]
    assert args[0] == "expired"
    assert "LLM service down" in str(args[2])


def test_run_proactive_turn_uses_directive_based_on_workflow_kind(monkeypatch):
    """Verify the orchestrator's run_proactive_turn synthesises a directive
    for the correct workflow kind and routes through run_turn."""
    from services.conversation_engine import orchestrator as orch_module

    captured = {}

    async def fake_run_turn(self, db, request):
        captured["mode"] = request.mode
        captured["workflow_kind"] = request.workflow_kind
        captured["user_message"] = request.user_message
        return _ok_turn_result()

    monkeypatch.setattr(orch_module.Orchestrator, "run_turn", fake_run_turn)

    asyncio.run(orch_module.default_orchestrator.run_proactive_turn(
        db=object(),
        company_id="c1", session_id="s1",
        customer_id="cu1", order_id="o1",
        workflow_kind="upsell",
    ))
    assert captured["mode"] == "proactive"
    assert captured["workflow_kind"] == "upsell"
    assert "complementary product" in captured["user_message"]

    asyncio.run(orch_module.default_orchestrator.run_proactive_turn(
        db=object(),
        company_id="c1", session_id="s1",
        workflow_kind="post_delivery_feedback",
    ))
    assert captured["workflow_kind"] == "post_delivery_feedback"
    assert "arrived in good condition" in captured["user_message"]
