import json

import pytest

from services.lead_stage_service import (
    apply_message_stage_transition,
    get_lead_stage_history,
    transition_lead_stage,
)


class FakeLeadStageDb:
    def __init__(self):
        self.leads = {
            "lead-1": {
                "id": "lead-1",
                "company_id": "company-1",
                "status": "new",
                "status_id": "",
                "name": "Avery",
                "email": "avery@example.com",
                "phone": "+14155550188",
            }
        }
        self.customers = {
            "customer-1": {
                "id": "customer-1",
                "company_id": "company-1",
                "lead_id": "lead-1",
                "email": "avery@example.com",
                "phone": "+14155550188",
            }
        }
        self.activities = []

    async def fetchrow(self, query, *args):
        if "SELECT * FROM lead_activities" in query:
            lead_id, company_id = args[:2]
            rows = [
                row
                for row in self.activities
                if row["lead_id"] == lead_id and row["company_id"] == company_id and row["type"] == "stage_changed"
            ]
            return rows[-1] if rows else None
        if "SELECT * FROM leads WHERE id=$1 AND company_id=$2" in query:
            lead = self.leads.get(args[0])
            return dict(lead) if lead and lead["company_id"] == args[1] else None
        if "SELECT * FROM customers WHERE id=$1 AND company_id=$2" in query:
            customer = self.customers.get(args[0])
            return dict(customer) if customer and customer["company_id"] == args[1] else None
        if "SELECT id FROM lead_statuses" in query:
            return {"id": f"status-{args[1]}"}
        return None

    async def fetch(self, query, *args):
        if "SELECT * FROM lead_activities" in query:
            lead_id, company_id = args[:2]
            return [
                dict(row)
                for row in reversed(self.activities)
                if row["lead_id"] == lead_id and row["company_id"] == company_id and row["type"] == "stage_changed"
            ]
        return []

    async def execute(self, query, *args):
        if query.startswith("UPDATE leads SET status"):
            stage, status_id, lead_id, company_id = args[:4]
            lead = self.leads[lead_id]
            assert lead["company_id"] == company_id
            lead["status"] = stage
            lead["status_id"] = status_id
            return "UPDATE 1"
        if query.startswith("INSERT INTO lead_activities"):
            activity_id, lead_id, company_id, content, stage = args[:5]
            self.activities.append(
                {
                    "id": activity_id,
                    "lead_id": lead_id,
                    "company_id": company_id,
                    "type": "stage_changed",
                    "content": content,
                    "stage": stage,
                    "created_at": f"t-{len(self.activities)}",
                }
            )
            return "INSERT 0 1"
        return "OK"


@pytest.mark.asyncio
async def test_rule_based_lead_stage_pipeline_moves_forward_and_records_history():
    db = FakeLeadStageDb()

    await apply_message_stage_transition(
        db,
        company_id="company-1",
        lead_id="lead-1",
        message_text="Hi, following up from Pulse Engine.",
        direction="outbound",
        source="message_sent",
        event_id="msg-1",
    )
    assert db.leads["lead-1"]["status"] == "contacted"

    await apply_message_stage_transition(
        db,
        company_id="company-1",
        customer_id="customer-1",
        message_text="I am interested. How much is the price?",
        direction="inbound",
        source="customer_reply",
        event_id="msg-2",
    )
    assert db.leads["lead-1"]["status"] == "qualified"

    await apply_message_stage_transition(
        db,
        company_id="company-1",
        lead_id="lead-1",
        message_text="Here is the proposal and quotation for your package.",
        direction="outbound",
        source="proposal_sent",
        event_id="msg-3",
    )
    assert db.leads["lead-1"]["status"] == "proposal"

    await apply_message_stage_transition(
        db,
        company_id="company-1",
        lead_id="lead-1",
        message_text="Can you reduce the final price or offer a discount?",
        direction="inbound",
        source="customer_reply",
        event_id="msg-4",
    )
    assert db.leads["lead-1"]["status"] == "negotiation"

    await transition_lead_stage(
        db,
        db.leads["lead-1"],
        "converted",
        reason="Converted manually by user",
        source="conversion",
        confidence=1.0,
        event_id="conversion:lead-1",
    )
    assert db.leads["lead-1"]["status"] == "converted"

    await apply_message_stage_transition(
        db,
        company_id="company-1",
        lead_id="lead-1",
        message_text="Payment done and confirmed.",
        direction="inbound",
        source="customer_reply",
        event_id="msg-5",
    )
    assert db.leads["lead-1"]["status"] == "won"

    history = await get_lead_stage_history(db, "lead-1", "company-1")
    assert history[0]["new_stage"] == "won"
    assert any(item["new_stage"] == "contacted" for item in history)
    assert json.loads(db.activities[-1]["content"])["reason"] == "Successful close or payment confirmation"


@pytest.mark.asyncio
async def test_manual_override_blocks_weak_automatic_signal():
    db = FakeLeadStageDb()

    await transition_lead_stage(
        db,
        db.leads["lead-1"],
        "contacted",
        reason="Human set contacted",
        source="manual_update",
        confidence=1.0,
        changed_by_user_id="user-1",
        automatic=False,
    )

    result = await apply_message_stage_transition(
        db,
        company_id="company-1",
        lead_id="lead-1",
        message_text="I am interested in details.",
        direction="inbound",
        source="customer_reply",
        event_id="msg-weak",
    )

    assert result["changed"] is False
    assert result["reason"] == "manual_override_respected"
    assert db.leads["lead-1"]["status"] == "contacted"


@pytest.mark.asyncio
async def test_locked_terminal_stage_is_not_overwritten_by_normal_message():
    db = FakeLeadStageDb()
    db.leads["lead-1"]["status"] = "lost"

    result = await apply_message_stage_transition(
        db,
        company_id="company-1",
        lead_id="lead-1",
        message_text="Actually I am interested in price details.",
        direction="inbound",
        source="customer_reply",
        event_id="msg-after-lost",
    )

    assert result["changed"] is False
    assert result["reason"] == "terminal_stage_locked"
    assert db.leads["lead-1"]["status"] == "lost"
