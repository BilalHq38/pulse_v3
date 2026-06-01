import asyncio
import logging
from dataclasses import dataclass, field

import pytest

from services.conversation_engine.schemas import ProductLink, TokenUsage, TurnResult
from services.followup_scheduler import loop as loop_module
from services.followup_scheduler import scheduler as scheduler_module
from services import order_service


@dataclass
class _PostConversationDb:
    followups: list[dict] = field(default_factory=list)
    conversations: dict[str, dict] = field(default_factory=dict)
    messages: list[dict] = field(default_factory=list)
    automation_log: list[dict] = field(default_factory=list)
    engagement: dict[tuple[str, str], dict] = field(default_factory=dict)
    executed: list[tuple] = field(default_factory=list)

    async def fetchrow(self, sql, *args):
        sql_norm = " ".join(sql.split())
        if "FROM customer_engagement" in sql_norm:
            company_id, customer_id = args
            return self.engagement.get((company_id, customer_id))
        if "INSERT INTO ai_followups" in sql_norm and "ON CONFLICT (idempotency_key)" in sql_norm:
            fid, company_id, order_id, customer_id, session_id, workflow_kind, delay, idem = args
            if any(row.get("idempotency_key") == idem for row in self.followups):
                return None
            row = {
                "id": fid,
                "company_id": company_id,
                "order_id": order_id,
                "customer_id": customer_id,
                "session_id": session_id,
                "workflow_kind": workflow_kind,
                "status": "scheduled",
                "scheduled_for": delay,
                "idempotency_key": idem,
            }
            self.followups.append(row)
            return {"id": fid}
        if sql_norm.startswith("UPDATE ai_followups") and "status = 'running'" in sql_norm:
            for row in self.followups:
                if row.get("status") == "scheduled":
                    row["status"] = "running"
                    return {
                        "id": row["id"],
                        "company_id": row["company_id"],
                        "order_id": row["order_id"],
                        "customer_id": row["customer_id"],
                        "session_id": row["session_id"],
                        "workflow_kind": row["workflow_kind"],
                    }
            return None
        if "FROM conversations" in sql_norm and "session_id" in sql_norm:
            company_id, session_id = args
            for convo in self.conversations.values():
                if convo.get("company_id") == company_id and convo.get("session_id") == session_id:
                    return dict(convo)
            return None
        return None

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("INSERT INTO messages"):
            mid, company_id, conversation_id, content, confidence = args
            self.messages.append({
                "id": mid,
                "company_id": company_id,
                "conversation_id": conversation_id,
                "content": content,
                "confidence": confidence,
            })
        elif sql_norm.startswith("INSERT INTO automation_log"):
            lid, company_id, workflow_kind, followup_id, order_id, customer_id, outcome, duration_ms = args
            self.automation_log.append({
                "id": lid,
                "company_id": company_id,
                "workflow_kind": workflow_kind,
                "followup_id": followup_id,
                "order_id": order_id,
                "customer_id": customer_id,
                "outcome": outcome,
                "duration_ms": duration_ms,
            })
        elif sql_norm.startswith("UPDATE ai_followups") and "SET status = $1" in sql_norm:
            status, outcome, notes, followup_id = args
            for row in self.followups:
                if row.get("id") == followup_id:
                    row.update({"status": status, "outcome": outcome, "outcome_notes": notes})


