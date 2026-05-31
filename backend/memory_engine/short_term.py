"""
memory_engine/short_term.py — Redis-backed short-term memory.

Stores recent conversation messages, active session context,
and pending intent state. Data expires after configurable TTL.
"""

from __future__ import annotations

import asyncio
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

    def _key(self, tenant_id: str, user_id: str, suffix: str, *, conversation_id: str = "") -> str:
        user_scope = str(user_id or "").strip()
        conversation_scope = str(conversation_id or "").strip()
        if conversation_scope:
            return f"{tenant_id}:user:{user_scope}:conversation:{conversation_scope}:{suffix}"
        return f"{tenant_id}:user:{user_scope}:{suffix}"

    async def load(
        self,
        tenant_id: str,
        user_id: str,
        *,
        conversation_id: str = "",
    ) -> ShortTermContext:
        """Load all short-term context for a user."""
        (
            recent_messages,
            active_session,
            pending_intent,
            last_ai_response,
            conversation_state,
        ) = await asyncio.gather(
            self._get_recent_messages(tenant_id, user_id, conversation_id=conversation_id),
            self._get_json(tenant_id, user_id, "session", conversation_id=conversation_id),
            self._get_json(tenant_id, user_id, "intent", conversation_id=conversation_id),
            self._get_string(tenant_id, user_id, "last_ai", conversation_id=conversation_id),
            self._get_json(tenant_id, user_id, "conv_state", conversation_id=conversation_id),
        )

        return ShortTermContext(
            recent_messages=list(recent_messages or [])[-_MAX_RECENT_MESSAGES:],
            active_session=active_session,
            pending_intent=pending_intent,
            last_ai_response=last_ai_response,
            conversation_state=conversation_state,
            ttl_seconds=_DEFAULT_TTL,
        )

    async def store_message(
        self,
        tenant_id: str,
        user_id: str,
        message: dict[str, Any],
        *,
        conversation_id: str = "",
    ) -> None:
        """Append a message to the recent messages list (atomic RPUSH+LTRIM)."""
        key = self._key(tenant_id, user_id, "messages", conversation_id=conversation_id)
        await self._cache.append_to_list(
            key,
            message,
            max_length=_MAX_RECENT_MESSAGES,
            ttl_seconds=_DEFAULT_TTL,
        )

    async def store_session(
        self,
        tenant_id: str,
        user_id: str,
        session_data: dict[str, Any],
        *,
        conversation_id: str = "",
    ) -> None:
        """Store active session context."""
        key = self._key(tenant_id, user_id, "session", conversation_id=conversation_id)
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
        key = self._key(tenant_id, user_id, "intent", conversation_id=conversation_id)
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
        key = self._key(tenant_id, user_id, "last_ai", conversation_id=conversation_id)
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
        key = self._key(tenant_id, user_id, "conv_state", conversation_id=conversation_id)
        await self._cache.set_json(key, state, ttl_seconds=_DEFAULT_TTL)

    async def invalidate(
        self,
        tenant_id: str,
        user_id: str,
        *,
        conversation_id: str = "",
    ) -> None:
        """Clear all short-term memory for a user/conversation."""
        for suffix in ("messages", "session", "intent", "last_ai", "conv_state"):
            key = self._key(tenant_id, user_id, suffix, conversation_id=conversation_id)
            try:
                await self._cache.delete(key)
            except Exception:
                pass

    # ── Internal helpers ─────────────────────────────────────────────────

    async def _get_recent_messages(self, tenant_id: str, user_id: str, *, conversation_id: str = "") -> list[dict]:
        key = self._key(tenant_id, user_id, "messages", conversation_id=conversation_id)
        get_list = getattr(self._cache, "get_list", None)
        if get_list:
            data = await get_list(key)
            return data if isinstance(data, list) else []
        get_json = getattr(self._cache, "get_json", None)
        if get_json:
            data = await get_json(key)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return data["items"]
        return []

    async def _get_json(self, tenant_id: str, user_id: str, suffix: str, *, conversation_id: str = "") -> dict:
        key = self._key(tenant_id, user_id, suffix, conversation_id=conversation_id)
        data = await self._cache.get_json(key)
        return data if isinstance(data, dict) else {}

    async def _get_string(self, tenant_id: str, user_id: str, suffix: str, *, conversation_id: str = "") -> str:
        key = self._key(tenant_id, user_id, suffix, conversation_id=conversation_id)
        data = await self._cache.get_json(key)
        if isinstance(data, dict):
            return str(data.get("text") or "").strip()
        return ""
