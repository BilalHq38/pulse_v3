import pytest
from types import SimpleNamespace

import agent_orchestrator.agents.support_agent as support_agent
import services.db_helpers as db_helpers
from agent_orchestrator.agents.support_agent import SupportAgent
from agent_orchestrator.schemas import WorkflowKind
from services.ai_service import llm_client
from services.ai_service.model_catalog import is_supported_model, model_capabilities, supported_model_catalog
from services.db_helpers import (
    AI_API_EXHAUSTED_MANUAL_MESSAGE,
    auto_disable_ai_after_failure_fallback,
    conversation_ai_auto_paused,
    ensure_conversation_ai_pause_schema,
    ensure_default_llm_engine,
    escalate_conversation_to_human,
    is_ai_api_exhaustion_payload,
    is_ai_failure_fallback_payload,
    pause_ai_auto_response,
)


class FakeEngineDb:
    def __init__(self):
        self.rows = [
            {
                "id": "old-pro",
                "company_id": "",
                "provider": "gemini",
                "model_name": "gemini-2.5-pro",
                "version": "Gemini 2.5 Pro",
                "is_active": True,
            },
            {
                "id": "tenant-pro",
                "company_id": "co-1",
                "provider": "gemini",
                "model_name": "gemini-2.5-pro",
                "version": "Custom Pro",
                "is_active": True,
            },
        ]

    async def fetchval(self, sql, *args):
        if "FROM llm_engines" in sql:
            model_name = args[0]
            for row in self.rows:
                if row["company_id"] == "" and row["provider"] == "gemini" and row["model_name"] == model_name:
                    return row["id"]
        return None

    async def fetchrow(self, sql, *args):
        if "WHERE provider=$1 AND model_name=$2" in sql:
            provider, model_name = args
            for row in self.rows:
                if row["company_id"] == "" and row["provider"] == provider and row["model_name"] == model_name:
                    return row
            return None
        if "WHERE id=$1" in sql:
            row_id = args[0]
            for row in self.rows:
                if row["id"] == row_id:
                    return row
        return None

    async def execute(self, sql, *args):
        if sql.startswith("DELETE FROM llm_engines"):
            if "NOT (provider='gemini' AND model_name=$1)" in sql:
                model_name = args[0]
                self.rows = [
                    row
                    for row in self.rows
                    if row["company_id"] != "" or (row["provider"] == "gemini" and row["model_name"] == model_name)
                ]
                return
            model_name, version = args
            self.rows = [
                row
                for row in self.rows
                if not (
                    row["company_id"] == ""
                    and row["provider"] == "gemini"
                    and row["model_name"] == model_name
                    and row.get("version") == version
                )
            ]
            return
        if sql.startswith("UPDATE llm_engines"):
            return
        if sql.startswith("INSERT INTO llm_engines"):
            row_id = args[0]
            if len(args) == 7:
                _, model_name, provider, api_endpoint, temperature, max_tokens, version = args
            else:
                _, model_name, temperature, max_tokens, version = args
                provider = "gemini"
                api_endpoint = ""
            self.rows.append(
                {
                    "id": row_id,
                    "company_id": "",
                    "provider": provider,
                    "model_name": model_name,
                    "api_endpoint": api_endpoint,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "version": version,
                    "is_active": True,
                }
            )


class FakePauseDb:
    def __init__(self):
        self.executed = []
        self.messages = {}
        self.conversation = {"id": "convo-1", "company_id": "co-1", "ai_auto_paused": True, "ai_handled": False}

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        if sql.startswith("INSERT INTO messages"):
            self.messages[args[0]] = {
                "id": args[0],
                "company_id": args[1],
                "conversation_id": args[2],
                "content": args[3],
                "sender_type": "system",
            }

    async def fetchrow(self, sql, *args):
        if "FROM messages" in sql:
            return self.messages.get(args[0])
        if "FROM conversations" in sql:
            return self.conversation
        return None