@dataclass
class _OrderAliasDb:
    orders: dict[str, dict] = field(default_factory=dict)
    lifecycle_events: list[dict] = field(default_factory=list)

    async def fetchrow(self, sql, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("SELECT status FROM orders"):
            company_id, order_id = args
            order = self.orders.get(order_id)
            return {"status": order["status"]} if order and order.get("company_id") == company_id else None
        if sql_norm.startswith("UPDATE orders SET status="):
            status, company_id, order_id = args
            order = self.orders.get(order_id)
            if not order or order.get("company_id") != company_id:
                return None
            order["status"] = status
            return dict(order)
        if sql_norm.startswith("INSERT INTO order_lifecycle_events"):
            lid, company_id, order_id, from_status, to_status, actor_type, actor_id = args
            self.lifecycle_events.append({
                "id": lid,
                "company_id": company_id,
                "order_id": order_id,
                "from_status": from_status,
                "to_status": to_status,
                "actor_type": actor_type,
                "actor_id": actor_id,
            })
            return {"id": lid}
        return None


def _feedback_result() -> TurnResult:
    return TurnResult(
        answer=(
            "How was your experience with your order, and are you satisfied with the product? "
            "A Phone Case pairs well with your recent purchase if you want extra protection."
        ),
        session_id="session-1",
        turn_id="turn-1",
        sources_used=["product"],
        product_links=[ProductLink(product_id="related-1", url="https://example.test/phone-case", name="Phone Case")],
        tokens_used=TokenUsage(prompt=10, completion=10, total=20),
        active_template="",
        confidence=0.9,
    )


@pytest.mark.parametrize("trigger_status", ["order completed", "delivery completed", "delivered"])
def test_post_conversation_triggers_schedule_dispatch_and_verify_content(monkeypatch, caplog, trigger_status):
    asyncio.run(_run_post_conversation_trigger_case(monkeypatch, caplog, trigger_status))


@pytest.mark.parametrize("trigger_status", ["admin_review", "placed", "confirmed"])
def test_order_placed_status_schedules_order_confirmed_followup(trigger_status):
    asyncio.run(_run_order_placed_status_case(trigger_status))


async def _run_order_placed_status_case(trigger_status: str):
    db = _PostConversationDb()

    scheduled = await scheduler_module.evaluate_order_event(
        db,
        company_id="company-1",
        order_id=f"order-{trigger_status}",
        customer_id="customer-1",
        session_id="session-1",
        to_status=trigger_status,
    )

    assert scheduled["scheduled"] is True
    assert scheduled["workflow_kind"] == "order_confirmed"
    assert db.followups[0]["workflow_kind"] == "order_confirmed"


async def _run_post_conversation_trigger_case(monkeypatch, caplog, trigger_status: str):
    db = _PostConversationDb(
        conversations={
            "conversation-1": {
                "id": "conversation-1",
                "company_id": "company-1",
                "customer_id": "customer-1",
                "session_id": "session-1",
                "channel": "web_chat",
                "channel_id": "session-1",
            }
        }
    )

    async def fake_turn(_db, **kwargs):
        assert kwargs["workflow_kind"] == "post_delivery_feedback"
        assert kwargs["order_id"].startswith("order-")
        return _feedback_result()

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(loop_module, "run_proactive_turn", fake_turn)
    monkeypatch.setattr(loop_module, "_emit_socket_message", noop)
    monkeypatch.setattr(loop_module, "_dispatch_via_channel", noop)
    caplog.set_level(logging.INFO)

    order_id = f"order-{trigger_status.replace(' ', '-')}"
    scheduled = await scheduler_module.evaluate_order_event(
        db,
        company_id="company-1",
        order_id=order_id,
        customer_id="customer-1",
        session_id="session-1",
        to_status=trigger_status,
    )
    assert scheduled["scheduled"] is True
    assert scheduled["workflow_kind"] == "post_delivery_feedback"

    claimed = await scheduler_module.claim_due_followup(db)
    assert claimed is not None
    outcome = await loop_module.dispatch_one_followup(db, claimed)
    assert outcome["dispatched"] is True

    assert len(db.messages) == 1
    message = db.messages[0]["content"]
    assert "experience" in message.lower()
    assert "satisfied" in message.lower()
    assert "Phone Case" in message
    assert db.automation_log[0]["outcome"] == "dispatched"
    assert "post_conversation_trigger_evaluated" in caplog.text
    assert "trigger_result=pass" in caplog.text
    assert "post_conversation_message_content_check" in caplog.text
    assert "experience_check=pass" in caplog.text
    assert "satisfaction_check=pass" in caplog.text
    assert "related_product_check=pass" in caplog.text


@pytest.mark.parametrize(
    ("raw_status", "canonical_status"),
    [
        ("order completed", "completed"),
        ("order_completed", "completed"),
        ("delivery completed", "delivered"),
        ("delivery_completed", "delivered"),
        ("delivered", "delivered"),
    ],
)
def test_order_status_event_aliases_emit_followup_evaluation(monkeypatch, raw_status, canonical_status):
    asyncio.run(_run_order_status_alias_case(monkeypatch, raw_status, canonical_status))


async def _run_order_status_alias_case(monkeypatch, raw_status: str, canonical_status: str):
    db = _OrderAliasDb(
        orders={
            "order-1": {
                "id": "order-1",
                "company_id": "company-1",
                "status": "confirmed",
                "customer_id": "customer-1",
                "conversation_id": "session-1",
            }
        }
    )
    captured: list[dict] = []

    async def fake_enqueue(_db, **kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(order_service, "_maybe_enqueue_followup_evaluation", fake_enqueue)

    updated = await order_service.update_order_status(
        db,
        company_id="company-1",
        order_id="order-1",
        status=raw_status,
        actor_user_id="admin-1",
    )
    assert updated["status"] == canonical_status
    assert db.lifecycle_events[0]["to_status"] == canonical_status
    assert captured[0]["to_status"] == canonical_status
