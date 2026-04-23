"""
memory_engine/semantic.py — pgvector-backed semantic memory.

Retrieves semantically relevant context using embedding similarity search.
Wraps the existing embedding_service.py without duplicating logic.
"""

from __future__ import annotations

import logging
from typing import Any

from memory_engine.schemas import SemanticContext

logger = logging.getLogger(__name__)


class SemanticMemory:
    """
    Semantic memory layer backed by pgvector.

    Uses existing embedding_service.py for:
        - Generating embeddings
        - Similarity search against stored embeddings
        - Knowledge base retrieval
    """

    async def load(
        self,
        db,
        tenant_id: str,
        query: str,
        *,
        top_k: int = 5,
        min_similarity: float = 0.5,
    ) -> SemanticContext:
        """
        Retrieve semantically relevant context for a query.

        Searches:
            1. Knowledge base embeddings
            2. Past interaction embeddings
        """
        if not db or not tenant_id or not query or not query.strip():
            return SemanticContext()

        # Search knowledge base
        knowledge_results = await self._search_knowledge(db, tenant_id, query, top_k=top_k)

        # Search past interactions
        interaction_results = await self._search_interactions(db, tenant_id, query, top_k=min(3, top_k))

        # Filter by minimum similarity
        relevant_knowledge = [r for r in knowledge_results if float(r.get("similarity") or 0) >= min_similarity]
        relevant_interactions = [r for r in interaction_results if float(r.get("similarity") or 0) >= min_similarity]

        # Build knowledge text summary
        knowledge_text = self._compile_knowledge_text(relevant_knowledge)

        return SemanticContext(
            relevant_knowledge=relevant_knowledge,
            similar_interactions=relevant_interactions,
            knowledge_text=knowledge_text,
        )

    async def store_interaction_embedding(
        self,
        db,
        tenant_id: str,
        user_id: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: str = "",
    ) -> str | None:
        """Store an embedding for a customer interaction."""
        if not db or not tenant_id or not user_id or not content.strip():
            return None

        from services.ai_service.embedding_service import store_embedding

        source_id = conversation_id or user_id
        return await store_embedding(
            db,
            tenant_id,
            "interaction",
            source_id,
            content,
            metadata=metadata,
        )

    async def store_customer_profile_embedding(
        self,
        db,
        tenant_id: str,
        user_id: str,
        profile_text: str,
    ) -> str | None:
        """Store an embedding for a customer's profile summary."""
        if not db or not tenant_id or not user_id or not profile_text.strip():
            return None

        from services.ai_service.embedding_service import store_embedding

        return await store_embedding(
            db,
            tenant_id,
            "customer_profile",
            user_id,
            profile_text,
        )

    async def _search_knowledge(
        self,
        db,
        tenant_id: str,
        query: str,
        *,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Search knowledge base embeddings."""
        try:
            from services.ai_service.embedding_service import search_similar_embeddings

            return await search_similar_embeddings(
                db,
                tenant_id,
                query,
                source_type="knowledge_base",
                top_k=top_k,
            )
        except Exception as exc:
            logger.warning(
                "Knowledge base semantic search failed tenant=%s: %s",
                tenant_id,
                exc,
            )
            return []

    async def _search_interactions(
        self,
        db,
        tenant_id: str,
        query: str,
        *,
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """Search past interaction embeddings."""
        try:
            from services.ai_service.embedding_service import search_similar_embeddings

            return await search_similar_embeddings(
                db,
                tenant_id,
                query,
                source_type="interaction",
                top_k=top_k,
            )
        except Exception as exc:
            logger.warning(
                "Interaction semantic search failed tenant=%s: %s",
                tenant_id,
                exc,
            )
            return []

    def _compile_knowledge_text(
        self,
        results: list[dict[str, Any]],
        *,
        max_chars: int = 2000,
    ) -> str:
        """Compile search results into a text block for prompt context."""
        if not results:
            return ""

        sections: list[str] = []
        total_chars = 0

        for result in results:
            content = str(result.get("content") or "").strip()
            if not content:
                continue

            section = content
            if len(section) + total_chars > max_chars:
                remaining = max_chars - total_chars
                if remaining > 50:
                    section = section[:remaining].rstrip() + "..."
                else:
                    break

            sections.append(section)
            total_chars += len(section)

        return "\n\n".join(sections)
