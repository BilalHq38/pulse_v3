from __future__ import annotations

import asyncio
import logging

import pytest

from services.conversation_engine import llm_gateway as gateway_mod
from services.conversation_engine.llm_gateway import LlmGateway
from shared import config


def test_llm_inference_defaults_are_vertex_flash_25s_temperature_and_tokens(monkeypatch) -> None:
    monkeypatch.delenv("AI_API_CALL_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("AI_TEMPERATURE", raising=False)
    monkeypatch.delenv("AI_MAX_TOKENS", raising=False)
    monkeypatch.delenv("GEMINI_FLASH_MODEL", raising=False)

    assert config.ai_api_call_timeout_seconds() == 25.0
    assert config.ai_temperature() == 0.7
    assert config.ai_max_tokens() == 2048
    assert config.gemini_flash_model_name() == "gemini-2.5-flash"


def test_gemini_text_timeout_uses_ai_api_call_timeout(monkeypatch) -> None:
    import services.ai_service.llm_client as llm_client

    monkeypatch.delenv("GEMINI_TEXT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("AI_API_CALL_TIMEOUT_SECONDS", "25")

    assert llm_client._gemini_text_timeout_seconds() == 25.0


@pytest.mark.asyncio
async def test_gateway_sends_prompt_to_vertex_flash_with_generation_config(monkeypatch, caplog) -> None:
    captured = {}

    async def fake_call_model_text(prompt, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        return "Customer-facing answer"

    monkeypatch.delenv("AI_TEMPERATURE", raising=False)
    monkeypatch.delenv("AI_MAX_TOKENS", raising=False)
    monkeypatch.delenv("GEMINI_FLASH_MODEL", raising=False)
    monkeypatch.setattr(gateway_mod, "call_model_text", fake_call_model_text)
    caplog.set_level(logging.INFO, logger="services.conversation_engine.llm_gateway")

    result = await LlmGateway(retry_delays=()).generate("Prompt body")

    assert result.text == "Customer-facing answer"
    assert captured["prompt"] == "Prompt body"
    assert captured["engine"]["provider"] == "vertex_ai"
    assert captured["engine"]["model_name"] == "gemini-2.5-flash"
    assert captured["generation_config"]["temperature"] == 0.7
    assert captured["generation_config"]["max_output_tokens"] == 2048
    assert captured["allow_provider_fallback"] is False
    assert captured["max_provider_attempts"] == 1
    assert result.model_used == "gemini-2.5-flash"
    assert result.total_tokens > 0
    assert "llm_inference model_used=gemini-2.5-flash" in caplog.text
    assert "response_time_ms=" in caplog.text
    assert "token_count=" in caplog.text
    assert "fallback_triggered=false" in caplog.text


@pytest.mark.asyncio
async def test_gateway_fallback_model_triggers_when_primary_times_out(monkeypatch, caplog) -> None:
    calls = []

    async def fake_call_model_text(_prompt, **kwargs):
        model_name = kwargs["engine"]["model_name"]
        calls.append(model_name)
        if model_name == "gemini-2.5-flash":
            raise asyncio.TimeoutError("primary timed out")
        return "Fallback model answer"

    monkeypatch.delenv("GEMINI_FLASH_MODEL", raising=False)
    monkeypatch.setattr(gateway_mod, "call_model_text", fake_call_model_text)
    monkeypatch.setattr(gateway_mod, "gemini_fallback_models", lambda: ["gemini-2.5-flash-lite"])
    caplog.set_level(logging.INFO, logger="services.conversation_engine.llm_gateway")

    result = await LlmGateway(retry_delays=()).generate("Prompt body")

    assert calls == ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
    assert result.text == "Fallback model answer"
    assert result.model_used == "gemini-2.5-flash-lite"
    assert result.fallback_triggered is True
    assert "fallback_triggered=true" in caplog.text
