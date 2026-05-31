import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from shared.schemas.contracts import CombinedResponse
from agent_orchestrator.agents import capture_agent
from agent_orchestrator.schemas import MessageWorkflowRequest
from agent_orchestrator.workflows.workflow_manager import WorkflowManager
from services.ai_service.unified_ai_prompt import UnifiedMessageAIResult
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


def test_gemini_json_prompt_includes_strict_valid_json_instruction():
    prompt = llm_client._json_only_prompt("Write a JSON response.", {"type": "object"})
    lower = prompt.lower()

    assert "return only valid json" in lower
    assert "do not return markdown" in lower
    assert "do not wrap json in ```json blocks" in lower
    assert "python json.loads()" in lower
    assert "never return partial json" in lower


def test_gemini_runtime_uses_vertex_without_api_key(monkeypatch):
    monkeypatch.setattr(llm_client, "genai", SimpleNamespace())
    monkeypatch.setattr(llm_client, "GEMINI_API_KEY", "")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "pulse-engine7")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    ready, reason = llm_client.get_provider_runtime_info("gemini")
    kwargs = llm_client._gemini_client_kwargs("v1")

    assert ready is True
    assert reason == ""
    assert kwargs["vertexai"] is True
    assert kwargs["project"] == "pulse-engine7"
    assert kwargs["location"] == "us-central1"
    assert "api_key" not in kwargs


def test_vertex_ai_provider_uses_vertex_without_gemini_api_key(monkeypatch):
    monkeypatch.setattr(llm_client, "genai", SimpleNamespace())
    monkeypatch.setattr(llm_client, "GEMINI_API_KEY", "")
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.delenv("VERTEX_AI_ENABLED", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "pulse-engine7")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    ready, reason = llm_client.get_provider_runtime_info("vertex_ai")
    kwargs = llm_client._gemini_client_kwargs("v1", "vertex_ai")

    assert ready is True
    assert reason == ""
    assert kwargs["vertexai"] is True
    assert "api_key" not in kwargs


def test_gemini_api_provider_requires_gemini_api_key(monkeypatch):
    monkeypatch.setattr(llm_client, "genai", SimpleNamespace())
    monkeypatch.setattr(llm_client, "GEMINI_API_KEY", "")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "pulse-engine7")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    ready, reason = llm_client.get_provider_runtime_info("gemini_api")

    assert ready is False
    assert "GEMINI_API_KEY" in reason


def test_gemini_runtime_still_reports_missing_auth_without_vertex_or_api_key(monkeypatch):
    monkeypatch.setattr(llm_client, "genai", SimpleNamespace())
    monkeypatch.setattr(llm_client, "GEMINI_API_KEY", "")
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.delenv("VERTEX_AI_ENABLED", raising=False)

    ready, reason = llm_client.get_provider_runtime_info("gemini")

    assert ready is False
    assert "GEMINI_API_KEY" in reason


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

    payload_text = captured["prompt"].split("--- Lead Profile ---\n", 1)[1].split("\n\nStrict rules:", 1)[0]
    payload = json.loads(payload_text)
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


@pytest.mark.asyncio
async def test_combined_low_value_message_uses_zero_llm_and_zero_rag(monkeypatch):
    async def fail_context(*_args, **_kwargs):
        raise AssertionError("RAG should not run for low-value combined messages")

    async def fail_unified(*_args, **_kwargs):
        raise AssertionError("Unified LLM should not run for low-value combined messages")

    monkeypatch.setattr(response_generator, "build_ai_context", fail_context)
    monkeypatch.setattr(response_generator, "call_unified_message_ai", fail_unified)

    token = set_llm_context(
        message_id="m-low",
        conversation_id="c-low",
        workflow_id="w-low",
        company_id="co-low",
        max_calls=1,
        max_embedding_calls=1,
    )
    try:
        greeting_result = await response_generator.generate_combined_ai_analysis(
            "hi",
            [{"sender_type": "customer", "content": "hi"}],
            company_id="co-low",
            db=object(),
            conversation_id="c-low",
        )
        social_result = await response_generator.generate_combined_ai_analysis(
            "How are you?",
            [{"sender_type": "customer", "content": "How are you?"}],
            company_id="co-low",
            db=object(),
            conversation_id="c-low",
        )
        snapshot = get_ai_usage_snapshot()
    finally:
        reset_llm_context(token)

    assert greeting_result["intent"]["intent"] == "greeting"
    assert social_result["intent"]["intent"] == "social"
    assert greeting_result["ai_response"]["low_value_short_circuit"] is True
    assert social_result["ai_response"]["low_value_short_circuit"] is True
    assert greeting_result["ai_response"]["rag_called"] is False
    assert social_result["ai_response"]["rag_called"] is False
    assert snapshot["llm_call_count"] == 0
    assert snapshot["embedding_call_count"] == 0


