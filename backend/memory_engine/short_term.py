"""
memory_engine/short_term.py — Redis-backed short-term memory.

Stores recent conversation messages, active session context,
and pending intent state. Data expires after configurable TTL.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from memory_engine.schemas import ShortTermContext
from shared.cache import get_cache_client

logger = logging.getLogger(__name__)

_DEFAULT_TTL = max(
    60,
    int(os.environ.get("MEMORY_SHORT_TERM_TTL_SECONDS", "1800") or 1800),
)  # default 30 minutes; was 5 minutes which is too short for multi-turn conversations
_MAX_RECENT_MESSAGES = max(
    10,
    int(os.environ.get("MEMORY_SHORT_TERM_MAX_MESSAGES", "20") or 20),
)


class ShortTermMemory:
    """
    Fast-access memory layer backed by Redis.

    Stores:
        - Recent conversation messages (last N messages)
        - Active session state (current intent, conversation stage)
        - Last AI response for deduplication
    """

    def __init__(self, namespace: str = "memory_engine_st") -> None:
        self._cache = get_cache_client(namespace=namespace)

    def _key(self, tenant_id: str, user_id: str, suffix: str) -> str:
        return f"{tenant_id}:{user_id}:{suffix}"

    async def load(
        self,
        tenant_id: str,
        user_id: str,
        *,
        conversation_id: str = "",
    ) -> ShortTermContext:
        """Load all short-term context for a user."""
        discriminator = conversation_id or user_id

        recent_messages = await self._get_recent_messages(tenant_id, discriminator)
        active_session = await self._get_json(tenant_id, discriminator, "session")
        pending_intent = await self._get_json(tenant_id, discriminator, "intent")
        last_ai_response = await self._get_string(tenant_id, discriminator, "last_ai")
        conversation_state = await self._get_json(tenant_id, discriminator, "conv_state")

        return ShortTermContext(
            recent_messages=recent_messages,
            active_session=active_session,
            pending_intent=pending_intent,
            last_ai_response=last_ai_response,
            conversation_state=conversation_state,
        )

    async def store_message(
        self,
        tenant_id: str,
        user_id: str,
        message: dict[str, Any],
        *,
        conversation_id: str = "",
    ) -> None:
        """Append a message to the recent messages list."""
        discriminator = conversation_id or user_id
        key = self._key(tenant_id, discriminator, "messages")

        existing = await self._cache.get_json(key)
        messages: list[dict] = existing if isinstance(existing, list) else []
        messages.append(message)

        # Keep only the most recent messages
        if len(messages) > _MAX_RECENT_MESSAGES:
            messages = messages[-_MAX_RECENT_MESSAGES:]

        await self._cache.set_json(key, messages, ttl_seconds=_DEFAULT_TTL)

    async def store_session(
        self,
        tenant_id: str,
        user_id: str,
        session_data: dict[str, Any],
        *,
        conversation_id: str = "",
    ) -> None:
        """Store active session context."""
        discriminator = conversation_id or user_id
        key = self._key(tenant_id, discriminator, "session")
        await self._cache.set_json(key, session_data, ttl_seconds=_DEFAULT_TTL)

    async def store_intent(
        self,
        tenant_id: str,
        user_id: str,
        intent_data: dict[str, Any],
        *,
        conversation_id: str = "",
    ) -> None:
        """Store pending intent state."""
        discriminator = conversation_id or user_id
        key = self._key(tenant_id, discriminator, "intent")
        await self._cache.set_json(key, intent_data, ttl_seconds=_DEFAULT_TTL)

    async def store_last_ai_response(
        self,
        tenant_id: str,
        user_id: str,
        response_text: str,
        *,
        conversation_id: str = "",
    ) -> None:
        """Store the last AI response for deduplication."""
        discriminator = conversation_id or user_id
        key = self._key(tenant_id, discriminator, "last_ai")
        await self._cache.set_json(key, {"text": response_text}, ttl_seconds=_DEFAULT_TTL)

    async def store_conversation_state(
        self,
        tenant_id: str,
        user_id: str,
        state: dict[str, Any],
        *,
        conversation_id: str = "",
    ) -> None:
        """Store conversation state (stage, intent, turn count)."""
        discriminator = conversation_id or user_id
        key = self._key(tenant_id, discriminator, "conv_state")
        await self._cache.set_json(key, state, ttl_seconds=_DEFAULT_TTL)

    async def invalidate(
        self,
        tenant_id: str,
        user_id: str,
        *,
        conversation_id: str = "",
    ) -> None:
        """Clear all short-term memory for a user/conversation."""
        discriminator = conversation_id or user_id
        for suffix in ("messages", "session", "intent", "last_ai", "conv_state"):
            key = self._key(tenant_id, discriminator, suffix)
            try:
                await self._cache.delete(key)
            except Exception:
                pass

    # ── Internal helpers ─────────────────────────────────────────────────

    async def _get_recent_messages(self, tenant_id: str, discriminator: str) -> list[dict]:
        key = self._key(tenant_id, discriminator, "messages")
        data = await self._cache.get_json(key)
        return data if isinstance(data, list) else []

    async def _get_json(self, tenant_id: str, discriminator: str, suffix: str) -> dict:
        key = self._key(tenant_id, discriminator, suffix)
        data = await self._cache.get_json(key)
        return data if isinstance(data, dict) else {}

    async def _get_string(self, tenant_id: str, discriminator: str, suffix: str) -> str:
        key = self._key(tenant_id, discriminator, suffix)
        data = await self._cache.get_json(key)
        if isinstance(data, dict):
            return str(data.get("text") or "").strip()
        return ""
