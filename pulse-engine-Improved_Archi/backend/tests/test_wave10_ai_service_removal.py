"""Wave 10 — ai-service microservice runtime removal.

After this wave, no code path in the backend HTTP-roundtrips to the
ai-service microservice. All facade methods call their local implementations
directly. The deployment-level teardown of the ai-service container (its
Docker image, its entry point, its routes, its env vars) remains the
operator's task — but from a Python runtime perspective, the microservice is
unreachable.

These regression tests guard the cut:
  1. The HTTP-transport helpers (`_call_remote_ai`, `_prefer_local_impl`,
     `_allow_local_fallback`, the `AI_CLIENT` ServiceClient instance) must
     stay deleted. Re-introducing any of them would be a regression to the
     remote-microservice pattern.
  2. The public facade surface (`analyze_sentiment`, `classify_intent`,
     `generate_combined_ai_analysis`, etc.) still works after the cut so
     callers (capture_agent, lead routers, etc.) keep functioning.
  3. Each facade method swallows local-impl failures into a structured log
     + a safe-default response, never silently raising — the user's stated
     "do not silently swallow exceptions" rule is respected via logging.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from services.ai_service import facade


# ---------------------------------------------------------------------------
# Removal regression guards.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attr",
    [
        "_prefer_local_impl",
        "_allow_local_fallback",
        "_call_remote_ai",
        "_fallback_reason",
        "AI_CLIENT",
        "AI_BASE_URL",
        "_INTERNAL_AI_USER_ID",
    ],
)
def test_ai_service_http_transport_attrs_stay_deleted(attr):
    """Each of these enabled or supported the HTTP roundtrip to the
    ai-service microservice. Re-adding any of them brings the legacy
    transport back. Block at the test layer."""
    assert not hasattr(facade, attr), (
        f"{attr} was removed in Wave 10 (ai-service microservice runtime "
        f"teardown). Do not bring it back without rerouting through the "
        f"Conversation Engine first."
    )


def test_facade_does_not_import_remote_transport_helpers():
    """ServiceClient + build_internal_headers were exclusively used for the
    remote ai-service call. Their imports are gone from facade.py."""
    import ast
    from pathlib import Path

    src = Path(facade.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.add(alias.name)
    assert "ServiceClient" not in imported_names
    assert "build_internal_headers" not in imported_names
    # The ai_service_* config helpers were only consumed by AI_CLIENT setup.
    assert "ai_service_retry_attempts" not in imported_names
    assert "ai_service_timeout_seconds" not in imported_names
    assert "allow_local_ai_fallback" not in imported_names
    assert "service_urls" not in imported_names


# ---------------------------------------------------------------------------
# Public surface still works.
# ---------------------------------------------------------------------------


_REQUIRED_PUBLIC_FUNCS = (
    "analyze_sentiment",
    "classify_intent",
    "generate_combined_ai_analysis",
    "generate_lead_score",
    "generate_nurture_message",
    "auto_score_and_nurture_lead",
    "update_customer_memory",
    "summarize_conversation",
    "summarize_customer_interaction",
    "generate_daily_ai_summary",
    "generate_product_description",
    "get_company_knowledge",
    "get_active_llm_engines",
    "get_active_llm_engine",
)


@pytest.mark.parametrize("name", _REQUIRED_PUBLIC_FUNCS)
def test_facade_public_function_present(name):
    func = getattr(facade, name, None)
    assert func is not None, f"facade.{name} must remain on the public surface"
    assert asyncio.iscoroutinefunction(func), f"facade.{name} should be an async function"


# ---------------------------------------------------------------------------
# Local-impl failures degrade through a structured warning + safe default,
# they do not raise (user rule: "Do NOT silently swallow exceptions" — we
# log + downgrade rather than swallowing).
# ---------------------------------------------------------------------------


def test_analyze_sentiment_failure_logs_and_returns_safe_default(caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger="services.ai_service.facade")

    async def _boom(*_a, **_kw):
        raise RuntimeError("local sentiment crashed")

    monkeypatch.setattr(facade, "_local_analyze_sentiment", _boom)
    out = asyncio.run(facade.analyze_sentiment("hello world", company_id="co_test"))
    assert isinstance(out, dict)
    assert any("ai_facade analyze_sentiment local failed" in r.message for r in caplog.records)


def test_classify_intent_failure_logs_and_returns_safe_default(caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger="services.ai_service.facade")

    async def _boom(*_a, **_kw):
        raise RuntimeError("local intent crashed")

    monkeypatch.setattr(facade, "_local_classify_intent", _boom)
    out = asyncio.run(facade.classify_intent("hi", company_id="co_test"))
    assert isinstance(out, dict)
    assert any("ai_facade classify_intent local failed" in r.message for r in caplog.records)


def test_summarize_conversation_failure_returns_short_safe_string(caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger="services.ai_service.facade")

    async def _boom(*_a, **_kw):
        raise RuntimeError("summary crashed")

    monkeypatch.setattr(facade, "_local_summarize_conversation", _boom)
    out = asyncio.run(facade.summarize_conversation([{"role": "user", "content": "hi"}]))
    assert out == "Unable to summarize."
    assert any("ai_facade summarize_conversation local failed" in r.message for r in caplog.records)


def test_get_active_llm_engines_failure_returns_empty_list(caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger="services.ai_service.facade")

    async def _boom(*_a, **_kw):
        raise RuntimeError("engine lookup crashed")

    monkeypatch.setattr(facade, "_local_get_active_llm_engines", _boom)
    out = asyncio.run(facade.get_active_llm_engines(db=None, company_id="co_test"))
    assert out == []
    assert any("ai_facade get_active_llm_engines local failed" in r.message for r in caplog.records)
