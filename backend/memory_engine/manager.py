"""
memory_engine/manager.py — Central MemoryManager facade.

Unifies short-term (Redis), long-term (PostgreSQL), and semantic (pgvector)
memory into a single interface. All memory operations go through this class.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from memory_engine.long_term import LongTermMemory
from memory_engine.schemas import InteractionData, MemoryContext
from memory_engine.semantic import SemanticMemory
from memory_engine.short_term import ShortTermMemory
from shared.metrics import increment_counter

logger = logging.getLogger(__name__)


def _estimate_memory_tokens(value: Any) -> int:
    try:
        text = json.dumps(value, ensure_ascii=True, default=str, separators=(",", ":"))
    except Exception:
        text = str(value or "")
    if not text:
        return 0
    return max(1, len(text) // 4)


class MemoryManager:
    """
    Unified memory facade combining all three memory tiers.

    Usage:
        manager = MemoryManager(db)

        # Fetch complete context for AI
        context = await manager.fetch_context(user_id, tenant_id)

        # Store a new interaction
        await manager.store_interaction(interaction_data)

        # Update specific memory
        await manager.update_memory(user_id, tenant_id, memory_type="preference", ...)
    """

    def __init__(self, db=None) -> None:
        self.db = db
        self.short_term = ShortTermMemory()
        self.long_term = LongTermMemory()
        self.semantic = SemanticMemory()

    async def fetch_context(
        self,
        user_id: str,
        tenant_id: str,
        *,
        conversation_id: str = "",
        current_query: str = "",
        channel: str = "",
        include_semantic: bool = True,
    ) -> MemoryContext:
        """
        Fetch the complete memory context across all three tiers.

        This is the primary method AI services should call before
        generating responses.

        Args:
            user_id: Customer/user ID.
            tenant_id: Company/tenant ID.
            conversation_id: Current conversation ID (optional).
            current_query: The current user query for semantic search.
            channel: Channel type (for channel-specific context).
            include_semantic: Whether to run semantic search (disable for speed).

        Returns:
            MemoryContext containing short-term, long-term, and semantic context.
        """
        started_at = time.monotonic()
        if not tenant_id or not user_id:
            logger.warning(
                "memory_personalization_disabled reason=missing_scope tenant_id_present=%s user_id_present=%s conversation_id_present=%s",
                bool(tenant_id),
                bool(user_id),
                bool(conversation_id),
            )
            from memory_engine.schemas import LongTermContext, SemanticContext, ShortTermContext

            return MemoryContext(
                user_id=user_id,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                short_term=ShortTermContext(),
                long_term=LongTermContext(),
                semantic=SemanticContext(),
            )

        increment_counter(
            "memory_engine.fetch_context",
            labels={"tenant_id": tenant_id},
        )

        # Parallel fetch across all tiers.
        # Always load short-term memory even without a conversation_id; the
        # _key() method falls back to user-level keys so first-message turns
        # still get any previously cached intent/session state.
        short_term_task = self.short_term.load(tenant_id, user_id, conversation_id=conversation_id)

        tasks = [
            short_term_task,
            self.long_term.load(self.db, tenant_id, user_id, conversation_id=conversation_id),
        ]

        if include_semantic and current_query:
            tasks.append(
                self.semantic.load(
                    self.db,
                    tenant_id,
                    current_query,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    top_k=5,
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        short_term = results[0] if not isinstance(results[0], Exception) else None
        long_term = results[1] if not isinstance(results[1], Exception) else None
        semantic = results[2] if len(results) > 2 and not isinstance(results[2], Exception) else None

        # Log any errors
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                tier = ["short_term", "long_term", "semantic"][i]
                logger.warning(
                    "Memory tier %s fetch failed for user=%s tenant=%s: %s",
                    tier,
                    user_id,
                    tenant_id,
                    result,
                )

        from memory_engine.schemas import (
            LongTermContext,
            SemanticContext,
            ShortTermContext,
        )

        short_context = short_term or ShortTermContext()
        long_context = long_term or LongTermContext()
        semantic_context = semantic or SemanticContext()
        short_tokens = _estimate_memory_tokens(short_context.model_dump(mode="json"))
        long_tokens = _estimate_memory_tokens(long_context.model_dump(mode="json"))
        semantic_tokens = _estimate_memory_tokens(semantic_context.model_dump(mode="json"))
        total_tokens = short_tokens + long_tokens + semantic_tokens
        logger.info(
            "memory_hydration load_time_ms=%s tenant_id=%s user_id=%s conversation_id=%s short_term_tokens=%s long_term_tokens=%s semantic_tokens=%s total_context_tokens=%s recent_messages=%s pending_intent=%s semantic_top_k=%s",
            int((time.monotonic() - started_at) * 1000),
            tenant_id,
            user_id,
            conversation_id,
            short_tokens,
            long_tokens,
            semantic_tokens,
            total_tokens,
            len(short_context.recent_messages or []),
            bool(short_context.pending_intent),
            5 if include_semantic and current_query else 0,
        )

        return MemoryContext(
            user_id=user_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            short_term=short_context,
            long_term=long_context,
            semantic=semantic_context,
        )

    async def store_interaction(self, data: InteractionData) -> None:
        """
        Store a complete interaction across all relevant memory tiers.

        This should be called after every AI response or user message.
        """
        if not data.tenant_id or not data.user_id:
            logger.warning(
                "memory_store_interaction_skipped reason=missing_scope tenant_id_present=%s user_id_present=%s conversation_id_present=%s",
                bool(data.tenant_id),
                bool(data.user_id),
                bool(data.conversation_id),
            )
            return
        increment_counter(
            "memory_engine.store_interaction",
            labels={"tenant_id": data.tenant_id},
        )

        # Store in short-term memory
        message_data = {
            "sender_type": data.sender_type,
            "content": data.message_content,
            "timestamp": data.timestamp.isoformat(),
            "intent": data.intent,
            "channel": data.channel,
        }

        tasks: list[Any] = []
        if data.conversation_id:
            tasks.append(
                self.short_term.store_message(
                    data.tenant_id,
                    data.user_id,
                    message_data,
                    conversation_id=data.conversation_id,
                )
            )
        else:
            logger.info(
                "memory_short_term_store_skipped reason=missing_conversation_scope tenant=%s user=%s",
                data.tenant_id,
                data.user_id,
            )

        # Store intent in short-term
        if data.intent and data.conversation_id:
            tasks.append(
                self.short_term.store_intent(
                    data.tenant_id,
                    data.user_id,
                    data.intent,
                    conversation_id=data.conversation_id,
                )
            )

        # Store AI response in short-term
        if data.ai_response and data.conversation_id:
            tasks.append(
                self.short_term.store_last_ai_response(
                    data.tenant_id,
                    data.user_id,
                    data.ai_response,
                    conversation_id=data.conversation_id,
                )
            )

        # Store interaction embedding (semantic tier) — fire and forget
        if data.message_content and data.sender_type == "customer":
            tasks.append(
                self.semantic.store_interaction_embedding(
                    self.db,
                    data.tenant_id,
                    data.user_id,
                    data.message_content,
                    conversation_id=data.conversation_id,
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                increment_counter(
                    "memory_engine.store_interaction.errors",
                    labels={"tenant_id": data.tenant_id},
                )
                logger.warning(
                    "Memory store_interaction task failed for user=%s tenant=%s: %s",
                    data.user_id,
                    data.tenant_id,
                    result,
                )

    async def update_memory(
        self,
        user_id: str,
        tenant_id: str,
        *,
        memory_type: str,
        content: dict[str, Any],
        conversation_id: str = "",
    ) -> None:
        """
        Update a specific memory type.

        Supported types:
            - conversation_state: Update conversation stage/intent
            - pending_intent: Store the latest classified intent for the active turn
            - preference: Store a user preference
            - customer_summary: Update long-term summary
            - shown_products: Update shown product list
        """
        if memory_type == "conversation_state":
            await self.short_term.store_conversation_state(tenant_id, user_id, content, conversation_id=conversation_id)

        elif memory_type == "pending_intent":
            await self.short_term.store_intent(tenant_id, user_id, content, conversation_id=conversation_id)

        elif memory_type == "preference":
            for key, value in content.items():
                await self.long_term.store_preference(self.db, tenant_id, user_id, key, str(value))

        elif memory_type == "customer_summary":
            await self.long_term.update_customer_summary(
                self.db,
                tenant_id,
                user_id,
                content.get("summary", ""),
                content.get("sentiment", ""),
            )
            # Also update the semantic profile embedding
            summary = content.get("summary", "")
            if summary:
                await self.semantic.store_customer_profile_embedding(self.db, tenant_id, user_id, summary)

        elif memory_type == "interaction_summary":
            await self.long_term.store_interaction_summary(
                self.db,
                tenant_id,
                user_id,
                content,
                conversation_id=conversation_id,
            )

        elif memory_type == "shown_products":
            await self.long_term.store_shown_products(
                self.db,
                tenant_id,
                user_id,
                list(content.get("product_ids") or []),
                conversation_id=conversation_id,
            )

        else:
            logger.warning("Unknown memory type: %s", memory_type)

    async def invalidate_short_term(
        self,
        user_id: str,
        tenant_id: str,
        *,
        conversation_id: str = "",
    ) -> None:
        """Clear short-term memory for a user (e.g., on conversation close)."""
        await self.short_term.invalidate(tenant_id, user_id, conversation_id=conversation_id)