class FakeSchemaDb:
    def __init__(self):
        self.executed = []
        self.checked = []

    async def execute(self, sql, *args):
        self.executed.append(sql)

    async def fetchval(self, sql, *args):
        self.checked.append((sql, args))
        return True


class FakeGlobalEngineDb:
    async def fetch(self, sql, *args):
        if "FROM llm_engines" in sql:
            return [
                {
                    "id": "global-flash",
                    "company_id": "",
                    "provider": "gemini",
                    "model_name": "gemini-2.5-flash",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "is_active": True,
                    "version": "Gemini 2.5 Flash",
                }
            ]
        return []


@pytest.mark.asyncio
async def test_conversation_ai_pause_schema_bootstrap_checks_required_columns_without_runtime_ddl(monkeypatch):
    monkeypatch.setattr(db_helpers, "_conversation_ai_pause_schema_ready", False)
    db = FakeSchemaDb()

    await ensure_conversation_ai_pause_schema(db)

    checked = "\n".join(str(args) for _sql, args in db.checked)
    assert "ai_auto_paused" in checked
    assert "ai_paused_reason" in checked
    assert "ai_paused_scope" in checked
    assert db.executed == []


@pytest.mark.asyncio
async def test_global_engine_resolution_uses_global_engines_without_company_warning(caplog):
    caplog.set_level("WARNING")

    engine = await llm_client._resolve_engine_for_request(db=FakeGlobalEngineDb(), company_id="")

    assert engine["model_name"] == "gemini-2.5-flash"
    assert "Engine resolution failed" not in caplog.text


@pytest.mark.asyncio
async def test_default_engine_seeding_keeps_only_gemini_25_flash_and_preserves_tenant_engines():
    db = FakeEngineDb()

    engine = await ensure_default_llm_engine(db)

    assert engine["provider"] == "gemini"
    assert engine["model_name"] == "gemini-2.5-flash"
    global_gemini = [row["model_name"] for row in db.rows if row["company_id"] == "" and row["provider"] == "gemini"]
    assert global_gemini == ["gemini-2.5-flash"]
    assert any(row["id"] == "tenant-pro" and row["model_name"] == "gemini-2.5-pro" for row in db.rows)


def test_gemini_catalog_exposes_only_supported_generation_models():
    catalog = supported_model_catalog("gemini")
    names_by_category = {(item["category"], item["model_name"]) for item in catalog}

    assert ("text_generation", "gemini-2.5-flash") in names_by_category
    assert ("text_generation", "gemini-2.5-flash-lite") in names_by_category
    assert ("text_generation", "gemini-2.5-pro") in names_by_category
    assert not is_supported_model("gemini", "gemini-" + "3.1" + "-flash")
    assert not is_supported_model("gemini", "gemini-" + "3-flash-preview")
    assert not is_supported_model("gemini", "gemma-" + "4-31b")
    assert model_capabilities("gemini", "gemini-2.5-flash")["supports_vision"] is True


def test_vertex_ai_catalog_uses_supported_gemini_models():
    catalog = supported_model_catalog("vertex_ai")
    names_by_category = {(item["category"], item["model_name"]) for item in catalog}

    assert ("text_generation", "gemini-2.5-flash-lite") in names_by_category
    assert is_supported_model("vertex_ai", "gemini-2.5-flash-lite")
    assert not is_supported_model("vertex_ai", "gemini-" + "3.1" + "-flash")
    assert model_capabilities("vertex_ai", "gemini-2.5-flash-lite")["supports_text"] is True


