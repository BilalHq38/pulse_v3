import pytest

from agent_orchestrator.agents.capture_agent import _adaptive_short_circuit_decision
from agent_orchestrator.agents.support_agent import _deterministic_budget_fallback
from agent_orchestrator.schemas import MessageWorkflowRequest
from agent_orchestrator.workflows.workflow_manager import WorkflowManager
from services.ai_service.llm_tracking import (
    get_ai_usage_snapshot,
    reset_llm_context,
    reserve_embedding_call,
    set_llm_context,
)


def _manager():
    return WorkflowManager(db=None, state_store=None, memory_store=None, registry=None, router=None)


def test_same_text_with_different_provider_timestamps_gets_distinct_fallback_ids():
    first = MessageWorkflowRequest(
        company_id="co",
        conversation_id="c1",
        sender_contact="u1",
        message_text="hi",
        metadata={"timestamp": "2026-04-30T10:00:00Z"},
    )
    second = MessageWorkflowRequest(
        company_id="co",
        conversation_id="c1",
        sender_contact="u1",
        message_text="hi",
        metadata={"timestamp": "2026-04-30T10:00:05Z"},
    )

    first_id, first_strength = _manager()._derive_message_id(first)
    second_id, second_strength = _manager()._derive_message_id(second)

    assert first_strength == second_strength == "fallback_timestamp"
    assert first_id != second_id


def test_identical_legacy_messages_without_timestamp_are_unstable_not_deduped():
    request = MessageWorkflowRequest(
        company_id="co",
        conversation_id="c1",
        sender_contact="u1",
        message_text="hi",
    )

    message_id, strength = _manager()._derive_message_id(request)

    assert strength == "unstable"
    assert message_id.startswith("internal:")


def test_provider_external_message_id_is_strong():
    request = MessageWorkflowRequest(
        company_id="co",
        conversation_id="c1",
        sender_contact="u1",
        message_text="hi",
        external_message_id="provider-123",
    )

    message_id, strength = _manager()._derive_message_id(request)

    assert message_id == "provider-123"
    assert strength == "strong"


def test_manual_ai_workflow_request_has_strong_idempotency_material():
    request = MessageWorkflowRequest(
        company_id="co",
        conversation_id="c1",
        customer_id="cust1",
        channel="whatsapp",
        source="manual_ai_respond",
        message_text="manual trigger",
        sender_contact="+923001234567",
        message_id="manual_ai_respond:co:c1:m1",
        external_message_id="wamid.customer-message",
        provider_event_id="wamid.customer-message",
        idempotency_key="manual_ai_respond:co:c1:m1",
        metadata={
            "message_id": "manual_ai_respond:co:c1:m1",
            "external_message_id": "wamid.customer-message",
            "provider_event_id": "wamid.customer-message",
            "idempotency_key": "manual_ai_respond:co:c1:m1",
            "raw_sender_id": "923001234567",
            "normalized_sender_id": "+923001234567",
        },
    )

    message_id, strength = _manager()._derive_message_id(request)

    assert message_id == "manual_ai_respond:co:c1:m1"
    assert strength == "strong"


def test_manual_ai_workflow_request_without_identity_material_is_rejected():
    with pytest.raises(ValueError):
        MessageWorkflowRequest(
            company_id="co",
            conversation_id="c1",
            channel="whatsapp",
            source="manual_ai_respond",
            message_text="manual trigger",
        )


def test_adaptive_short_circuit_allows_clear_qualification_answer():
    used, reason, blocked = _adaptive_short_circuit_decision(
        "Our budget is $5000 and we need this next month",
        {"score": 0.1, "emotion": "neutral"},
        {
            "ready_for_scoring": False,
            "next_question": "What is your budget?",
            "completed_fields": ["budget", "timeline"],
        },
    )

    assert used is True
    assert reason == "adaptive_qualification"
    assert blocked == ""


@pytest.mark.parametrize(
    "text",
    [
        "I need a refund now",
        "What is the price of your product?",
        "I cannot login to my account",
    ],
)
def test_adaptive_short_circuit_blocks_support_and_product_questions(text):
    used, _, blocked = _adaptive_short_circuit_decision(
        text,
        {"score": 0.0, "emotion": "neutral"},
        {
            "ready_for_scoring": False,
            "next_question": "What is your budget?",
            "completed_fields": [],
        },
    )

    assert used is False
    assert blocked


def test_embedding_cache_hits_do_not_need_budget_reservation():
    token = set_llm_context(
        message_id="m1",
        conversation_id="c1",
        workflow_id="w1",
        company_id="co",
        max_calls=1,
        max_embedding_calls=1,
    )
    try:
        reserve_embedding_call(provider="openai", model="text-embedding-3-small")
        snapshot = get_ai_usage_snapshot()
    finally:
        reset_llm_context(token)

    assert snapshot["embedding_call_count"] == 1
    assert snapshot["total_ai_api_call_count"] == 1


def test_support_budget_fallback_is_deliverable():
    result = _deterministic_budget_fallback("I need a refund", {"intent": "refund"}, {"name": "Sam"})

    assert result["provider"] == "deterministic_fallback"
    assert result["llm_budget_exhausted"] is True
    assert result["response"]
