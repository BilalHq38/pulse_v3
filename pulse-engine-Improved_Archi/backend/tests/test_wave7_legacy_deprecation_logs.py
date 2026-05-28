"""Wave 7 — legacy deprecation log instrumentation.

After Wave 8 deleted `response_generator.generate_ai_response` and its
facade wrapper, two structured deprecation warnings remain on the legacy
runtime paths:

  1. `legacy_support_agent_message_path_executed` — fires in
     SupportAgent._handle_message_support when the per-company opt-out is
     still set (ai_use_conversation_engine = FALSE).
  2. `non_gemini_provider_invoked` — fires inside llm_client.call_model_text
     whenever a tenant config still dispatches to a non-Gemini provider.

These tests confirm the warnings:
  - DO NOT fire when the engine is active (the happy path post-Wave 6).
  - DO fire when the legacy path is reached.

The `legacy_response_generator_invoked` warning + its test were removed in
Wave 8 along with the underlying function.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import pytest

from agent_orchestrator.agents.support_agent import SupportAgent
from agent_orchestrator.schemas import WorkflowKind


@dataclass
class _FakeAgentOutputs:
    capture: dict = field(default_factory=dict)
    qualification: dict = field(default_factory=dict)


@dataclass
class _FakeContext:
    workflow_kind: WorkflowKind
    request: object
    agent_outputs: _FakeAgentOutputs = field(default_factory=_FakeAgentOutputs)
    workflow_id: str = "wf-test"
    db: object = None


class _FakeRequest:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_engine_active_does_not_emit_legacy_warning(caplog):
    """When the suppress flag is True (the Wave-6-default path), the support
    agent must return its stub without logging the legacy-path warning."""
    caplog.set_level(logging.WARNING, logger="agent_orchestrator.agents.support_agent")
    agent = SupportAgent()
    request = _FakeRequest(
        suppress_response_generation=True,
        message_id="m1",
        message_text="hi",
        company_id="c1",
        conversation_id="cv1",
        customer_id="cu1",
        channel="web_chat",
    )
    ctx = _FakeContext(workflow_kind=WorkflowKind.MESSAGE, request=request)
    result = asyncio.run(agent.execute(ctx))
    assert result.status == "skipped"
    assert not any("legacy_support_agent_message_path_executed" in rec.message for rec in caplog.records)


def test_engine_opt_out_emits_legacy_warning(caplog, monkeypatch):
    """When ai_use_conversation_engine is FALSE for the company, the legacy
    MESSAGE path is reached. The warning must fire with the company_id so
    we can correlate opt-outs in production logs."""
    caplog.set_level(logging.WARNING, logger="agent_orchestrator.agents.support_agent")
    agent = SupportAgent()
    # Bypass the actual legacy generation; we only want to assert that the
    # deprecation warning fires before any of the heavy work runs.

    async def _short_circuit_db_call(*_args, **_kwargs):
        return None

    request = _FakeRequest(
        suppress_response_generation=False,
        message_id="m1",
        message_text="anything",
        company_id="co_optout",
        conversation_id="cv1",
        customer_id="cu1",
        channel="web_chat",
    )

    class _NullDb:
        async def fetchval(self, *a, **kw):
            return False

        async def fetchrow(self, *a, **kw):
            return None

        async def fetch(self, *a, **kw):
            return []

        async def execute(self, *a, **kw):
            return None

    ctx = _FakeContext(workflow_kind=WorkflowKind.MESSAGE, request=request, db=_NullDb())
    try:
        asyncio.run(agent.execute(ctx))
    except Exception:
        # The downstream legacy generator may explode on the stubbed DB.
        # That's fine; we only care that the warning fired BEFORE that.
        pass
    deprecation_lines = [r for r in caplog.records if "legacy_support_agent_message_path_executed" in r.message]
    assert deprecation_lines, "expected legacy-path deprecation warning when engine is off"
    line = deprecation_lines[0].message
    assert "company_id=co_optout" in line
    assert "reason=engine_opt_out" in line


def test_legacy_generate_ai_response_function_is_gone():
    """Wave 8 deleted generate_ai_response. Re-introducing it would be a
    regression — guard against that with an import that must fail."""
    from services.ai_service import response_generator

    assert not hasattr(response_generator, "generate_ai_response"), (
        "generate_ai_response should be gone — it was deleted in Wave 8 because "
        "no production caller exercised it after Wave 6 made the conversation "
        "engine the default response generator."
    )

    from services.ai_service import facade

    assert not hasattr(facade, "generate_ai_response"), (
        "facade.generate_ai_response should be gone — wrapped a deleted helper."
    )


def test_non_gemini_provider_warning_fires_for_openai(caplog, monkeypatch):
    """If a tenant config still selects OpenAI / Anthropic / Vertex, we
    surface a non_gemini_provider_invoked warning so the provider's actual
    production usage can be measured before the dispatch branch is removed.
    """
    caplog.set_level(logging.WARNING, logger="services.ai_service.llm_client")
    from services.ai_service import llm_client

    async def _stub_dispatch(*_a, **_kw):
        raise RuntimeError("short-circuit after warning")

    # Force the provider selection to OpenAI by stubbing _text_engine.
    def _force_openai(*_a, **_kw):
        return {"provider": "openai", "model_name": "gpt-4o-mini"}

    monkeypatch.setattr(llm_client, "_text_engine", _force_openai)
    # And stub the actual dispatch so we don't try to hit the OpenAI client.
    monkeypatch.setattr(llm_client, "_get_fallback_provider_order", lambda _p: ["openai"])

    try:
        asyncio.run(llm_client.call_model_text(
            "hello",
            engine=None,
            call_type="text",
            call_purpose="test",
        ))
    except Exception:
        # Expected — the dispatch path beyond the warning is stubbed/broken.
        pass

    deprecation_lines = [r for r in caplog.records if "non_gemini_provider_invoked" in r.message]
    assert deprecation_lines, "expected non_gemini_provider_invoked warning for openai dispatch"
    assert "provider=openai" in deprecation_lines[0].message


def test_non_gemini_warning_does_not_fire_for_gemini(caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger="services.ai_service.llm_client")
    from services.ai_service import llm_client

    def _force_gemini(*_a, **_kw):
        return {"provider": "gemini", "model_name": "gemini-2.5-flash"}

    monkeypatch.setattr(llm_client, "_text_engine", _force_gemini)
    monkeypatch.setattr(llm_client, "_get_fallback_provider_order", lambda _p: ["gemini"])

    try:
        asyncio.run(llm_client.call_model_text(
            "hello",
            engine=None,
            call_type="text",
            call_purpose="test",
        ))
    except Exception:
        pass

    assert not any("non_gemini_provider_invoked" in r.message for r in caplog.records)
