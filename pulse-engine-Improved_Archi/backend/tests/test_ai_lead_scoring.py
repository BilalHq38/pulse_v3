import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from agent_orchestrator.memory.store import MemoryStore
from agent_orchestrator.schemas import AgentName, AgentRunResult, GlobalMemory, LeadWorkflowRequest, WorkflowRouteDecision
from agent_orchestrator.workflows.state_store import WorkflowStateStore
from services.ai_service import intent
from services.ai_service import response_generator
from shared.service_client import ServiceClient


@pytest.mark.asyncio
async def test_generate_lead_score_quota_failure_returns_degraded_result(monkeypatch):
    async def fake_engine(**_kwargs):
        return {"provider": "gemini", "model_name": "gemini-2.5-flash"}

    async def fail_json(*_args, **_kwargs):
        raise RuntimeError("429 RESOURCE_EXHAUSTED quota exceeded")

    monkeypatch.setattr(response_generator, "_resolve_engine_cached", fake_engine)
    monkeypatch.setattr(response_generator, "call_model_json", fail_json)

    result = await response_generator.generate_lead_score(
        {"id": "lead-1", "phone": "+923001234567", "notes": "Interested in pricing"},
        db=None,
        company_id="company-1",
    )

    assert result["scoring_status"] == "failed"
    assert result["fallback_used"] is True
    assert result["error_type"] == "quota_exhausted"
    assert result["provider"] == "gemini"
    assert result["model_name"] == "gemini-2.5-flash"
    assert result["next_action"] == "Review lead manually after AI provider is available."


def test_lead_workflow_payload_is_json_serializable_for_service_client():
    payload = LeadWorkflowRequest(
        company_id="company-1",
        lead_id="lead-1",
        lead={"id": "lead-1", "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc)},
    )

    dumped = payload.model_dump(mode="json")

    assert isinstance(dumped["lead"]["created_at"], str)
    assert dumped["lead"]["created_at"].startswith("2026-05-01T00:00:00")


@pytest.mark.asyncio
async def test_service_client_json_encodes_datetime_payload(monkeypatch):
    captured = {}
    client = ServiceClient("http://example.test", retry_attempts=1, service_name="example")

    class FakeAsyncClient:
        async def request(self, **kwargs):
            captured.update(kwargs)
            import httpx

            return httpx.Response(200, json={"ok": True})

    client._client = FakeAsyncClient()

    result = await client.request(
        "POST",
        "/payload",
        json={"created_at": datetime(2026, 5, 1, tzinfo=timezone.utc)},
    )

    assert result == {"ok": True}
    assert isinstance(captured["json"]["created_at"], str)
    assert captured["json"]["created_at"].startswith("2026-05-01T00:00:00")


@pytest.mark.asyncio
async def test_intent_quota_failure_returns_local_fallback(monkeypatch):
    async def fake_engine(**_kwargs):
        return {"provider": "gemini", "model_name": "gemini-2.5-flash"}

    async def fail_json(*_args, **_kwargs):
        raise RuntimeError("429 RESOURCE_EXHAUSTED quota exceeded")

    monkeypatch.setattr(intent, "_resolve_engine_for_request", fake_engine)
    monkeypatch.setattr(intent, "call_model_json", fail_json)

    result = await intent.classify_intent(
        "What are the charges for this option?",
        db=None,
        company_id="company-1",
    )

    assert result["source"] == "local_fallback"
    assert result["intent"] == "pricing_question"
    assert result["error_type"] == "quota_exhausted"


@pytest.mark.asyncio
async def test_orchestrator_memory_store_json_encodes_dates_and_decimals():
    captured = {}

    class FakeDB:
        async def execute(self, query, *args):
            captured["query"] = query
            captured["args"] = args

    class FakeCache:
        async def set_json(self, *_args, **_kwargs):
            return None

    store = MemoryStore.__new__(MemoryStore)
    store.db = FakeDB()
    store.cache = FakeCache()
    store._saved_hashes = {}

    await store.save_global_memory(
        GlobalMemory(
            memory_key="company-1:lead:lead-1",
            company_id="company-1",
            lead_id="lead-1",
            identity_context={"created_at": date(2026, 5, 1), "price": Decimal("12.50")},
            conversation_history=[{"created_at": datetime(2026, 5, 1, tzinfo=timezone.utc)}],
        )
    )

    json.dumps(captured["args"][6])
    json.dumps(captured["args"][7])


@pytest.mark.asyncio
async def test_workflow_state_store_json_encodes_agent_outputs():
    captured = {}

    class FakeDB:
        async def fetchrow(self, *_args):
            return {"shared_context": {}, "final_output": {}}

        async def execute(self, query, *args):
            captured["query"] = query
            captured["args"] = args

    store = WorkflowStateStore.__new__(WorkflowStateStore)
    store.db = FakeDB()
    store.cache = None

    await store.update_after_agent(
        workflow_id="workflow-1",
        current_agent="capture",
        result=AgentRunResult(
            agent_name=AgentName.CAPTURE,
            payload={"lead": {"created_at": date(2026, 5, 1), "price": Decimal("12.50")}},
        ),
        route=None,
    )

    json.dumps(captured["args"][5])
    json.dumps(captured["args"][6])


@pytest.mark.asyncio
async def test_workflow_state_store_append_execution_allows_missing_route():
    captured = {}

    class FakeDB:
        async def execute(self, query, *args):
            captured["query"] = query
            captured["args"] = args

    store = WorkflowStateStore.__new__(WorkflowStateStore)
    store.db = FakeDB()
    store.cache = None

    await store.append_execution(
        workflow_id="workflow-1",
        company_id="company-1",
        trace_id="trace-1",
        result=AgentRunResult(
            agent_name=AgentName.ANALYTICS,
            payload={"created_at": date(2026, 5, 1), "price": Decimal("12.50")},
        ),
        route=None,
        input_payload={"requested_at": datetime(2026, 5, 1, tzinfo=timezone.utc)},
    )

    json.dumps(captured["args"][6])
    json.dumps(captured["args"][7])
    json.dumps(captured["args"][8])
