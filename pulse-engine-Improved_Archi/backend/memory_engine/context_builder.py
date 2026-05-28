"""
memory_engine/context_builder.py — Prompt context assembler.

Merges short-term, long-term, and semantic memory into a token-budgeted
PromptContext ready for AI consumption.

This replaces the scattered context assembly in response_generator.py
with a single, clean pipeline.
"""

from __future__ import annotations

import logging
import os as _os
from typing import Any

from memory_engine.manager import MemoryManager
from memory_engine.schemas import MemoryContext, PromptContext
from memory_engine.summarizer import (
    estimate_tokens,
    summarize_customer_profile,
    summarize_knowledge_context,
    summarize_messages,
    truncate_to_token_budget,
)

logger = logging.getLogger(__name__)

# Token budgets per section (configurable via env for fine-tuning)
_BUDGET_CONVERSATION_HISTORY = int(_os.environ.get("MEMORY_BUDGET_CONVERSATION", "2000") or 2000)
_BUDGET_CUSTOMER_SUMMARY = int(_os.environ.get("MEMORY_BUDGET_CUSTOMER", "600") or 600)
_BUDGET_KNOWLEDGE = int(_os.environ.get("MEMORY_BUDGET_KNOWLEDGE", "800") or 800)
_BUDGET_PRODUCTS = int(_os.environ.get("MEMORY_BUDGET_PRODUCTS", "400") or 400)
_BUDGET_STATE = int(_os.environ.get("MEMORY_BUDGET_STATE", "250") or 250)
_BUDGET_SYSTEM = int(_os.environ.get("MEMORY_BUDGET_SYSTEM", "350") or 350)

_TOTAL_BUDGET = (
    _BUDGET_CONVERSATION_HISTORY
    + _BUDGET_CUSTOMER_SUMMARY
    + _BUDGET_KNOWLEDGE
    + _BUDGET_PRODUCTS
    + _BUDGET_STATE
    + _BUDGET_SYSTEM
)


