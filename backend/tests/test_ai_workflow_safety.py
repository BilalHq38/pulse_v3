import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from shared.schemas.contracts import CombinedResponse
from agent_orchestrator.agents import capture_agent, support_agent
from agent_orchestrator.schemas import MessageWorkflowRequest
from agent_orchestrator.workflows.workflow_manager import WorkflowManager
from services.ai_service.llm_tracking import (
    get_ai_usage_snapshot,
    reset_llm_context,
    reserve_embedding_call,
    set_llm_context,
)
from services.ai_service import llm_client, response_generator


class _CampaignDraftForTest(BaseModel):
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)
    html_body: str = ""


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


def test_adaptive_keyword_short_circuit_is_removed():
    assert not hasattr(capture_agent, "_adaptive_short_circuit_decision")
    assert not hasattr(capture_agent, "_ADAPTIVE_BLOCK_KEYWORDS")
    assert not hasattr(capture_agent, "_PRODUCT_QUERY_KEYWORDS")


@pytest.mark.asyncio
async def test_lead_score_prompt_uses_safe_lead_context(monkeypatch):
    captured = {}

    async def fake_engine(**_kwargs):
        return {"provider": "test", "model_name": "test"}

    async def fake_call_model_json(prompt, *_args, **_kwargs):
        captured["prompt"] = prompt
        return {
            "score": 55,
            "grade": "warm",
            "reasoning": "evidence",
            "next_action": "Follow up",
            "phase": "awareness",
        }

    monkeypatch.setattr(response_generator, "_resolve_engine_cached", fake_engine)
    monkeypatch.setattr(response_generator, "call_model_json", fake_call_model_json)

    lead = {
        "id": "lead-1",
        "company_id": "company-1",
        "name": "Avery",
        "email": "avery@example.com",
        "phone": "+123",
        "father_name": "Should not pass",
        "address": "123 Main",
        "city": "Town",
        "state": "CA",
        "country": "US",
        "source": "web_chat",
        "source_id": "src-1",
        "status": "new",
        "status_id": "stat-1",
        "assigned_to": "user-1",
        "assigned_name": "Agent",
        "metadata": {"secret": "value"},
        "notes": "Interested in pricing",
        "scoring_reason": "Asked about features",
        "score": 60,
        "phase": "awareness",
        "next_action": "Assign to agent and update CRM",
        "customer_company_name": "Northstar",
        "raw_message": "Last message",
    }

    await response_generator.generate_lead_score(lead, db=None, company_id="company-1")

    payload = json.loads(captured["prompt"].split("\nlead:\n", 1)[1])
    assert payload["name"] == "Avery"
    assert payload["source"] == "web_chat"
    assert payload.get("customer_company_name") == "Northstar"
    assert "next_action" not in payload
    for key in (
        "father_name",
        "address",
        "city",
        "state",
        "country",
        "source_id",
        "status_id",
        "assigned_to",
        "assigned_name",
        "metadata",
        "id",
        "company_id",
    ):
        assert key not in payload


@pytest.mark.asyncio
async def test_nurture_prompt_uses_safe_lead_context(monkeypatch):
    captured = {}

    async def fake_engine(**_kwargs):
        return {"provider": "test", "model_name": "test"}

    async def fake_call_model_text(prompt, *_args, **_kwargs):
        captured["prompt"] = prompt
        return "Hello from nurture"

    monkeypatch.setattr(response_generator, "_resolve_engine_cached", fake_engine)
    monkeypatch.setattr(response_generator, "call_model_text", fake_call_model_text)

    lead = {
        "name": "Avery",
        "source": "web_chat",
        "status": "new",
        "score": 72,
        "notes": "Asked for onboarding details",
        "customer_company_name": "Northstar",
        "next_action": "Schedule a quick call",
        "assigned_to": "user-1",
        "status_id": "stat-1",
        "metadata": {"secret": "value"},
    }

    await response_generator.generate_nurture_message(
        lead,
        "new",
        company_context="We help with CRM setup.",
        db=None,
        company_id="company-1",
    )

    payload = json.loads(captured["prompt"].split("\nlead:\n", 1)[1])
    assert payload["name"] == "Avery"
    assert payload["customer_company_name"] == "Northstar"
    assert payload["next_action"] == "Schedule a quick call"
    for key in ("assigned_to", "status_id", "metadata", "id", "company_id"):
        assert key not in payload


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


