"""Wave 7 - legacy deprecation log instrumentation.

The legacy response generator entry point is gone. The remaining warning here
tracks tenants still configured for non-Gemini providers before that dispatch
branch is removed.
"""

from __future__ import annotations

import asyncio


def test_legacy_generate_ai_response_function_is_gone():
    """Wave 8 deleted generate_ai_response. Re-introducing it would be a regression."""
    from services.ai_service import response_generator

    assert not hasattr(response_generator, "generate_ai_response"), (
        "generate_ai_response should be gone - it was deleted in Wave 8 because "
        "no production caller exercised it after Wave 6 made the conversation "
        "engine the default response generator."
    )

    from services.ai_service import facade

    assert not hasattr(facade, "generate_ai_response"), (
        "facade.generate_ai_response should be gone - wrapped a deleted helper."
    )


def test_non_gemini_provider_warning_fires_for_openai(caplog, monkeypatch):
    """Non-Gemini tenant configs should still emit a deprecation warning."""
    caplog.set_level("WARNING", logger="services.ai_service.llm_client")
    from services.ai_service import llm_client

    def _force_openai(*_a, **_kw):
        return {"provider": "openai", "model_name": "gpt-4o-mini"}

    monkeypatch.setattr(llm_client, "_text_engine", _force_openai)
    monkeypatch.setattr(llm_client, "_get_fallback_provider_order", lambda _p: ["openai"])

    try:
        asyncio.run(
            llm_client.call_model_text(
                "hello",
                engine=None,
                call_type="text",
                call_purpose="test",
            )
        )
    except Exception:
        pass

    deprecation_lines = [r for r in caplog.records if "non_gemini_provider_invoked" in r.message]
    assert deprecation_lines, "expected non_gemini_provider_invoked warning for openai dispatch"
    assert "provider=openai" in deprecation_lines[0].message


def test_non_gemini_warning_does_not_fire_for_gemini(caplog, monkeypatch):
    caplog.set_level("WARNING", logger="services.ai_service.llm_client")
    from services.ai_service import llm_client

    def _force_gemini(*_a, **_kw):
        return {"provider": "gemini", "model_name": "gemini-2.5-flash"}

    monkeypatch.setattr(llm_client, "_text_engine", _force_gemini)
    monkeypatch.setattr(llm_client, "_get_fallback_provider_order", lambda _p: ["gemini"])

    try:
        asyncio.run(
            llm_client.call_model_text(
                "hello",
                engine=None,
                call_type="text",
                call_purpose="test",
            )
        )
    except Exception:
        pass

    assert not any("non_gemini_provider_invoked" in r.message for r in caplog.records)