class ContextBuilder:
    """
    Assembles complete AI prompt context from memory.

    Usage:
        builder = ContextBuilder(memory_manager)
        prompt_context = await builder.build_prompt_context(
            user_id=customer_id,
            tenant_id=company_id,
            current_query=query,
            conversation_id=conversation_id,
        )
        # prompt_context.to_prompt_string() → ready for LLM
    """

    def __init__(self, memory_manager: MemoryManager) -> None:
        self._memory = memory_manager

    async def build_prompt_context(
        self,
        user_id: str,
        tenant_id: str,
        *,
        current_query: str = "",
        conversation_id: str = "",
        conversation_context: list[dict[str, Any]] | None = None,
        system_instruction: str = "",
        channel: str = "",
    ) -> PromptContext:
        """
        Build a complete, token-budgeted prompt context.

        Steps:
            1. Fetch memory context (parallel across all tiers)
            2. Merge and prioritize
            3. Apply token budgets
            4. Return assembled PromptContext

        Args:
            user_id: Customer ID.
            tenant_id: Company ID.
            current_query: Current user message for semantic search.
            conversation_id: Current conversation ID.
            conversation_context: Override conversation messages (skip Redis).
            system_instruction: Additional system prompt instructions.
            channel: Channel type for channel-specific context.

        Returns:
            PromptContext with all sections token-budgeted and ready.
        """
        if not tenant_id or not user_id:
            logger.warning(
                "prompt_context_personalization_disabled reason=missing_scope tenant_id_present=%s user_id_present=%s conversation_id_present=%s",
                bool(tenant_id),
                bool(user_id),
                bool(conversation_id),
            )
            return PromptContext()

        # Fetch full memory context
        memory = await self._memory.fetch_context(
            user_id,
            tenant_id,
            conversation_id=conversation_id,
            current_query=current_query,
            channel=channel,
        )

        # Build each prompt section
        conversation_history = self._build_conversation_section(memory, conversation_context)
        customer_summary = self._build_customer_section(memory)
        knowledge_context = self._build_knowledge_section(memory)
        products = self._build_products_section(memory)
        state = self._build_state_section(memory)
        system = self._build_system_section(memory, system_instruction)

        # Calculate total estimated tokens
        total_tokens = sum(
            estimate_tokens(text)
            for text in (
                conversation_history,
                customer_summary,
                knowledge_context,
                products,
                state,
                system,
            )
        )

        return PromptContext(
            system_context=system,
            conversation_history=conversation_history,
            customer_summary=customer_summary,
            knowledge_context=knowledge_context,
            recent_products=products,
            conversation_state_summary=state,
            total_estimated_tokens=total_tokens,
        )

    def _build_conversation_section(
        self,
        memory: MemoryContext,
        override_messages: list[dict[str, Any]] | None = None,
    ) -> str:
        """Build the conversation history section."""
        messages = override_messages or memory.short_term.recent_messages
        if not messages:
            return ""
        return summarize_messages(messages, max_tokens=_BUDGET_CONVERSATION_HISTORY)

    def _build_customer_section(self, memory: MemoryContext) -> str:
        """Build the customer profile summary section."""
        if not memory.long_term.customer_profile:
            return ""

        profile = summarize_customer_profile(
            memory.long_term.customer_profile,
            max_tokens=_BUDGET_CUSTOMER_SUMMARY // 2,
        )

        # Add preferences
        prefs = memory.long_term.preferences
        if prefs:
            pref_text = ", ".join(f"{k}: {v}" for k, v in list(prefs.items())[:5])
            profile += f"\nPreferences: {pref_text}"

        # Add intent history
        if memory.long_term.intent_history:
            recent_intents = ", ".join(memory.long_term.intent_history[:5])
            profile += f"\nRecent intents: {recent_intents}"

        # Add sentiment trend
        if memory.long_term.sentiment_trend:
            trend_labels = [s.get("label", "") for s in memory.long_term.sentiment_trend[:3]]
            profile += f"\nSentiment trend: {', '.join(trend_labels)}"

        return truncate_to_token_budget(profile, _BUDGET_CUSTOMER_SUMMARY)

    def _build_knowledge_section(self, memory: MemoryContext) -> str:
        """Build the knowledge base context section."""
        if not memory.semantic.knowledge_text:
            return ""
        return summarize_knowledge_context(
            memory.semantic.knowledge_text,
            max_tokens=_BUDGET_KNOWLEDGE,
        )

    def _build_products_section(self, memory: MemoryContext) -> str:
        """Build the recent products section."""
        if not memory.long_term.shown_product_ids:
            return ""
        product_ids = memory.long_term.shown_product_ids[:5]
        return truncate_to_token_budget(
            f"Previously shown products: {', '.join(product_ids)}",
            _BUDGET_PRODUCTS,
        )

    def _build_state_section(self, memory: MemoryContext) -> str:
        """Build the conversation state summary section."""
        state = memory.short_term.conversation_state
        if not state:
            return ""

        parts: list[str] = []
        if state.get("stage"):
            parts.append(f"Stage: {state['stage']}")
        if state.get("intent"):
            parts.append(f"Intent: {state['intent']}")
        if state.get("turn_count"):
            parts.append(f"Turn: {state['turn_count']}")
        if state.get("next_action"):
            parts.append(f"Next: {state['next_action']}")
        if state.get("transition_note"):
            parts.append(state["transition_note"])

        return truncate_to_token_budget(" | ".join(parts), _BUDGET_STATE)

    def _build_system_section(
        self,
        memory: MemoryContext,
        additional_instruction: str = "",
    ) -> str:
        """Build the system context section."""
        parts: list[str] = []

        if additional_instruction:
            parts.append(additional_instruction)

        # Add long-term summary if available
        if memory.long_term.long_term_summary:
            parts.append(f"Customer context: {memory.long_term.long_term_summary}")

        # Add last AI response for continuity
        if memory.short_term.last_ai_response:
            parts.append(f"Your last response: {memory.short_term.last_ai_response[:200]}")

        return truncate_to_token_budget("\n".join(parts), _BUDGET_SYSTEM)
