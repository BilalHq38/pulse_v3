"""Gemini gateway with model fallback and inference summary logging.

The conversation engine owns retry behavior at this layer. It calls the shared
AI client with a Vertex Gemini Flash default, explicit temperature/token config,
and count_against_budget=False so the legacy per-message budget context does not
block the conversation engine's single generation call.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from services.ai_service.common import estimate_tokens
from services.ai_runtime.llm_client import call_model_text
from shared.config import (
    ai_api_call_timeout_seconds,
    ai_max_tokens,
    ai_temperature,
    gemini_fallback_models,
    gemini_flash_model_name,
)


logger = logging.getLogger(__name__)

_DEFAULT_RETRY_DELAYS = (0.2,)
_GEMINI_PROVIDERS = {"gemini", "gemini_api", "vertex_ai"}


@dataclass
class GenerationResult:
    text: str
    attempts: int
    last_error: str = ""
    model_used: str = ""
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    fallback_triggered: bool = False


class LlmGateway:
    def __init__(self, *, retry_delays: tuple[float, ...] = _DEFAULT_RETRY_DELAYS, use_pro: bool = False) -> None:
        self._retry_delays = retry_delays
        self._use_pro = use_pro

    def _base_engine(self, engine: dict | None = None) -> dict:
        selected = dict(engine or {})
        provider = str(selected.get("provider") or "vertex_ai").strip().lower()
        selected["provider"] = provider
        if provider in _GEMINI_PROVIDERS and not str(selected.get("model_name") or "").strip():
            selected["model_name"] = gemini_flash_model_name()
        selected.setdefault("temperature", ai_temperature())
        selected.setdefault("max_tokens", ai_max_tokens())
        return selected

    def _model_attempts(self, engine: dict | None = None) -> list[dict]:
        primary = self._base_engine(engine)
        provider = str(primary.get("provider") or "").strip().lower()
        if provider not in _GEMINI_PROVIDERS:
            return [primary]
        attempts = [primary]
        seen = {str(primary.get("model_name") or "").strip()}
        for model_name in gemini_fallback_models():
            if not model_name or model_name in seen:
                continue
            fallback = dict(primary)
            fallback["model_name"] = model_name
            attempts.append(fallback)
            seen.add(model_name)
        return attempts

    async def generate(self, prompt: str, *, engine: dict | None = None) -> GenerationResult:
        last_error = ""
        attempts = 0
        model_attempts = self._model_attempts(engine)
        for model_index, attempt_engine in enumerate(model_attempts):
            model_name = str(attempt_engine.get("model_name") or "")
            fallback_triggered = model_index > 0
            for delay in [0.0, *self._retry_delays]:
                attempts += 1
                if delay > 0:
                    await asyncio.sleep(delay)
                started = time.perf_counter()
                try:
                    text = await asyncio.wait_for(
                        call_model_text(
                            prompt,
                            engine=attempt_engine,
                            use_pro=self._use_pro,
                            generation_config={
                                "temperature": float(attempt_engine.get("temperature") or ai_temperature()),
                                "max_output_tokens": int(attempt_engine.get("max_tokens") or ai_max_tokens()),
                            },
                            call_purpose="conversation_engine_inference",
                            function_name="conversation_engine.generate",
                            max_provider_attempts=1,
                            allow_provider_fallback=False,
                            count_against_budget=False,
                        ),
                        timeout=ai_api_call_timeout_seconds(),
                    )
                    latency_ms = int((time.perf_counter() - started) * 1000)
                    answer = text or ""
                    prompt_tokens = estimate_tokens(prompt)
                    completion_tokens = estimate_tokens(answer)
                    total_tokens = prompt_tokens + completion_tokens
                    logger.info(
                        "llm_inference model_used=%s response_time_ms=%s token_count=%s fallback_triggered=%s",
                        model_name,
                        latency_ms,
                        total_tokens,
                        str(fallback_triggered).lower(),
                    )
                    return GenerationResult(
                        text=answer,
                        attempts=attempts,
                        model_used=model_name,
                        latency_ms=latency_ms,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        fallback_triggered=fallback_triggered,
                    )
                except Exception as exc:  # noqa: BLE001 - caller decides fallback response
                    last_error = type(exc).__name__ + ": " + str(exc)
                    logger.warning(
                        "llm_gateway_attempt_failed attempt=%s model=%s fallback_triggered=%s error=%s",
                        attempts,
                        model_name,
                        str(fallback_triggered).lower(),
                        last_error,
                    )
                    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) and model_index + 1 < len(model_attempts):
                        break
        return GenerationResult(text="", attempts=attempts, last_error=last_error)
