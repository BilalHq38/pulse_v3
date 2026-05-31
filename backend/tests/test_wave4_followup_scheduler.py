"""Wave 4 — follow-up scheduler unit tests.

Pure-logic checks for disengagement detection, sentiment classification,
scheduler insert/claim, and the on_customer_reply flow. The webhook glue
helper is covered in test_wave4_webhook_routing.py.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from services.followup_scheduler import (
    classify_sentiment,
    detect_disengagement,
)
from services.followup_scheduler import scheduler as scheduler_module
from services.followup_scheduler.replies import on_customer_reply


# ---------------------------------------------------------------------------
# disengagement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "stop messaging me please",
        "Please STOP.",
        "no more messages thanks",
        "unsubscribe",
        "do not contact me",
        "I am not interested",
        "remove me from your list",
    ],
)
def test_disengagement_detects_obvious_phrases(text):
    assert detect_disengagement(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "I love the product, thanks!",
        "Could you please send me a quote?",
        "When does my order ship?",
        "The stopwatch in the box is broken",  # 'stop' substring must not fire
        "",
    ],
)
def test_disengagement_does_not_fire_on_normal_replies(text):
    assert detect_disengagement(text) is False


# ---------------------------------------------------------------------------
# sentiment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("I love it! Works perfectly.", "positive"),
        ("Thanks, great product.", "positive"),
        ("It is broken and I want a refund", "negative"),
        ("Late delivery, very disappointed", "negative"),
        ("Got it.", "neutral"),
        ("", "neutral"),
        # Negative wins ties — never upsell to an unhappy customer.
        ("Great packaging but defective product", "negative"),
    ],
)
def test_sentiment_classifier(text, expected):
    assert classify_sentiment(text) == expected


# ---------------------------------------------------------------------------
# scheduler — evaluate_order_event / claim_due_followup / mark_followup_outcome
# ---------------------------------------------------------------------------


@dataclass
class _FakeDb:
    """Minimal async DB stub backed by an in-memory list of ai_followups rows."""
    followups: list[dict] = field(default_factory=list)
    engagement: dict[tuple[str, str], dict] = field(default_factory=dict)
    feedback: list[dict] = field(default_factory=list)
    executed: list[tuple] = field(default_factory=list)

    async def fetchrow(self, sql, *args):
        # INSERT INTO ai_followups ... ON CONFLICT (idempotency_key) DO NOTHING RETURNING id
        if "INSERT INTO ai_followups" in sql and "ON CONFLICT (idempotency_key)" in sql:
            (fid, company_id, order_id, customer_id, session_id, workflow_kind, delay, idem) = args
            for row in self.followups:
                if row.get("idempotency_key") == idem:
                    return None
            row = {
                "id": fid,
                "company_id": company_id,
                "order_id": order_id,
                "customer_id": customer_id,
                "session_id": session_id,
                "workflow_kind": workflow_kind,
                "status": "scheduled",
                "idempotency_key": idem,
                "scheduled_for": delay,
            }
            self.followups.append(row)
            return {"id": fid}

        # UPDATE ai_followups SET status='running' ... FOR UPDATE SKIP LOCKED ... RETURNING ...
        if sql.lstrip().startswith("UPDATE ai_followups") and "status = 'running'" in sql:
            for row in self.followups:
                if row.get("status") == "scheduled":
                    row["status"] = "running"
                    return {k: row[k] for k in (
                        "id", "company_id", "order_id", "customer_id", "session_id", "workflow_kind"
                    )}
            return None

        # SELECT engagement
        if "FROM customer_engagement" in sql:
            company_id, customer_id = args
            return self.engagement.get((company_id, customer_id))

        return None

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        if sql.lstrip().startswith("UPDATE ai_followups"):
            (status, outcome, notes, followup_id) = args
            for row in self.followups:
                if row.get("id") == followup_id:
                    row["status"] = status
                    row["outcome"] = outcome
                    row["outcome_notes"] = notes
        if "INSERT INTO customer_feedback" in sql:
            (fid, company_id, customer_id, order_id, session_id, followup_id, sentiment, raw) = args
            self.feedback.append({
                "id": fid, "company_id": company_id, "customer_id": customer_id,
                "order_id": order_id, "session_id": session_id, "followup_id": followup_id,
                "sentiment": sentiment, "raw_response": raw,
            })
        if "INSERT INTO customer_engagement" in sql and "ON CONFLICT" in sql:
            (company_id, customer_id) = args
            self.engagement[(company_id, customer_id)] = {
                "opted_out": True, "followup_count": 0, "last_contacted_at": None,
            }


def test_evaluate_order_event_schedules_delivery_completion_aliases():
    db = _FakeDb()
    result = asyncio.run(scheduler_module.evaluate_order_event(
        db, company_id="c1", order_id="o1", customer_id="cu1", session_id="s1",
        to_status="shipped",
    ))
    assert result["scheduled"] is False
    assert result["reason"] == "not_a_followup_status"
    assert not db.followups

    for index, status in enumerate(("delivered", "completed", "delivery_completed", "order_completed")):
        result = asyncio.run(scheduler_module.evaluate_order_event(
            db,
            company_id="c1",
            order_id=f"o-delivery-{index}",
            customer_id="cu1",
            session_id="s1",
            to_status=status,
        ))
        assert result["scheduled"] is True
        assert result["workflow_kind"] == "post_delivery_feedback"


def test_evaluate_order_event_skips_when_customer_opted_out():
    db = _FakeDb(engagement={("c1", "cu1"): {"opted_out": True, "followup_count": 0}})
    result = asyncio.run(scheduler_module.evaluate_order_event(
        db, company_id="c1", order_id="o1", customer_id="cu1", session_id="s1",
        to_status="delivered",
    ))
    assert result["scheduled"] is False
    assert result["reason"] == "opted_out"
    assert not db.followups


def test_evaluate_order_event_is_idempotent():
    db = _FakeDb()
    first = asyncio.run(scheduler_module.evaluate_order_event(
        db, company_id="c1", order_id="o1", customer_id="cu1", session_id="s1",
        to_status="delivered",
    ))
    second = asyncio.run(scheduler_module.evaluate_order_event(
        db, company_id="c1", order_id="o1", customer_id="cu1", session_id="s1",
        to_status="delivered",
    ))
    assert first["scheduled"] is True
    assert second["scheduled"] is False
    assert second["reason"] == "idempotent_replay"
    assert len(db.followups) == 1


def test_claim_due_followup_transitions_scheduled_to_running():
    db = _FakeDb()
    asyncio.run(scheduler_module.evaluate_order_event(
        db, company_id="c1", order_id="o1", customer_id="cu1", session_id="s1",
        to_status="delivered",
    ))
    claimed = asyncio.run(scheduler_module.claim_due_followup(db))
    assert claimed is not None
    assert claimed["order_id"] == "o1"
    assert db.followups[0]["status"] == "running"
    # A second claim returns nothing (the row is already running).
    assert asyncio.run(scheduler_module.claim_due_followup(db)) is None


def test_mark_followup_outcome_writes_status_and_outcome():
    db = _FakeDb()
    asyncio.run(scheduler_module.evaluate_order_event(
        db, company_id="c1", order_id="o1", customer_id="cu1", session_id="s1",
        to_status="delivered",
    ))
    fid = db.followups[0]["id"]
    asyncio.run(scheduler_module.mark_followup_outcome(
        db, followup_id=fid, status="completed", outcome="positive",
    ))
    assert db.followups[0]["status"] == "completed"
    assert db.followups[0]["outcome"] == "positive"


# ---------------------------------------------------------------------------
# on_customer_reply
# ---------------------------------------------------------------------------


def _seeded_db_with_running_followup(workflow_kind: str = "post_delivery_feedback") -> _FakeDb:
    db = _FakeDb()
    asyncio.run(scheduler_module.evaluate_order_event(
        db, company_id="c1", order_id="o1", customer_id="cu1", session_id="s1",
        to_status="delivered",
    ))
    db.followups[0]["status"] = "running"
    db.followups[0]["workflow_kind"] = workflow_kind
    return db


def test_on_customer_reply_disengagement_flips_engagement_and_marks_declined():
    db = _seeded_db_with_running_followup()
    outcome = asyncio.run(on_customer_reply(
        db,
        followup={
            "id": db.followups[0]["id"],
            "company_id": "c1",
            "order_id": "o1",
            "customer_id": "cu1",
            "session_id": "s1",
            "workflow_kind": "post_delivery_feedback",
        },
        text="please stop messaging me",
    ))
    assert outcome["action"] == "opt_out_acknowledged"
    assert outcome["schedule_upsell"] is False
    assert db.engagement[("c1", "cu1")]["opted_out"] is True
    assert db.followups[0]["status"] == "customer_declined"
    assert db.feedback[0]["sentiment"] == "opted_out"


def test_on_customer_reply_positive_feedback_schedules_upsell():
    db = _seeded_db_with_running_followup()
    outcome = asyncio.run(on_customer_reply(
        db,
        followup={
            "id": db.followups[0]["id"],
            "company_id": "c1",
            "order_id": "o1",
            "customer_id": "cu1",
            "session_id": "s1",
            "workflow_kind": "post_delivery_feedback",
        },
        text="loved the product, thanks!",
    ))
    assert outcome["sentiment"] == "positive"
    assert outcome["schedule_upsell"] is True
    assert outcome["upsell"]["scheduled"] is True
    # Two rows now in ai_followups: the original (completed) + a new upsell.
    assert len(db.followups) == 2
    upsell = [row for row in db.followups if row["workflow_kind"] == "upsell"]
    assert len(upsell) == 1


def test_on_customer_reply_negative_feedback_does_not_schedule_upsell():
    db = _seeded_db_with_running_followup()
    outcome = asyncio.run(on_customer_reply(
        db,
        followup={
            "id": db.followups[0]["id"],
            "company_id": "c1",
            "order_id": "o1",
            "customer_id": "cu1",
            "session_id": "s1",
            "workflow_kind": "post_delivery_feedback",
        },
        text="the product is broken, I want a refund",
    ))
    assert outcome["sentiment"] == "negative"
    assert outcome["schedule_upsell"] is False
    assert outcome.get("upsell") is None
    assert len(db.followups) == 1


def test_on_customer_reply_upsell_workflow_never_schedules_a_second_upsell():
    db = _seeded_db_with_running_followup(workflow_kind="upsell")
    outcome = asyncio.run(on_customer_reply(
        db,
        followup={
            "id": db.followups[0]["id"],
            "company_id": "c1",
            "order_id": "o1",
            "customer_id": "cu1",
            "session_id": "s1",
            "workflow_kind": "upsell",
        },
        text="love it!",
    ))
    # Sentiment is positive but the parent was already an upsell — no second
    # upsell should be scheduled (otherwise we'd loop indefinitely).
    assert outcome["schedule_upsell"] is False
