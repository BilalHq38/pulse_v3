from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class AIBudgetExceededError(RuntimeError):
    def __init__(self, *, budget_type: str, used: int, limit: int) -> None:
        self.budget_type = budget_type
        self.used = used
        self.limit = limit
        super().__init__(f"AI_BUDGET_EXCEEDED type={budget_type} used={used} limit={limit}")


@dataclass
class LLMCallContext:
    message_id: str = ""
    conversation_id: str = ""
    workflow_id: str = ""
    company_id: str = ""
    agent_name: str = ""
    max_calls: int = 2
    max_embedding_calls: int = 1
    call_count: int = 0
    embedding_call_count: int = 0
    total_ai_api_call_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


_llm_context: ContextVar[LLMCallContext | None] = ContextVar("llm_call_context", default=None)


def set_llm_context(**kwargs: Any) -> Token:
    return _llm_context.set(LLMCallContext(**kwargs))


def reset_llm_context(token: Token) -> None:
    _llm_context.reset(token)


def get_llm_context() -> LLMCallContext | None:
    return _llm_context.get()


def set_current_agent(agent_name: str) -> None:
    context = _llm_context.get()
    if context is not None:
        context.agent_name = str(agent_name or "")


def reserve_llm_call(
    *,
    agent_name: str = "",
    function_name: str = "",
    provider: str = "",
    model: str = "",
    call_purpose: str = "",
    call_type: str = "text",
    attempt_number: int = 1,
    fallback_used: bool = False,
    token_estimate: int = 0,
    count_against_budget: bool = True,
) -> int:
    context = _llm_context.get()
    if context is None or not count_against_budget:
        return 0
    if context.call_count >= context.max_calls:
        logger.warning(
            "llm_call_budget_exceeded message_id=%s conversation_id=%s workflow_id=%s "
            "company_id=%s agent=%s function=%s provider=%s model=%s purpose=%s "
            "call_type=%s attempt=%s fallback_used=%s call_count=%s max_calls=%s token_estimate=%s",
            context.message_id or "-",
            context.conversation_id or "-",
            context.workflow_id or "-",
            context.company_id or "-",
            agent_name or context.agent_name or "-",
            function_name or "-",
            provider or "-",
            model or "-",
            call_purpose or "-",
            call_type or "-",
            attempt_number,
            bool(fallback_used),
            context.call_count,
            context.max_calls,
            token_estimate,
        )
        raise AIBudgetExceededError(budget_type="llm", used=context.call_count, limit=context.max_calls)
    context.call_count += 1
    context.total_ai_api_call_count += 1
    return context.call_count


def has_llm_budget_remaining(required: int = 1) -> bool:
    context = _llm_context.get()
    if context is None:
        return True
    return context.call_count + max(1, int(required or 1)) <= context.max_calls


def has_embedding_budget_remaining(required: int = 1) -> bool:
    context = _llm_context.get()
    if context is None:
        return True
    return context.embedding_call_count + max(1, int(required or 1)) <= context.max_embedding_calls


def get_ai_usage_snapshot() -> dict[str, int | str]:
    context = _llm_context.get()
    if context is None:
        return {
            "message_id": "",
            "conversation_id": "",
            "workflow_id": "",
            "company_id": "",
            "llm_call_count": 0,
            "embedding_call_count": 0,
            "total_ai_api_call_count": 0,
            "max_llm_calls": 0,
            "max_embedding_calls": 0,
        }
    return {
        "message_id": context.message_id,
        "conversation_id": context.conversation_id,
        "workflow_id": context.workflow_id,
        "company_id": context.company_id,
        "llm_call_count": context.call_count,
        "embedding_call_count": context.embedding_call_count,
        "total_ai_api_call_count": context.total_ai_api_call_count,
        "max_llm_calls": context.max_calls,
        "max_embedding_calls": context.max_embedding_calls,
    }


def reserve_embedding_call(
    *,
    function_name: str = "generate_embedding",
    provider: str = "",
    model: str = "",
    call_purpose: str = "embedding",
    attempt_number: int = 1,
    fallback_used: bool = False,
    token_estimate: int = 0,
) -> int:
    context = _llm_context.get()
    if context is None:
        return 0
    if context.embedding_call_count >= context.max_embedding_calls:
        logger.warning(
            "embedding_budget_exceeded message_id=%s conversation_id=%s workflow_id=%s "
            "company_id=%s agent=%s function=%s provider=%s model=%s purpose=%s "
            "attempt=%s fallback_used=%s embedding_call_count=%s max_embedding_calls=%s token_estimate=%s",
            context.message_id or "-",
            context.conversation_id or "-",
            context.workflow_id or "-",
            context.company_id or "-",
            context.agent_name or "-",
            function_name or "-",
            provider or "-",
            model or "-",
            call_purpose or "-",
            attempt_number,
            bool(fallback_used),
            context.embedding_call_count,
            context.max_embedding_calls,
            token_estimate,
        )
        raise AIBudgetExceededError(
            budget_type="embedding",
            used=context.embedding_call_count,
            limit=context.max_embedding_calls,
        )
    context.embedding_call_count += 1
    context.total_ai_api_call_count += 1
    return context.embedding_call_count


def log_llm_reuse(
    *,
    agent_name: str = "",
    function_name: str = "",
    call_purpose: str = "",
    provider: str = "",
    model: str = "",
) -> None:
    context = _llm_context.get()
    logger.info(
        "llm_response_reused message_id=%s conversation_id=%s workflow_id=%s "
        "company_id=%s agent=%s function=%s provider=%s model=%s purpose=%s reused=true call_count=%s",
        (context.message_id if context else "") or "-",
        (context.conversation_id if context else "") or "-",
        (context.workflow_id if context else "") or "-",
        (context.company_id if context else "") or "-",
        agent_name or (context.agent_name if context else "") or "-",
        function_name or "-",
        provider or "-",
        model or "-",
        call_purpose or "-",
        context.call_count if context else 0,
    )
