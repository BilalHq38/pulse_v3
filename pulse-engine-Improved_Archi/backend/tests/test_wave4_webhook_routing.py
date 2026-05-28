"""Wave 4 — webhook glue + order_service hook tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from services.followup_scheduler.webhook_glue import maybe_handle_followup_reply


@dataclass
class _FakeDb:
    rows: list[dict] = field(default_factory=list)
    executed: list[tuple] = field(default_factory=list)
    raise_lookup: bool = False

    async def fetchrow(self, sql, *args):
        if self.raise_lookup and "FROM ai_followups" in sql and "status = 'running'" in sql:
            raise RuntimeError("db down")
        if "FROM ai_followups" in sql and "status = 'running'" in sql:
            company_id, session_id = args
            for row in self.rows:
                if (
                    row.get("company_id") == company_id
                    and row.get("session_id") == session_id
                    and row.get("status") == "running"
                ):
                    return row
            return None
        # mark_followup_outcome doesn't read; on_customer_reply doesn't fetch either.
        return None

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        if "UPDATE ai_followups" in sql and "SET status = $1" in sql:
            (status, _outcome, _notes, followup_id) = args
            for row in self.rows:
                if row.get("id") == followup_id:
                    row["status"] = status


def _running_followup_row():
    return {
        "id": "fu1",
        "company_id": "c1",
        "order_id": "o1",
        "customer_id": "cu1",
        "session_id": "s1",
        "workflow_kind": "post_delivery_feedback",
        "status": "running",
    }


def test_glue_returns_none_when_no_running_followup_matches():
    db = _FakeDb(rows=[])
    out = asyncio.run(maybe_handle_followup_reply(
        db, company_id="c1", session_id="s1", text="hello",
    ))
    assert out is None


def test_glue_returns_none_when_db_lookup_raises():
    db = _FakeDb(raise_lookup=True)
    out = asyncio.run(maybe_handle_followup_reply(
        db, company_id="c1", session_id="s1", text="hello",
    ))
    assert out is None


def test_glue_routes_disengagement_reply_to_opt_out_path():
    db = _FakeDb(rows=[_running_followup_row()])
    out = asyncio.run(maybe_handle_followup_reply(
        db, company_id="c1", session_id="s1", text="please stop messaging me",
    ))
    assert out is not None
    assert out["action"] == "opt_out_acknowledged"
    assert out["ack_message"] == "Understood — we won't reach out again."
    assert out["followup_id"] == "fu1"


def test_glue_routes_positive_reply_to_feedback_path():
    db = _FakeDb(rows=[_running_followup_row()])
    out = asyncio.run(maybe_handle_followup_reply(
        db, company_id="c1", session_id="s1", text="loved it, thanks!",
    ))
    assert out is not None
    assert out["action"] == "feedback_recorded"
    assert out["sentiment"] == "positive"
    assert out["schedule_upsell"] is True
    assert out["ack_message"] == "Thanks — your feedback has been recorded."


def test_glue_returns_none_for_empty_session_id():
    db = _FakeDb(rows=[_running_followup_row()])
    out = asyncio.run(maybe_handle_followup_reply(
        db, company_id="c1", session_id="", text="hello",
    ))
    assert out is None


# ---------------------------------------------------------------------------
# order_service lifecycle hook — verify update_order_status records lifecycle
# events and dispatches the follow-up evaluation when the transition is new.
# ---------------------------------------------------------------------------


@dataclass
class _OrderServiceFakeDb:
    """In-memory stand-in for the order_service's DB interactions."""
    orders: dict[str, dict] = field(default_factory=dict)
    lifecycle_events: list[dict] = field(default_factory=list)
    followup_evaluations: list[dict] = field(default_factory=list)
    executed: list[tuple] = field(default_factory=list)

    async def fetchrow(self, sql, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("SELECT status FROM orders"):
            (company_id, order_id) = args
            order = self.orders.get(order_id)
            return {"status": order["status"]} if order else None
        if sql_norm.startswith("UPDATE orders SET status="):
            (status, company_id, order_id) = args
            if order_id not in self.orders:
                return None
            self.orders[order_id]["status"] = status
            return dict(self.orders[order_id])
        if sql_norm.startswith("INSERT INTO order_lifecycle_events") and "ON CONFLICT" in sql_norm:
            (lid, company_id, order_id, from_status, to_status, actor_type, actor_id) = args
            # Idempotent (order_id, to_status, actor_id) — skip duplicates.
            key = (order_id, to_status, actor_id)
            if any((e["order_id"], e["to_status"], e["actor_id"]) == key for e in self.lifecycle_events):
                return None
            event = {
                "id": lid, "company_id": company_id, "order_id": order_id,
                "from_status": from_status, "to_status": to_status,
                "actor_type": actor_type, "actor_id": actor_id,
            }
            self.lifecycle_events.append(event)
            return {"id": lid}
        return None

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


@pytest.mark.asyncio
async def test_update_order_status_records_lifecycle_event(monkeypatch):
    from services import order_service

    db = _OrderServiceFakeDb(
        orders={"o1": {
            "id": "o1", "company_id": "c1", "status": "confirmed",
            "customer_id": "cu1", "conversation_id": "s1",
        }},
    )

    captured: list[dict] = []

    async def fake_dispatch(_db, **kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(order_service, "_maybe_enqueue_followup_evaluation", fake_dispatch)

    await order_service.update_order_status(
        db, company_id="c1", order_id="o1", status="delivered", actor_user_id="u_admin",
    )
    assert len(db.lifecycle_events) == 1
    assert db.lifecycle_events[0]["to_status"] == "delivered"
    assert db.lifecycle_events[0]["from_status"] == "confirmed"
    # Follow-up evaluation should have been dispatched exactly once.
    assert len(captured) == 1
    assert captured[0]["to_status"] == "delivered"
    assert captured[0]["customer_id"] == "cu1"


@pytest.mark.asyncio
async def test_update_order_status_is_idempotent_on_lifecycle_replay(monkeypatch):
    from services import order_service

    db = _OrderServiceFakeDb(
        orders={"o1": {
            "id": "o1", "company_id": "c1", "status": "confirmed",
            "customer_id": "cu1", "conversation_id": "s1",
        }},
    )

    dispatch_calls: list[dict] = []

    async def fake_dispatch(_db, **kwargs):
        dispatch_calls.append(kwargs)

    monkeypatch.setattr(order_service, "_maybe_enqueue_followup_evaluation", fake_dispatch)

    # First transition: a fresh lifecycle row is recorded and the follow-up
    # evaluation is dispatched.
    await order_service.update_order_status(
        db, company_id="c1", order_id="o1", status="delivered", actor_user_id="u_admin",
    )
    # Replay the same transition (simulates a webhook retry). The lifecycle
    # row must not double-insert and the follow-up dispatch must not fire
    # again.
    await order_service.update_order_status(
        db, company_id="c1", order_id="o1", status="delivered", actor_user_id="u_admin",
    )
    assert len(db.lifecycle_events) == 1
    assert len(dispatch_calls) == 1


@pytest.mark.asyncio
async def test_update_order_status_rejects_unknown_status(monkeypatch):
    from services import order_service

    db = _OrderServiceFakeDb(orders={"o1": {
        "id": "o1", "company_id": "c1", "status": "confirmed",
        "customer_id": "cu1", "conversation_id": "s1",
    }})

    with pytest.raises(ValueError):
        await order_service.update_order_status(
            db, company_id="c1", order_id="o1", status="floating",
        )
    assert not db.lifecycle_events


@pytest.mark.asyncio
async def test_update_order_status_to_shipped_emits_lifecycle_without_followup(monkeypatch):
    """shipped is a manage status (Wave 4 expansion) but doesn't fire a
    follow-up — only 'delivered' does. The lifecycle row should still land."""
    from services import order_service

    db = _OrderServiceFakeDb(
        orders={"o1": {
            "id": "o1", "company_id": "c1", "status": "confirmed",
            "customer_id": "cu1", "conversation_id": "s1",
        }},
    )

    dispatch_calls: list[dict] = []

    async def fake_dispatch(_db, **kwargs):
        dispatch_calls.append(kwargs)

    monkeypatch.setattr(order_service, "_maybe_enqueue_followup_evaluation", fake_dispatch)

    await order_service.update_order_status(
        db, company_id="c1", order_id="o1", status="shipped", actor_user_id="u_admin",
    )
    assert len(db.lifecycle_events) == 1
    assert db.lifecycle_events[0]["to_status"] == "shipped"
    # The dispatcher IS called (it's the dispatcher's job to no-op on non-delivered),
    # but evaluate_order_event downstream will see to_status="shipped" and skip.
    assert len(dispatch_calls) == 1
    assert dispatch_calls[0]["to_status"] == "shipped"