@pytest.mark.asyncio
async def test_combined_non_product_message_does_not_fetch_product_context(monkeypatch):
    async def fail_context(*_args, **_kwargs):
        raise AssertionError("Product/RAG context should not run for ordinary conversation")

    async def fake_engine(**_kwargs):
        return {"provider": "test", "model_name": "test-model", "id": "llm-test"}

    async def fake_unified(*_args, **_kwargs):
        return {
            "sentiment": {"score": 0.02, "sentiment_label": "neutral", "emotion": "neutral"},
            "conversation_sentiment": {"score": 0.02, "sentiment_label": "neutral", "emotion": "neutral"},
            "intent": {"intent": "general_question", "confidence": 0.85, "entities": {}, "urgency": "low"},
            "ai_response": {"response": "I can help with that. What do you need next?", "confidence": 0.9},
            "qualification_hint": {},
            "interaction_summary": {},
        }

    monkeypatch.setattr(response_generator, "build_ai_context", fail_context)
    monkeypatch.setattr(response_generator, "_resolve_engine_cached", fake_engine)
    monkeypatch.setattr(response_generator, "call_unified_message_ai", fake_unified)

    result = await response_generator.generate_combined_ai_analysis(
        "Can you help me today?",
        [{"sender_type": "customer", "content": "Can you help me today?"}],
        company_id="co-gate",
        db=object(),
        conversation_id="c-gate",
    )

    assert result["ai_response"]["product_context_blocked"] is True
    assert result["ai_response"]["attachments"] == []
    assert result["ai_response"]["product_ids"] == []


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


