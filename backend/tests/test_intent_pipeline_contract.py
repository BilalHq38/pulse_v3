from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from agent_orchestrator.agents.capture_agent import CaptureAgent
from memory_engine.manager import MemoryManager
from services.ai_service import intent as intent_mod


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_intent"),
    [
        ("Tell me about your products", "product_catalog_question"),
        ("I want to place an order", "order_intent"),
        ("Tell me about your company", "company_question"),
        ("What is your return policy?", "faq"),
        ("I have a complaint because this arrived broken", "complaint"),
        ("zxqv blorf", "unknown"),
    ],
)
async def test_intent_classification_contract_covers_core_message_types(
    monkeypatch,
    caplog,
    message: str,
    expected_intent: str,
) -> None:
    async def fail_json(*_args, **_kwargs):
        raise RuntimeError("No configured AI providers")

    async def fake_engine(**_kwargs):
        return {}

    async def no_sleep(*_args, **_kwargs):
        return None

    real_lightweight = intent_mod.lightweight_route_message

    def lightweight_without_unknown_ml(text: str, **kwargs):
        if "zxqv" in text:
            return {}
        return real_lightweight(text, **kwargs)

    monkeypatch.setattr(intent_mod, "call_model_json", fail_json)
    monkeypatch.setattr(intent_mod, "_resolve_engine_for_request", fake_engine)
    monkeypatch.setattr(intent_mod.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(intent_mod, "lightweight_route_message", lightweight_without_unknown_ml)
    caplog.set_level(logging.INFO, logger="services.ai_service.intent")

    result = await intent_mod.classify_intent(message, db=None, company_id="company-1")

    assert result["intent"] == expected_intent
    assert 0.0 <= float(result["confidence"]) <= 1.0
    assert "intent_classification raw_message=" in caplog.text
    assert f"classified_intent={expected_intent}" in caplog.text
    assert "confidence=" in caplog.text


@pytest.mark.asyncio
async def test_memory_manager_update_memory_stores_pending_intent() -> None:
    captured = {}

    class FakeShortTerm:
        async def store_intent(self, tenant_id, user_id, intent_data, *, conversation_id=""):
            captured.update(
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "intent_data": intent_data,
                    "conversation_id": conversation_id,
                }
            )

    manager = MemoryManager(db=None)
    manager.short_term = FakeShortTerm()

    await manager.update_memory(
        "customer-1",
        "company-1",
        memory_type="pending_intent",
        content={"intent": "order_intent", "confidence": 0.92},
        conversation_id="conversation-1",
    )

    assert captured == {
        "tenant_id": "company-1",
        "user_id": "customer-1",
        "intent_data": {"intent": "order_intent", "confidence": 0.92},
        "conversation_id": "conversation-1",
    }


@pytest.mark.asyncio
async def test_capture_agent_persists_classified_intent_to_pending_intent(monkeypatch, caplog) -> None:
    captured = {}

    class FakeMemoryManager:
        def __init__(self, db=None):
            captured["db"] = db

        async def update_memory(self, user_id, tenant_id, *, memory_type, content, conversation_id=""):
            captured.update(
                {
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "memory_type": memory_type,
                    "content": content,
                    "conversation_id": conversation_id,
                }
            )

    import memory_engine.manager as manager_mod

    monkeypatch.setattr(manager_mod, "MemoryManager", FakeMemoryManager)
    caplog.set_level(logging.INFO, logger="agent_orchestrator.agents.capture_agent")
    context = SimpleNamespace(
        company_id="company-1",
        db=object(),
        request=SimpleNamespace(customer_id="customer-1", conversation_id="conversation-1"),
        global_memory=SimpleNamespace(customer_id="", conversation_id=""),
    )

    await CaptureAgent()._store_pending_intent(context, {"intent": "faq", "confidence": 0.82})

    assert captured["memory_type"] == "pending_intent"
    assert captured["content"] == {"intent": "faq", "confidence": 0.82}
    assert captured["tenant_id"] == "company-1"
    assert captured["user_id"] == "customer-1"
    assert captured["conversation_id"] == "conversation-1"
    assert "pending_intent_stored" in caplog.text