def test_support_budget_fallback_is_removed():
    assert not hasattr(support_agent, "_deterministic_budget_fallback")


def test_combined_response_preserves_degraded_metadata():
    payload = CombinedResponse(
        sentiment={"score": 0},
        conversation_sentiment={"score": 0},
        intent={"intent": "general_question"},
        ai_response={},
        ai_response_error="AI_BUDGET_EXCEEDED type=llm",
        llm_budget_exhausted=True,
        ai_response_generated=False,
    )

    assert payload.llm_budget_exhausted is True
    assert payload.ai_response_generated is False


def test_json_extraction_reports_invalid_model_output_without_runtime_error():
    with pytest.raises(ValueError, match="valid JSON object"):
        llm_client._extract_json_object("not json")


def test_json_extraction_handles_common_model_json_wrappers():
    payload = llm_client._extract_json_object(
        """```json
        {subject: "Hello", "body": "World", "html_body": "<p>World</p>",}
        ```"""
    )

    assert payload == {"subject": "Hello", "body": "World", "html_body": "<p>World</p>"}


def test_email_campaign_json_calls_can_use_campaign_token_limit():
    selected = llm_client._json_engine_for_call(
        {"provider": "gemini", "model_name": "gemini-test", "max_tokens": 900},
        call_purpose="email_campaign_copy",
    )

    assert selected["max_tokens"] == 900


def test_email_campaign_json_calls_raise_too_small_engine_token_limit():
    selected = llm_client._json_engine_for_call(
        {"provider": "gemini", "model_name": "gemini-test", "max_tokens": 24},
        call_purpose="email_campaign_copy",
    )

    assert selected["max_tokens"] >= 900


def test_json_generation_config_omits_gemini_response_mime_for_stable_api_model():
    config = llm_client._json_generation_config(
        {"provider": "gemini", "model_name": "gemini-2.5-pro", "max_tokens": 900}
    )

    assert "response_mime_type" not in config


