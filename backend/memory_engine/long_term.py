"""
memory_engine/long_term.py — PostgreSQL-backed persistent memory.

Stores customer profiles, interaction summaries, preferences,
intent history, and sentiment trends — all tenant-isolated.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from memory_engine.schemas import LongTermContext
from services.db_helpers import r, rs

logger = logging.getLogger(__name__)


class LongTermMemory:
    """
    Persistent memory layer backed by PostgreSQL.

    Queries existing tables (customers, context_memories, customer_profiles,
    customer_profile_preferences) — no new tables required.
    """

    async def load(
        self,
        db,
        tenant_id: str,
        user_id: str,
        *,
        conversation_id: str = "",
    ) -> LongTermContext:
        """Load all persistent memory for a customer."""
        if not db or not tenant_id or not user_id:
            return LongTermContext()

        # Parallel fetch for performance
        import asyncio

        (
            customer,
            interaction_summaries,
            preferences,
            intent_history,
            sentiment_trend,
            shown_products,
            context_memories,
        ) = await asyncio.gather(
            self._fetch_customer(db, tenant_id, user_id),
            self._fetch_interaction_summaries(db, tenant_id, user_id),
            self._fetch_preferences(db, user_id),
            self._fetch_intent_history(db, tenant_id, user_id),
            self._fetch_sentiment_trend(db, tenant_id, user_id),
            self._fetch_shown_products(db, tenant_id, user_id, conversation_id),
            self._fetch_context_memories(db, tenant_id, user_id, conversation_id),
        )

        return LongTermContext(
            customer_profile=customer,
            long_term_summary=str(customer.get("long_term_summary") or "").strip(),
            historical_sentiment=str(customer.get("historical_sentiment") or "").strip(),
            interaction_summaries=interaction_summaries,
            preferences=preferences,
            intent_history=intent_history,
            sentiment_trend=sentiment_trend,
            shown_product_ids=shown_products,
            lifetime_value=float(customer.get("lifetime_value") or 0.0),
            total_conversations=int(customer.get("total_conversations") or 0),
        )

    async def store_interaction_summary(
        self,
        db,
        tenant_id: str,
        user_id: str,
        summary: dict[str, Any],
        *,
        conversation_id: str = "",
    ) -> str:
        """Store or update an interaction summary as a context memory (upsert per conversation)."""
        from core.utils import make_id

        payload = json.dumps(summary or {}, ensure_ascii=True)

        # Upsert: update existing summary for the conversation, insert if none
        if conversation_id:
            existing_id = await db.fetchval(
                "SELECT id FROM context_memories "
                "WHERE company_id=$1 AND entity_id=$2 AND convo_id=$3 "
                "AND memory_type='interaction_summary' LIMIT 1",
                tenant_id,
                user_id,
                conversation_id,
            )
            if existing_id:
                await db.execute(
                    "UPDATE context_memories SET memory_content=$1, updated_at=NOW() WHERE id=$2",
                    payload,
                    existing_id,
                )
                return str(existing_id)

        memory_id = make_id()
        await db.execute(
            "INSERT INTO context_memories"
            "(id,company_id,convo_id,entity_id,entity_type,memory_content,memory_type,"
            "relevance_score,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,'customer',$5,'interaction_summary',0.8,NOW(),NOW())",
            memory_id,
            tenant_id,
            conversation_id,
            user_id,
            payload,
        )
        return memory_id

    async def update_customer_summary(
        self,
        db,
        tenant_id: str,
        user_id: str,
        summary: str,
        sentiment: str = "",
    ) -> None:
        """Update the customer's long-term summary and sentiment."""
        if not db or not user_id:
            return
        try:
            await db.execute(
                "UPDATE customers SET long_term_summary=$1, historical_sentiment=$2, updated_at=NOW() WHERE id=$3",
                summary,
                sentiment or "",
                user_id,
            )
        except Exception as exc:
            logger.warning("Failed to update customer summary: %s", exc)

    async def store_preference(
        self,
        db,
        user_id: str,
        key: str,
        value: str,
    ) -> None:
        """Store or update a customer preference."""
        if not db or not user_id or not key:
            return
        try:
            await db.execute(
                "INSERT INTO customer_profile_preferences(customer_id,pref_key,pref_value) "
                "VALUES($1,$2,$3) "
                "ON CONFLICT(customer_id,pref_key) DO UPDATE SET pref_value=$3",
                user_id,
                key.strip(),
                value.strip(),
            )
        except Exception as exc:
            logger.warning("Failed to store preference: %s", exc)

    async def store_shown_products(
        self,
        db,
        tenant_id: str,
        user_id: str,
        product_ids: list[str],
        *,
        conversation_id: str = "",
    ) -> None:
        """Store the rolling set of shown product IDs in context memory."""
        if not db or not tenant_id or not user_id:
            return

        cleaned = [str(item).strip() for item in product_ids if str(item).strip()]
        if not cleaned:
            return
        merged_ids = list(dict.fromkeys(cleaned))[:20]
        payload = json.dumps({"product_ids": merged_ids}, ensure_ascii=True)

        existing_id = await db.fetchval(
            "SELECT id FROM context_memories "
            "WHERE company_id=$1 AND entity_id=$2 AND memory_type='shown_products' "
            "AND ($3='' OR convo_id=$3) "
            "ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST LIMIT 1",
            tenant_id,
            user_id,
            conversation_id,
        )
        if existing_id:
            await db.execute(
                "UPDATE context_memories SET convo_id=$1,memory_content=$2,updated_at=NOW() WHERE id=$3",
                conversation_id,
                payload,
                existing_id,
            )
            return

        from core.utils import make_id

        await db.execute(
            "INSERT INTO context_memories"
            "(id,company_id,convo_id,entity_id,entity_type,memory_content,memory_type,relevance_score,created_at,updated_at) "  # noqa: E501
            "VALUES($1,$2,$3,$4,'customer',$5,'shown_products',0.7,NOW(),NOW())",
            make_id(),
            tenant_id,
            conversation_id,
            user_id,
            payload,
        )

    # ── Internal fetch methods ───────────────────────────────────────────

    async def _fetch_customer(self, db, tenant_id: str, user_id: str) -> dict[str, Any]:
        """Fetch customer profile from customers table (selected columns only)."""
        try:
            row = await db.fetchrow(
                "SELECT id, company_id, name, email, phone, segment, status, "
                "long_term_summary, historical_sentiment, lifetime_value, "
                "total_conversations, created_at, updated_at "
                "FROM customers WHERE company_id=$1 AND id=$2 LIMIT 1",
                tenant_id,
                user_id,
            )
            return r(row) or {}
        except Exception as exc:
            logger.debug("Customer fetch failed: %s", exc)
            return {}

    async def _fetch_interaction_summaries(self, db, tenant_id: str, user_id: str) -> list[dict]:
        """Fetch past interaction summaries from context_memories."""
        try:
            rows = await db.fetch(
                "SELECT memory_content, created_at FROM context_memories "
                "WHERE company_id=$1 AND entity_id=$2 AND memory_type='interaction_summary' "
                "ORDER BY created_at DESC LIMIT 5",
                tenant_id,
                user_id,
            )
            results = []
            for row in rs(rows or []):
                content = row.get("memory_content", "")
                try:
                    parsed = json.loads(content) if isinstance(content, str) else content
                    results.append(parsed if isinstance(parsed, dict) else {"summary": str(parsed)})
                except Exception:
                    results.append({"summary": str(content)})
            return results
        except Exception as exc:
            logger.debug("Interaction summaries fetch failed: %s", exc)
            return []

    async def _fetch_preferences(self, db, user_id: str) -> dict[str, str]:
        """Fetch customer preferences."""
        try:
            rows = await db.fetch(
                "SELECT pref_key, pref_value FROM customer_profile_preferences WHERE customer_id=$1",
                user_id,
            )
            return {str(row["pref_key"]): str(row["pref_value"]) for row in (rows or []) if row}
        except Exception as exc:
            logger.debug("Preferences fetch failed: %s", exc)
            return {}

    async def _fetch_intent_history(self, db, tenant_id: str, user_id: str) -> list[str]:
        """Fetch recent intents from messages table."""
        try:
            rows = await db.fetch(
                "SELECT DISTINCT intent_type FROM messages m "
                "JOIN conversations c ON m.conversation_id=c.id AND m.company_id=c.company_id "
                "WHERE m.company_id=$1 AND c.customer_id=$2 "
                "AND m.intent_type IS NOT NULL AND m.intent_type != '' "
                "ORDER BY m.created_at DESC LIMIT 10",
                tenant_id,
                user_id,
            )
            return [str(row["intent_type"]) for row in (rows or []) if row]
        except Exception as exc:
            logger.debug("Intent history fetch failed: %s", exc)
            return []

    async def _fetch_sentiment_trend(self, db, tenant_id: str, user_id: str) -> list[dict]:
        """Fetch recent sentiment scores from conversations."""
        try:
            rows = await db.fetch(
                "SELECT sentiment_label, sentiment_score, created_at "
                "FROM conversations "
                "WHERE company_id=$1 AND customer_id=$2 "
                "AND sentiment_label IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 5",
                tenant_id,
                user_id,
            )
            return [
                {
                    "label": str(row.get("sentiment_label", "")),
                    "score": float(row.get("sentiment_score") or 0),
                    "date": str(row.get("created_at", "")),
                }
                for row in rs(rows or [])
            ]
        except Exception as exc:
            logger.debug("Sentiment trend fetch failed: %s", exc)
            return []

    async def _fetch_shown_products(self, db, tenant_id: str, user_id: str, conversation_id: str) -> list[str]:
        """Fetch previously shown product IDs from context_memories."""
        try:
            row = await db.fetchrow(
                "SELECT memory_content FROM context_memories "
                "WHERE company_id=$1 AND entity_id=$2 AND memory_type='shown_products' "
                "AND ($3='' OR convo_id=$3) "
                "ORDER BY updated_at DESC LIMIT 1",
                tenant_id,
                user_id,
                conversation_id,
            )
            if not row:
                return []
            content = str(dict(row).get("memory_content", ""))
            parsed = json.loads(content) if content else {}
            return [str(pid).strip() for pid in (parsed.get("product_ids") or []) if str(pid).strip()]
        except Exception as exc:
            logger.debug("Shown products fetch failed: %s", exc)
            return []

    async def _fetch_context_memories(self, db, tenant_id: str, user_id: str, conversation_id: str) -> list[dict]:
        """Fetch all context memories for additional context."""
        try:
            rows = await db.fetch(
                "SELECT memory_type, memory_content, relevance_score, updated_at "
                "FROM context_memories "
                "WHERE company_id=$1 AND entity_id=$2 "
                "AND ($3='' OR convo_id=$3) "
                "ORDER BY updated_at DESC LIMIT 10",
                tenant_id,
                user_id,
                conversation_id,
            )
            return rs(rows or [])
        except Exception as exc:
            logger.debug("Context memories fetch failed: %s", exc)
            return []
