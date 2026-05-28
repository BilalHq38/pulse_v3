"""Gemini gateway with exponential-backoff retry.

Wraps `services.ai_service.llm_client.call_model_text` so the orchestrator
can ask for a completion without dealing with the underlying client's retry
contract. We retry transient failures only (network, timeout, rate-limit
exceptions surfaced by the SDK); validation errors propagate immediately.

count_against_budget=False is intentional: the engine has its own retry
contract and must not be blocked by the per-message LLM budget tracker that
the legacy capture-agent sets via llm_tracking.set_llm_context(max_calls=1).
Both calls share the same async ContextVar, so without this flag the engine's
first generate() call would see budget_used(1) >= max_calls(1) and raise
AIBudgetExceededError, which the gateway catches silently and returns text="".
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from services.ai_service.llm_client import call_model_text


logger = logging.getLogger(__name__)

_DEFAULT_RETRY_DELAYS = (0.2,)


@dataclass
class GenerationResult:
    text: str
    attempts: int
    last_error: str = ""


class LlmGateway:
    def __init__(self, *, retry_delays: tuple[float, ...] = _DEFAULT_RETRY_DELAYS, use_pro: bool = False) -> None:
        self._retry_delays = retry_delays
        self._use_pro = use_pro

    async def generate(self, prompt: str, *, engine: dict | None = None) -> GenerationResult:
        last_error = ""
        for attempt, delay in enumerate([0.0, *self._retry_delays], start=1):
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                text = await call_model_text(
                    prompt,
                    engine=engine,
                    use_pro=self._use_pro,
                    # Do not count against the per-message LLM budget — the
                    # legacy capture-agent sets max_calls=1 and the budget is
                    # shared via a ContextVar in the same async task. Without
                    # this flag every engine call fails with AIBudgetExceededError.
                    count_against_budget=False,
                )
                return GenerationResult(text=text or "", attempts=attempt)
            except Exception as exc:  # noqa: BLE001 — call site decides what to do
                last_error = type(exc).__name__ + ": " + str(exc)
                logger.warning("llm_gateway_attempt_failed attempt=%s error=%s", attempt, last_error)
        return GenerationResult(text="", attempts=len(self._retry_delays) + 1, last_error=last_error)
