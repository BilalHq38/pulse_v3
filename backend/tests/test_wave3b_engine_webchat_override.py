"""Wave 3b — web-chat conversation-engine override.

Covers the two integration points the override touches:
1. The support agent honours `suppress_response_generation` and returns a
   metadata-only payload (no live LLM call) when set.
2. The webhook helper that synthesises a support_plan from the engine result
   preserves the legacy sentiment-gate escalation decision.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from agent_orchestrator.agents.support_agent import SupportAgent
from agent_orchestrator.schemas import AgentName, WorkflowKind
from services.conversation_engine.schemas import ProductLink
from services.conversation_engine_webchat import (
    apply_engine_response_to_support_plan,
    company_uses_conversation_engine,
)


@dataclass
class _FakeAgentOutputs:
    capture: dict
    qualification: dict


@dataclass
class _FakeContext:
    workflow_kind: WorkflowKind
    request: object
    agent_outputs: _FakeAgentOutputs
    workflow_id: str = "wf-1"
    db: object = None


class _FakeRequest:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_support_agent_skips_response_when_suppression_flag_is_set():
    agent = SupportAgent()
    request = _FakeRequest(
        suppress_response_generation=True,
        message_id="m1",
        message_text="anything",
        company_id="co1",
        conversation_id="c1",
        customer_id="cu1",
    )
    context = _FakeContext(
        workflow_kind=WorkflowKind.MESSAGE,
        request=request,
        agent_outputs=_FakeAgentOutputs(capture={}, qualification={}),
    )
    result = asyncio.run(agent.execute(context))
    assert result.agent_name == AgentName.SUPPORT
    assert result.status == "skipped"
    assert result.payload["response"] == ""
    assert result.payload["deliver_response"] is False
    assert result.payload["next_action"] == "conversation_engine_override"


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
    assert merged["product_links"] == [{"product_id": "p1", "url": "https://example.com/p1"}]


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
    # Without a sentiment_gate, ai_response_allowed is None — the helper should
    # default to "deliver" (only an explicit False blocks delivery).
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
    # Existing support_plan fields survive the merge.
    assert merged["existing_field"] == "kept"


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
