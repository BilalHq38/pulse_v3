"""Wave 3b - web-chat conversation-engine override helpers."""

from __future__ import annotations

import asyncio

from agent_orchestrator.schemas import (
    AgentName,
    GlobalMemory,
    MessageWorkflowRequest,
    WorkflowKind,
    WorkflowOutputs,
    WorkflowRouteDecision,
)
from agent_orchestrator.workflows.workflow_manager import WorkflowManager, WorkflowRuntimeContext
from services.conversation_engine.schemas import ProductLink
from services.conversation_engine_webchat import (
    apply_engine_response_to_support_plan,
    company_uses_conversation_engine,
)


class _MissingRegistry:
    def get(self, agent_name):
        raise KeyError(agent_name)


class _FakeMemoryStore:
    def __init__(self):
        self.agent_memory = []
        self.global_memory_saved = False

    async def save_agent_memory(self, **kwargs):
        self.agent_memory.append(kwargs)

    async def save_global_memory(self, global_memory):
        self.global_memory_saved = True


class _FakeStateStore:
    def __init__(self):
        self.execution = None
        self.transition = None
        self.updated = None

    async def append_execution(self, **kwargs):
        self.execution = kwargs["result"]

    async def update_after_agent(self, **kwargs):
        self.updated = kwargs

    async def append_transition(self, **kwargs):
        self.transition = kwargs

    async def get_record(self, workflow_id):
        return {"id": workflow_id, "status": "running"}

    async def build_response(self, **kwargs):
        return kwargs


class _FakeRouter:
    def next_agent(self, context, previous_agent):
        return WorkflowRouteDecision(
            current_agent=previous_agent.value,
            next_agent="",
            decision_mode="rule_based",
            reason="test completed",
        )


def test_missing_agent_skip_is_logged_and_persisted(caplog):
    state_store = _FakeStateStore()
    memory_store = _FakeMemoryStore()
    router = _FakeRouter()
    registry = _MissingRegistry()
    manager = WorkflowManager(
        db=None,
        state_store=state_store,
        memory_store=memory_store,
        registry=registry,
        router=router,
    )
    context = WorkflowRuntimeContext(
        db=None,
        state_store=state_store,
        memory_store=memory_store,
        registry=registry,
        router=router,
        workflow_id="wf-1",
        trace_id="trace-1",
        company_id="co-1",
        workflow_kind=WorkflowKind.MESSAGE,
        request=MessageWorkflowRequest(
            company_id="co-1",
            conversation_id="c1",
            sender_contact="customer-1",
            message_text="hello",
        ),
        global_memory=GlobalMemory(company_id="co-1"),
        agent_outputs=WorkflowOutputs(),
    )

    caplog.set_level("WARNING", logger="agent_orchestrator.workflows.workflow_manager")
    asyncio.run(
        manager._execute_agent_by_name(
            context,
            AgentName.SUPPORT,
            route=WorkflowRouteDecision(current_agent="qualification", next_agent="support", reason="legacy route"),
        )
    )

    assert "agent_not_registered_skip" in caplog.text
    assert state_store.execution.agent_name == AgentName.SUPPORT
    assert state_store.execution.status == "skipped"
    assert state_store.execution.warnings == ["agent_not_registered"]
    assert memory_store.agent_memory[0]["agent_name"] == "support"
    assert context.agent_outputs.support == {}


def test_engine_override_helper_delivers_when_sentiment_gate_allows():
    support_plan = {"escalate": False, "deliver_response": False}
    capture = {"sentiment_gate": {"ai_response_allowed": True}}
    merged = apply_engine_response_to_support_plan(
        support_plan=support_plan,
        capture=capture,
        engine_answer="Our Pulse Engine starter plan is 99 USD.",
        engine_confidence=0.82,
        engine_turn_id="t_abc",
        engine_product_links=[ProductLink(product_id="p1", url="https://example.com/p1")],
    )
    assert merged["deliver_response"] is True
    assert merged["escalate"] is False
    assert merged["confidence"] == 0.82
    assert merged["next_action"] == "conversation_engine_reply"
    assert merged["engine_turn_id"] == "t_abc"
    assert merged["product_links"] == [
        {"product_id": "p1", "url": "https://example.com/p1", "name": "", "image_url": ""}
    ]


def test_engine_override_helper_escalates_when_sentiment_gate_blocks():
    capture = {
        "sentiment_gate": {
            "ai_response_allowed": False,
            "recommended_action": "escalate_to_human",
        }
    }
    merged = apply_engine_response_to_support_plan(
        support_plan={},
        capture=capture,
        engine_answer="anything",
        engine_confidence=0.7,
        engine_turn_id="t_x",
        engine_product_links=[],
    )
    assert merged["escalate"] is True
    assert merged["deliver_response"] is False
    assert merged["escalation_reason"] == "escalate_to_human"
    assert merged["next_action"] == "manual_review"


