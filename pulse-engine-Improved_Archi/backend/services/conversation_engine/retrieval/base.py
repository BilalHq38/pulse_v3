"""Protocol every source retriever implements.

Each retriever returns ContextChunk records on the same envelope so the
compression, budgeting, and prompt-building layers stay source-agnostic.
relevance_score is normalised to [0, 1] inside the retriever — the rest of
the pipeline never re-normalises across retrievers.
"""

from __future__ import annotations

from typing import Protocol

from services.conversation_engine.schemas import ContextChunk, SourceType


class SourceRetriever(Protocol):
    source_type: SourceType

    async def fetch(self, db, *, company_id: str, query: str, top_k: int) -> list[ContextChunk]:
        ...