@pytest.mark.asyncio
async def test_call_model_json_retries_malformed_json_response(monkeypatch):
    calls = []

    async def fake_call_model_text(prompt, *_args, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        if len(calls) == 1:
            return '{"subject":"Hi","body":"This response was cut off'
        return '{"subject":"Hi","body":"Complete body","html_body":"<p>Complete body</p>"}'

    monkeypatch.setattr(llm_client, "call_model_text", fake_call_model_text)

    result = await llm_client.call_model_json(
        "Write campaign copy.",
        _CampaignDraftForTest,
        engine={"provider": "gemini", "model_name": "gemini-test", "max_tokens": 24},
        call_purpose="email_campaign_copy",
    )

    assert result == {"subject": "Hi", "body": "Complete body", "html_body": "<p>Complete body</p>"}
    assert len(calls) == 2
    assert "previous model response was invalid" in calls[1]["prompt"].lower()
    assert calls[0]["engine"]["max_tokens"] >= 900
    assert calls[1]["engine"]["max_tokens"] > calls[0]["engine"]["max_tokens"]
    assert calls[0]["generation_config"]["response_format"] == "json_object"


@pytest.mark.asyncio
async def test_gemini_stream_retries_without_response_mime_type_when_api_rejects(monkeypatch):
    if not llm_client.genai_types:
        pytest.skip("google-genai is not installed")

    class Chunk:
        text = '{"subject":"Hi","body":"Body","html_body":"<p>Body</p>"}'

    class FakeModels:
        def __init__(self):
            self.configs = []

        async def generate_content_stream(self, *, model, contents, config):
            self.configs.append(config)
            if len(self.configs) == 1:
                raise RuntimeError(
                    '400 INVALID_ARGUMENT. Invalid JSON payload received. Unknown name "responseMimeType" '
                    "at 'generation_config': Cannot find field."
                )
            return [Chunk()]

    models = FakeModels()
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    monkeypatch.setattr(llm_client, "_gemini_client_for_model", lambda _model: client)
    monkeypatch.setattr(llm_client, "_iter_gemini_model_candidates", lambda _model: ["gemini-2.5-pro-preview"])

    config = llm_client._build_gemini_config(
        {"temperature": 0.0, "max_output_tokens": 900, "response_mime_type": "application/json"}
    )
    chunks = []
    async for item in llm_client._stream_gemini(
        "prompt",
        {"provider": "gemini", "model_name": "gemini-2.5-pro-preview", "max_tokens": 900},
        generation_config=config,
    ):
        chunks.append(item)

    assert chunks == [(Chunk.text, "gemini-2.5-pro-preview")]
    assert getattr(models.configs[0], "response_mime_type", None) == "application/json"
    assert getattr(models.configs[1], "response_mime_type", None) is None


@pytest.mark.asyncio
async def test_call_model_json_batch_quota_error_skips_individual_fallback(monkeypatch):
    from services.ai_service.common import IntentResult

    calls = {"json": 0}

    async def fail_text(*_args, **_kwargs):
        raise RuntimeError("429 RESOURCE_EXHAUSTED quota exceeded")

    async def count_json(*_args, **_kwargs):
        calls["json"] += 1
        return {}

    monkeypatch.setattr(llm_client, "call_model_text", fail_text)
    monkeypatch.setattr(llm_client, "call_model_json", count_json)

    result = await llm_client.call_model_json_batch(
        {
            "one": {"prompt": "classify one", "schema": IntentResult},
            "two": {"prompt": "classify two", "schema": IntentResult},
            "three": {"prompt": "classify three", "schema": IntentResult},
        },
        engine={"provider": "openai", "model_name": "gpt-test"},
        individual_fallback=True,
    )

    assert result == {"one": None, "two": None, "three": None}
    assert calls["json"] == 0


@pytest.mark.asyncio
async def test_combined_analysis_raises_low_existing_budget_to_allow_response(monkeypatch):
    async def fake_engine(**_kwargs):
        return {"provider": "openai", "model_name": "gpt-test", "id": "llm-1"}

    async def fake_json(*_args, **_kwargs):
        return {
            "intent": {"intent": "greeting", "confidence": 0.9, "entities": {}, "urgency": "low"},
            "sentiment": {"label": "neutral", "score": 0.55, "emotion": "neutral"},
            "conversation_sentiment": {"label": "neutral", "score": 0.55, "trend": "stable"},
            "sentiment_gate": {"escalate": False, "reason": ""},
            "ai_response": {
                "response": "Hi, what can I help with?",
                "deliver_response": True,
                "escalate": False,
                "next_action": "continue_conversation",
            },
            "qualification_hint": {
                "missing_fields": [],
                "completed_fields": [],
                "ready_for_scoring": False,
                "next_question": "",
            },
        }

    monkeypatch.setattr(response_generator, "_resolve_engine_cached", fake_engine)
    monkeypatch.setattr(response_generator, "call_model_json", fake_json)

    token = set_llm_context(company_id="co", max_calls=1, max_embedding_calls=1)
    try:
        result = await response_generator.generate_combined_ai_analysis(
            "hi",
            [{"sender_type": "customer", "content": "hi"}],
            company_id="co",
        )
        snapshot = get_ai_usage_snapshot()
    finally:
        reset_llm_context(token)

    assert result["ai_response_generated"] is True
    assert result["intent"]["intent"] == "greeting"
    assert snapshot["max_llm_calls"] == 1