def test_provider_status_treats_vertex_ai_as_server_configured(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.delenv("VERTEX_AI_ENABLED", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "pulse-engine7")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    status, detail = db_helpers.provider_configuration_status("vertex_ai")

    assert status == "configured"
    assert detail == ""


def test_unsupported_gemini_model_error_is_clean(monkeypatch):
    monkeypatch.setattr(llm_client, "get_provider_runtime_info", lambda _provider: (True, ""))

    invalid_model = "gemini-" + "3.1" + "-flash"
    with pytest.raises(RuntimeError, match=f"Unsupported Gemini model: {invalid_model}"):
        llm_client.validate_live_engine(
            {"provider": "gemini", "model_name": invalid_model},
            require_vision=True,
        )


def test_serious_provider_failures_are_detected():
    assert is_ai_api_exhaustion_payload({"error_type": "permission_denied", "error_reason": "403 PERMISSION_DENIED"})
    assert is_ai_api_exhaustion_payload({"error_reason": "suspended API key"})
    assert is_ai_api_exhaustion_payload({"error_reason": "all providers exhausted"})
    assert is_ai_api_exhaustion_payload({"error_reason": "invalid model"})


def test_static_provider_fallback_requires_ai_shutdown():
    assert is_ai_failure_fallback_payload(
        {
            "static_fallback_served": True,
            "api_error": True,
            "error_type": "quota_exhausted",
        }
    )
    assert not is_ai_failure_fallback_payload({"fallback_used": True, "api_error": False})


def test_ai_disabled_until_counts_as_paused():
    assert conversation_ai_auto_paused(
        {
            "ai_handled": True,
            "ai_auto_paused": False,
            "ai_disabled_until": "2999-01-01T00:00:00+00:00",
        }
    )


@pytest.mark.asyncio
async def test_provider_failure_persists_conversation_pause_state():
    db = FakePauseDb()

    await pause_ai_auto_response(
        db,
        "co-1",
        "convo-1",
        reason=AI_API_EXHAUSTED_MANUAL_MESSAGE,
        error_type="quota_exhausted",
        provider="gemini",
        model="gemini-2.5-flash",
    )

    sql, args = db.executed[0]
    assert "ai_auto_paused=TRUE" in sql
    assert "ai_paused_reason" in sql
    assert args[0] == AI_API_EXHAUSTED_MANUAL_MESSAGE
    assert args[1] == "quota_exhausted"
    assert args[2] == "gemini"
    assert args[3] == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_static_fallback_auto_disables_conversation_ai():
    db = FakePauseDb()
    db.conversation = {"id": "convo-1", "company_id": "co-1", "ai_auto_paused": False, "ai_handled": True}

    disabled = await auto_disable_ai_after_failure_fallback(
        db,
        "co-1",
        "convo-1",
        {
            "static_fallback_served": True,
            "api_error": True,
            "error_type": "quota_exhausted",
            "provider": "static_fallback",
            "model_name": "static-fallback",
        },
        channel="web_chat",
        trace_id="trace-1",
    )

    sql, args = db.executed[0]
    assert disabled is True
    assert "ai_auto_paused=TRUE" in sql
    assert "ai_failure_count=COALESCE(ai_failure_count,0)+1" in sql
    assert args[1] == "quota_exhausted"


@pytest.mark.asyncio
async def test_manual_image_send_does_not_auto_disable_ai():
    db = FakePauseDb()

    disabled = await auto_disable_ai_after_failure_fallback(
        db,
        "co-1",
        "convo-1",
        {
            "sender_type": "agent",
            "message_type": "media",
            "media_type": "image",
            "static_fallback_served": False,
            "api_error": False,
        },
        channel="whatsapp",
        trace_id="trace-media",
    )

    assert disabled is False
    assert db.executed == []


@pytest.mark.asyncio
async def test_manual_text_send_does_not_auto_disable_ai():
    db = FakePauseDb()

    disabled = await auto_disable_ai_after_failure_fallback(
        db,
        "co-1",
        "convo-1",
        {
            "sender_type": "agent",
            "message_type": "text",
            "static_fallback_served": False,
            "api_error": False,
        },
        channel="web_chat",
        trace_id="trace-text",
    )

    assert disabled is False
    assert db.executed == []


@pytest.mark.asyncio
async def test_customer_media_does_not_auto_disable_ai():
    db = FakePauseDb()

    disabled = await auto_disable_ai_after_failure_fallback(
        db,
        "co-1",
        "convo-1",
        {
            "sender_type": "customer",
            "message_type": "media",
            "media_type": "image",
            "static_fallback_served": False,
            "api_error": False,
        },
        channel="whatsapp",
        trace_id="trace-customer-media",
    )

    assert disabled is False
    assert db.executed == []


@pytest.mark.asyncio
async def test_escalation_pauses_ai_auto_response():
    db = FakePauseDb()

    await escalate_conversation_to_human(
        db,
        "convo-1",
        "co-1",
        "Customer",
        "web_chat",
        reason="Customer asked for a human",
        automatic=True,
    )

    update_sql, update_args = db.executed[0]
    assert "ai_auto_paused=TRUE" in update_sql
    assert "conversation_escalated" in update_sql
    assert update_args[0] == "Customer asked for a human"
    assert any("Conversation escalated" in msg["content"] for msg in db.messages.values())


@pytest.mark.asyncio
async def test_support_agent_provider_failure_returns_static_fallback(monkeypatch):
    async def threshold(*_args, **_kwargs):
        return 0.6

    monkeypatch.setattr(support_agent, "fetch_company_ai_threshold", threshold)

    context = SimpleNamespace(
        workflow_kind=WorkflowKind.MESSAGE,
        workflow_id="wf-1",
        company_id="co-1",
        db=None,
        request=SimpleNamespace(
            message_text="What products do you have?",
            conversation_id="convo-1",
            customer_id="cust-1",
            message_id="msg-1",
            conversation_context=[],
            knowledge_context="",
            channel="web_chat",
        ),
        global_memory=SimpleNamespace(conversation_history=[]),
        agent_outputs=SimpleNamespace(
            capture={
                "customer": {"id": "cust-1", "name": "Customer"},
                "sentiment": {"emotion": "neutral", "score": 0},
                "intent": {"intent": "product_recommendation"},
                "sentiment_gate": {"ai_response_allowed": True},
                "prefetched_support_response": {
                    "response": "I can help with products.",
                    "confidence": 0.9,
                    "api_error": True,
                    "error_type": "quota_exhausted",
                    "error_reason": "quota_exhausted",
                    "provider": "gemini",
                    "model_name": "gemini-2.5-flash",
                    "provider_error": {"error_type": "quota_exhausted"},
                },
            },
            qualification={},
        ),
    )

    result = await SupportAgent().execute(context)
    payload = result.payload

    assert payload["provider"] == "static_fallback"
    assert payload["deliver_response"] is True
    assert "having trouble connecting" in payload["response"]


@pytest.mark.asyncio
async def test_support_agent_low_confidence_does_not_auto_escalate(monkeypatch):
    async def threshold(*_args, **_kwargs):
        return 0.8

    monkeypatch.setattr(support_agent, "fetch_company_ai_threshold", threshold)

    context = SimpleNamespace(
        workflow_kind=WorkflowKind.MESSAGE,
        workflow_id="wf-low-confidence",
        company_id="co-1",
        db=None,
        request=SimpleNamespace(
            message_text="Can you help me pick one?",
            conversation_id="convo-1",
            customer_id="cust-1",
            message_id="msg-1",
            conversation_context=[],
            knowledge_context="",
            channel="web_chat",
        ),
        global_memory=SimpleNamespace(conversation_history=[]),
        agent_outputs=SimpleNamespace(
            capture={
                "customer": {"id": "cust-1", "name": "Customer"},
                "sentiment": {"emotion": "neutral", "score": 0},
                "intent": {"intent": "product_recommendation"},
                "sentiment_gate": {"ai_response_allowed": True},
                "prefetched_support_response": {
                    "response": "I can help you compare the available options.",
                    "confidence": 0.3,
                    "api_error": False,
                    "provider": "gemini",
                    "model_name": "gemini-2.5-flash-lite",
                },
            },
            qualification={},
        ),
    )

    payload = (await SupportAgent().execute(context)).payload

    assert payload["requires_review"] is True
    assert payload["escalate"] is False
    assert payload["deliver_response"] is True