@pytest.mark.asyncio
async def test_call_model_json_valid_json_still_works(monkeypatch):
    calls = []

    async def fake_call_model_text(prompt, *_args, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        return '{"subject":"Hi","body":"Body","html_body":"<p>Body</p>"}'

    monkeypatch.setattr(llm_client, "call_model_text", fake_call_model_text)

    result = await llm_client.call_model_json(
        "Write campaign copy.",
        _CampaignDraftForTest,
        engine={"provider": "gemini", "model_name": "gemini-test", "max_tokens": 900},
        call_purpose="email_campaign_copy",
    )

    assert result == {"subject": "Hi", "body": "Body", "html_body": "<p>Body</p>"}
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_call_model_json_parses_markdown_wrapped_json(monkeypatch):
    async def fake_call_model_text(*_args, **_kwargs):
        return '```json\n{"subject":"Hi","body":"Body","html_body":"<p>Body</p>"}\n```'

    monkeypatch.setattr(llm_client, "call_model_text", fake_call_model_text)

    result = await llm_client.call_model_json(
        "Write campaign copy.",
        _CampaignDraftForTest,
        engine={"provider": "gemini", "model_name": "gemini-test", "max_tokens": 900},
        call_purpose="email_campaign_copy",
    )

    assert result["body"] == "Body"


@pytest.mark.asyncio
async def test_call_model_json_parses_json_with_surrounding_prose(monkeypatch):
    async def fake_call_model_text(*_args, **_kwargs):
        return 'Here is the JSON:\n{"subject":"Hi","body":"Body","html_body":"<p>Body</p>"}\nDone.'

    monkeypatch.setattr(llm_client, "call_model_text", fake_call_model_text)

    result = await llm_client.call_model_json(
        "Write campaign copy.",
        _CampaignDraftForTest,
        engine={"provider": "gemini", "model_name": "gemini-test", "max_tokens": 900},
        call_purpose="email_campaign_copy",
    )

    assert result["subject"] == "Hi"


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


def test_unified_message_json_calls_use_2000_token_floor():
    selected = llm_client._json_engine_for_call(
        {"provider": "gemini", "model_name": "gemini-test", "max_tokens": 512},
        call_purpose="unified_message_ai",
    )

    assert selected["max_tokens"] >= 2000


def test_unified_lead_json_calls_use_1500_token_floor():
    selected = llm_client._json_engine_for_call(
        {"provider": "gemini", "model_name": "gemini-test", "max_tokens": 512},
        call_purpose="unified_lead_ai",
    )

    assert selected["max_tokens"] >= 1500


def test_normalize_gemini_config_payload_preserves_response_format_when_supported(monkeypatch):
    monkeypatch.setattr(
        llm_client,
        "_gemini_config_fields",
        lambda: {"temperature", "max_output_tokens", "response_format"},
    )

    config = llm_client._normalize_gemini_config_payload(
        {
            "temperature": 0.0,
            "maxOutputTokens": 900,
            "response_format": {"text": {"mime_type": "application/json", "schema": {"type": "object"}}},
        }
    )

    assert config["response_format"]["text"]["mime_type"] == "application/json"
    assert config["max_output_tokens"] == 900


def test_json_generation_config_prefers_gemini_response_format_when_supported(monkeypatch):
    llm_client._GEMINI_RESPONSE_FORMAT_UNSUPPORTED_MODELS.clear()
    monkeypatch.setattr(llm_client, "genai_types", SimpleNamespace())
    monkeypatch.setattr(
        llm_client,
        "_gemini_config_fields",
        lambda: {"temperature", "max_output_tokens", "response_format", "response_mime_type", "response_schema"},
    )

    config = llm_client._json_generation_config(
        {"provider": "gemini", "model_name": "gemini-2.5-pro", "max_tokens": 900},
        _CampaignDraftForTest,
        _CampaignDraftForTest.model_json_schema(),
    )

    assert config["response_format"]["text"]["mime_type"] == "application/json"
    assert config["response_format"]["text"]["schema"]["properties"]["subject"]["type"] == "string"
    assert "response_mime_type" not in config
    assert "response_schema" not in config


def test_json_generation_config_omits_response_mime_after_marked_unsupported(monkeypatch):
    model = "gemini-2.5-pro-preview"
    llm_client._GEMINI_RESPONSE_MIME_UNSUPPORTED_MODELS.add(llm_client._gemini_response_mime_cache_key(model))
    monkeypatch.setattr(llm_client, "genai_types", SimpleNamespace())
    monkeypatch.setattr(
        llm_client,
        "_gemini_config_fields",
        lambda: {"temperature", "max_output_tokens", "response_mime_type", "response_schema"},
    )

    config = llm_client._json_generation_config(
        {"provider": "gemini", "model_name": model, "max_tokens": 900},
        _CampaignDraftForTest,
        _CampaignDraftForTest.model_json_schema(),
    )

    assert "response_mime_type" not in config
    assert "response_schema" not in config


def test_gemini_25_flash_lite_is_structured_output_capable(monkeypatch):
    monkeypatch.setattr(llm_client, "genai_types", SimpleNamespace())
    monkeypatch.setattr(
        llm_client,
        "_gemini_config_fields",
        lambda: {"temperature", "max_output_tokens", "response_mime_type", "response_schema"},
    )

    assert llm_client._gemini_supports_response_mime_type(
        {"provider": "gemini", "model_name": "gemini-2.5-flash-lite"}
    )
    assert llm_client._gemini_supports_response_mime_type(
        {"provider": "gemini", "model_name": "gemini-2.5-flash-lite-preview"}
    )
    assert llm_client._gemini_supports_response_mime_type(
        {"provider": "gemini", "model_name": "gemini-2.5-flash"}
    )
    assert llm_client._gemini_supports_response_mime_type(
        {"provider": "gemini", "model_name": "gemini-2.5-pro"}
    )


def test_unified_message_uses_legacy_structured_json_for_gemini_25_when_supported(monkeypatch):
    llm_client._GEMINI_RESPONSE_MIME_UNSUPPORTED_MODELS.clear()
    monkeypatch.setattr(llm_client, "genai_types", SimpleNamespace())
    monkeypatch.setattr(
        llm_client,
        "_gemini_config_fields",
        lambda: {"temperature", "max_output_tokens", "response_mime_type", "response_schema"},
    )

    config = llm_client._json_generation_config(
        {"provider": "gemini", "model_name": "gemini-2.5-pro-preview", "max_tokens": 2000},
        UnifiedMessageAIResult,
        UnifiedMessageAIResult.model_json_schema(),
        call_purpose="unified_message_ai",
    )

    assert config["response_mime_type"] == "application/json"
    assert config["response_schema"] is UnifiedMessageAIResult
    assert config["max_output_tokens"] == 2000


@pytest.mark.asyncio
async def test_call_model_json_recovers_malformed_json_without_second_model_call(monkeypatch):
    calls = []

    async def fake_call_model_text(prompt, *_args, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        return '{"subject":"Hi","body":"This response was cut off'

    monkeypatch.setattr(llm_client, "call_model_text", fake_call_model_text)

    result = await llm_client.call_model_json(
        "Write campaign copy.",
        _CampaignDraftForTest,
        engine={"provider": "gemini", "model_name": "gemini-test", "max_tokens": 24},
        call_purpose="email_campaign_copy",
    )

    assert result == {"subject": "Hi", "body": "This response was cut off", "html_body": ""}
    assert len(calls) == 1
    assert calls[0]["engine"]["max_tokens"] >= 900
    assert calls[0]["generation_config"].get("response_format") != "json_object"


@pytest.mark.asyncio
async def test_call_model_json_recovers_unified_reply_from_truncated_json(monkeypatch):
    calls = []

    async def fake_call_model_text(*_args, **_kwargs):
        calls.append(1)
        return '{"intent":{"intent":"pricing_question","confidence":0.9},"ai_response":{"response":"Sure, I can help with pricing'

    monkeypatch.setattr(llm_client, "call_model_text", fake_call_model_text)

    result = await llm_client.call_model_json(
        "Respond to the customer.",
        UnifiedMessageAIResult,
        engine={"provider": "gemini", "model_name": "gemini-test", "max_tokens": 512},
        call_purpose="unified_message_ai",
        max_provider_attempts=1,
        allow_provider_fallback=False,
    )

    assert result["ai_response"]["response"] == "Sure, I can help with pricing"
    assert result["intent"]["confidence"] == 0.9
    assert result["sentiment"]["label"] == "neutral"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_unified_message_json_hot_path_forces_single_provider_attempt(monkeypatch):
    calls = []

    async def fake_call_model_text(prompt, *_args, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        return '{"ai_response":{"response":"Hi there"}}'

    monkeypatch.setattr(llm_client, "call_model_text", fake_call_model_text)

    result = await llm_client.call_model_json(
        "Respond to the customer.",
        UnifiedMessageAIResult,
        engine={"provider": "gemini", "model_name": "gemini-2.5-flash", "max_tokens": 512},
        call_purpose="unified_message_ai",
    )

    assert result["ai_response"]["response"] == "Hi there"
    assert len(calls) == 1
    assert calls[0]["max_provider_attempts"] == 1
    assert calls[0]["allow_provider_fallback"] is False
    assert calls[0]["engine"]["_disable_transient_retries"] is True


@pytest.mark.asyncio
async def test_customer_json_single_attempt_flag_disables_transient_retries(monkeypatch):
    calls = {"stream": 0}

    async def fail_stream(*_args, **_kwargs):
        calls["stream"] += 1
        raise RuntimeError("temporary upstream error")
        yield "", ""

    monkeypatch.setattr(llm_client, "_stream_provider_once", fail_stream)

    with pytest.raises(RuntimeError, match="temporary upstream error"):
        await llm_client._call_provider_once(
            "gemini",
            "prompt",
            {
                "provider": "gemini",
                "model_name": "gemini-2.5-flash",
                "max_tokens": 2000,
                "_disable_structured_config_retry": True,
            },
        )

    assert calls["stream"] == 1


@pytest.mark.asyncio
async def test_gemini_stream_disables_rejected_response_mime_type_for_future_requests(monkeypatch):
    llm_client._GEMINI_RESPONSE_MIME_UNSUPPORTED_MODELS.clear()

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
    monkeypatch.setattr(llm_client, "_gemini_client_for_model", lambda _model, *_args: client)
    monkeypatch.setattr(llm_client, "_iter_gemini_model_candidates", lambda _model: ["gemini-2.5-pro-preview"])
    monkeypatch.setattr(
        llm_client,
        "_prepare_gemini_generation_config",
        lambda _engine, _generation_config=None: {
            "temperature": 0.0,
            "max_output_tokens": 900,
            "response_mime_type": "application/json",
            "response_schema": {"type": "object"},
        },
    )

    chunks = []
    async for item in llm_client._stream_gemini(
        "prompt",
        {"provider": "gemini", "model_name": "gemini-2.5-pro-preview", "max_tokens": 900},
    ):
        chunks.append(item)

    assert chunks == [(Chunk.text, "gemini-2.5-pro-preview")]
    assert models.configs[0]["response_mime_type"] == "application/json"
    assert models.configs[0]["response_schema"] == {"type": "object"}
    assert "response_mime_type" not in models.configs[1]
    assert "response_schema" not in models.configs[1]

    chunks = []
    async for item in llm_client._stream_gemini(
        "prompt",
        {"provider": "gemini", "model_name": "gemini-2.5-pro-preview", "max_tokens": 900},
    ):
        chunks.append(item)

    assert chunks == [(Chunk.text, "gemini-2.5-pro-preview")]
    assert "response_mime_type" not in models.configs[2]
    assert "response_schema" not in models.configs[2]


@pytest.mark.asyncio
async def test_gemini_quota_error_does_not_retry_other_models(monkeypatch):
    class FakeModels:
        def __init__(self):
            self.calls = 0

        async def generate_content_stream(self, *, model, contents, config):
            self.calls += 1
            raise RuntimeError("429 RESOURCE_EXHAUSTED quota exceeded")

    models = FakeModels()
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    monkeypatch.setattr(llm_client, "_gemini_client_for_model", lambda _model, *_args: client)
    monkeypatch.setattr(llm_client, "_iter_gemini_model_candidates", lambda _model: ["gemini-one", "gemini-two"])

    with pytest.raises(RuntimeError, match="RESOURCE_EXHAUSTED"):
        async for _item in llm_client._stream_gemini(
            "prompt",
            {"provider": "gemini", "model_name": "gemini-one", "max_tokens": 900},
            generation_config={"temperature": 0.0, "max_output_tokens": 900},
        ):
            pass

    assert models.calls == 1


@pytest.mark.asyncio
async def test_gemini_json_call_uses_non_streaming_generate_content(monkeypatch):
    class Response:
        text = '{"subject":"Hi","body":"Body","html_body":"<p>Body</p>"}'

    class FakeModels:
        def __init__(self):
            self.generate_calls = 0
            self.stream_calls = 0

        async def generate_content(self, *, model, contents, config):
            self.generate_calls += 1
            return Response()

        async def generate_content_stream(self, *_args, **_kwargs):
            self.stream_calls += 1
            raise AssertionError("JSON calls must not use streaming")

    models = FakeModels()
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    monkeypatch.setattr(llm_client, "_gemini_client_for_model", lambda _model, *_args: client)
    monkeypatch.setattr(llm_client, "_iter_gemini_model_candidates", lambda _model: ["gemini-2.5-flash-lite"])
    monkeypatch.setattr(llm_client, "_prepare_gemini_generation_config", lambda _engine, config=None: config)

    text, model, usage = await llm_client._call_provider_once(
        "gemini",
        "prompt",
        {"provider": "gemini", "model_name": "gemini-2.5-flash-lite", "max_tokens": 512},
        generation_config={"temperature": 0.0, "max_output_tokens": 512},
        call_type="json",
    )

    assert text == Response.text
    assert model == "gemini-2.5-flash-lite"
    assert usage["completion_tokens"] > 0
    assert models.generate_calls == 1
    assert models.stream_calls == 0


@pytest.mark.asyncio
async def test_gemini_json_quota_error_is_not_retried(monkeypatch):
    class FakeModels:
        def __init__(self):
            self.calls = 0

        async def generate_content(self, *, model, contents, config):
            self.calls += 1
            raise RuntimeError("429 RESOURCE_EXHAUSTED quota exceeded")

    models = FakeModels()
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    monkeypatch.setattr(llm_client, "_gemini_client_for_model", lambda _model, *_args: client)
    monkeypatch.setattr(llm_client, "_iter_gemini_model_candidates", lambda _model: ["gemini-2.5-flash-lite"])
    monkeypatch.setattr(llm_client, "_prepare_gemini_generation_config", lambda _engine, config=None: config)

    with pytest.raises(RuntimeError, match="RESOURCE_EXHAUSTED"):
        await llm_client._call_provider_once(
            "gemini",
            "prompt",
            {"provider": "gemini", "model_name": "gemini-2.5-flash-lite", "max_tokens": 512},
            generation_config={"temperature": 0.0, "max_output_tokens": 512},
            call_type="json",
        )

    assert models.calls == 1


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
