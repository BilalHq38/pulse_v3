"""Knowledge-base retriever — vector search via the existing embedding service.

The existing `search_similar_embeddings` already runs cosine search against
the `embeddings` table and returns rows with a `similarity` field already in
[0, 1] (computed as `1 - (embedding <=> query)`), so no extra normalisation
needed. We attach the pre-computed `summary` column if present so the
compression layer can swap it in for long articles without a fresh LLM call.
"""

from __future__ import annotations

import os

from services.conversation_engine.embedding_service import search_similar_embeddings
from services.conversation_engine.schemas import ContextChunk


_KB_MIN_SIMILARITY = float(os.environ.get("AI_KB_MIN_SIMILARITY", "0.55") or 0.55)


class KnowledgeBaseRetriever:
    source_type = "knowledge_base"

    async def fetch(self, db, *, company_id: str, query: str, top_k: int = 5) -> list[ContextChunk]:
        if not company_id or not query.strip():
            return []
        try:
            rows = await search_similar_embeddings(
                db,
                company_id,
                query,
                source_type="knowledge_base",
                top_k=max(1, int(top_k)),
            )
        except Exception:
            return []

        chunks: list[ContextChunk] = []
        kb_ids = [str(row.get("source_id") or "") for row in rows if row.get("source_id")]
        summaries = await self._fetch_summaries(db, company_id, kb_ids) if kb_ids else {}

        for row in rows:
            similarity = float(row.get("similarity") or 0.0)
            if similarity < _KB_MIN_SIMILARITY:
                continue
            source_id = str(row.get("source_id") or "")
            chunks.append(
                ContextChunk(
                    source_type="knowledge_base",
                    source_id=source_id,
                    title=(row.get("metadata") or {}).get("title", "") if isinstance(row.get("metadata"), dict) else "",
                    content=str(row.get("content") or ""),
                    metadata={
                        "summary": summaries.get(source_id, ""),
                        "raw_metadata": row.get("metadata") or {},
                    },
                    relevance_score=max(0.0, min(1.0, similarity)),
                )
            )
        return chunks

    @staticmethod
    async def _fetch_summaries(db, company_id: str, kb_ids: list[str]) -> dict[str, str]:
        cleaned = [s for s in (kb_ids or []) if s]
        if not cleaned:
            return {}
        try:
            rows = await db.fetch(
                "SELECT id, COALESCE(summary, '') AS summary "
                "FROM knowledge_base WHERE company_id = $1 AND id = ANY($2::text[])",
                company_id,
                cleaned,
            )
        except Exception:
            return {}
        return {str(r.get("id")): str(r.get("summary") or "") for r in rows or []}
