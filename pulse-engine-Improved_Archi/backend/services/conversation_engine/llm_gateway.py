"""Gemini gateway with exponential-backoff retry.

Wraps `services.ai_service.llm_client.call_gemini` so the orchestrator can
ask for a completion without dealing with the underlying client's retry
contract. We retry transient failures only (network, timeout, rate-limit
exceptions surfaced by the SDK); validation errors propagate immediately.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from services.ai_service.llm_client import call_gemini


logger = logging.getLogger(__name__)

_DEFAULT_RETRY_DELAYS = (1.0, 2.0, 4.0)


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
                text = await call_gemini(prompt, use_pro=self._use_pro, engine=engine)
                return GenerationResult(text=text or "", attempts=attempt)
            except Exception as exc:  # noqa: BLE001 — call site decides what to do
                last_error = type(exc).__name__ + ": " + str(exc)
                logger.warning("llm_gateway_attempt_failed attempt=%s error=%s", attempt, last_error)
        return GenerationResult(text="", attempts=len(self._retry_delays) + 1, last_error=last_error)