def test_engine_override_helper_handles_missing_sentiment_gate():
    merged = apply_engine_response_to_support_plan(
        support_plan={"existing_field": "kept"},
        capture={},
        engine_answer="hello",
        engine_confidence=0.5,
        engine_turn_id="t_y",
        engine_product_links=[],
    )
    assert merged["deliver_response"] is True
    assert merged["escalate"] is False
    assert merged["existing_field"] == "kept"


def test_engine_override_helper_uses_engine_sentiment_when_capture_empty():
    merged = apply_engine_response_to_support_plan(
        support_plan={},
        capture={},
        user_message="I am very angry",
        engine_answer="I can help with that.",
        engine_confidence=0.8,
        engine_turn_id="t_angry",
        engine_product_links=[],
        engine_sentiment={"score": -0.72, "sentiment_label": "negative", "source": "local_heuristic"},
        engine_escalation_required=True,
    )

    assert merged["escalate"] is True
    assert merged["deliver_response"] is False
    assert merged["sentiment_gate"]["classification"] == "Negative"
    assert merged["next_action"] == "manual_review"


def test_engine_override_helper_preserves_active_order_flow_response():
    support_plan = {
        "response": "Please confirm your order.",
        "deliver_response": True,
        "escalate": False,
        "conversation_stage": "order_flow",
        "model_name": "deterministic-order-flow",
        "next_action": "request_order_confirmation",
        "order_id": "order-1",
    }

    merged = apply_engine_response_to_support_plan(
        support_plan=support_plan,
        capture={"sentiment_gate": {"ai_response_allowed": True}},
        engine_answer="Here is a general product answer.",
        engine_confidence=0.91,
        engine_turn_id="turn-1",
        engine_product_links=[ProductLink(product_id="p1", url="https://example.com/p1")],
    )

    assert merged["response"] == "Please confirm your order."
    assert merged["next_action"] == "request_order_confirmation"
    assert merged["engine_override_skipped_reason"] == "active_order_flow"
    assert merged["engine_turn_id"] == "turn-1"
    assert "product_links" not in merged


def test_engine_override_helper_uses_engine_for_collect_details_buy_link():
    support_plan = {
        "response": "Please share your delivery address to complete the order.",
        "deliver_response": True,
        "escalate": False,
        "conversation_stage": "order_flow",
        "model_name": "deterministic-order-flow",
        "next_action": "collect_order_details",
        "order_id": "order-1",
    }

    merged = apply_engine_response_to_support_plan(
        support_plan=support_plan,
        capture={"sentiment_gate": {"ai_response_allowed": True}},
        engine_answer="You can buy Aquamarine Drop Earrings here:\nhttps://example.com/aqua",
        engine_confidence=0.91,
        engine_turn_id="turn-1",
        engine_product_links=[
            ProductLink(product_id="p1", url="https://example.com/aqua", name="Aquamarine Drop Earrings")
        ],
    )

    assert merged["response"].startswith("You can buy Aquamarine Drop Earrings here:")
    assert merged["next_action"] == "conversation_engine_reply"
    assert merged["engine_turn_id"] == "turn-1"
    assert merged["product_links"] == [
        {
            "product_id": "p1",
            "url": "https://example.com/aqua",
            "name": "Aquamarine Drop Earrings",
            "image_url": "",
        }
    ]


class _FakeSettingsDb:
    def __init__(self, *, raise_lookup: bool = False, value):
        self._raise = raise_lookup
        self._value = value

    async def fetchrow(self, sql, *args):
        if self._raise:
            raise RuntimeError("db down")
        return {"v": self._value}


def test_company_uses_conversation_engine_returns_true_when_setting_is_on():
    db = _FakeSettingsDb(value=True)
    assert asyncio.run(company_uses_conversation_engine(db, "co1")) is True


def test_company_uses_conversation_engine_returns_false_when_setting_is_off():
    db = _FakeSettingsDb(value=False)
    assert asyncio.run(company_uses_conversation_engine(db, "co1")) is False


def test_company_uses_conversation_engine_returns_false_on_db_error():
    db = _FakeSettingsDb(raise_lookup=True, value=True)
    assert asyncio.run(company_uses_conversation_engine(db, "co1")) is False


def test_company_uses_conversation_engine_returns_false_for_empty_company_id():
    db = _FakeSettingsDb(value=True)
    assert asyncio.run(company_uses_conversation_engine(db, "")) is False
