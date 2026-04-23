from __future__ import annotations

import logging
from typing import Optional

from core.utils import make_id
from services.ai_service.llm_client import EMBEDDING_MODEL, _default_engine

logger = logging.getLogger(__name__)


async def generate_embedding(text: str, engine: dict | None = None) -> Optional[list[float]]:
    if not text or not text.strip():
        return None

    from services.ai_service.llm_client import _gemini_client, _openai_client

    selected = engine or _default_engine()
    provider = (selected.get("provider") or "").lower()
    try:
        if provider == "gemini" and _gemini_client:
            result = await _gemini_client.aio.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=text.strip()[:8000],
            )
            values = getattr((getattr(result, "embeddings", []) or [None])[0], "values", None)
            return values if isinstance(values, list) else None
        if provider == "openai" and _openai_client:
            response = await _openai_client.embeddings.create(
                model="text-embedding-3-small",
                input=text.strip()[:8000],
            )
            return response.data[0].embedding
    except Exception as exc:
        logger.error("Embedding generation failed: %s", exc)
    return None


async def store_embedding(
    db,
    company_id: str,
    source_type: str,
    source_id: str,
    content: str,
    chunk_index: int = 0,
    metadata: str = "",
    engine: dict | None = None,
) -> Optional[str]:
    if not db or not company_id or not source_type or not source_id:
        return None
    embedding = await generate_embedding(content, engine)
    if embedding is None:
        return None
    embedding_id = make_id()
    vector_value = "[" + ",".join(str(item) for item in embedding) + "]"
    try:
        existing = await db.fetchval(
            "SELECT id FROM embeddings "
            "WHERE company_id=$1 AND source_type=$2 AND source_id=$3 AND chunk_index=$4 "
            "ORDER BY created_at DESC NULLS LAST LIMIT 1",
            company_id,
            source_type,
            source_id,
            chunk_index,
        )
        if existing:
            await db.execute(
                "UPDATE embeddings SET content=$1, embedding=$2::vector, metadata=$3, created_at=NOW() WHERE id=$4",
                content[:10000],
                vector_value,
                metadata,
                existing,
            )
            return str(existing)
        await db.execute(
            "INSERT INTO embeddings(id,company_id,source_type,source_id,chunk_index,content,embedding,metadata,created_at) "  # noqa: E501
            "VALUES($1,$2,$3,$4,$5,$6,$7::vector,$8,NOW())",
            embedding_id,
            company_id,
            source_type,
            source_id,
            chunk_index,
            content[:10000],
            vector_value,
            metadata,
        )
        return embedding_id
    except Exception as exc:
        logger.error("Failed to store embedding: %s", exc)
        return None


async def search_similar_embeddings(
    db,
    company_id: str,
    query_text: str,
    source_type: str = "",
    top_k: int = 5,
    engine: dict | None = None,
) -> list[dict]:
    embedding = await generate_embedding(query_text, engine)
    if embedding is None:
        return []
    vector_value = "[" + ",".join(str(item) for item in embedding) + "]"
    try:
        if source_type:
            rows = await db.fetch(
                "SELECT id,source_type,source_id,content,metadata,"
                "1 - (embedding <=> $1::vector) AS similarity "
                "FROM embeddings WHERE company_id=$2 AND source_type=$3 AND embedding IS NOT NULL "
                "ORDER BY embedding <=> $1::vector LIMIT $4",
                vector_value,
                company_id,
                source_type,
                top_k,
            )
        else:
            rows = await db.fetch(
                "SELECT id,source_type,source_id,content,metadata,"
                "1 - (embedding <=> $1::vector) AS similarity "
                "FROM embeddings WHERE company_id=$2 AND embedding IS NOT NULL "
                "ORDER BY embedding <=> $1::vector LIMIT $3",
                vector_value,
                company_id,
                top_k,
            )
        return [dict(row) for row in rows]
    except Exception as exc:
        logger.error("Vector search failed: %s", exc)
        return []


async def batch_store_embeddings(
    db,
    company_id: str,
    source_type: str,
    source_id: str,
    chunks: list[str],
    metadata: str = "",
    engine: dict | None = None,
) -> int:
    count = 0
    for index, chunk in enumerate(chunks):
        if not chunk or not chunk.strip():
            continue
        stored = await store_embedding(
            db,
            company_id,
            source_type,
            source_id,
            chunk,
            chunk_index=index,
            metadata=metadata,
            engine=engine,
        )
        if stored:
            count += 1
    return count


def _chunk_text(text: str, max_chars: int = 1000, overlap: int = 100) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    # Clamp overlap to at most half of max_chars to prevent infinite loops
    safe_overlap = min(overlap, max_chars // 2)
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk.strip())
        start = end - safe_overlap
        if start <= 0:
            start = end
    return chunks


async def index_knowledge_base(db, company_id: str) -> int:
    rows = await db.fetch("SELECT id,title,content FROM knowledge_base WHERE company_id=$1", company_id)
    total = 0
    for row in rows:
        article = dict(row)
        chunks = _chunk_text(
            f"{article.get('title', '')}\n{article.get('content', '')}",
            max_chars=1000,
            overlap=100,
        )
        total += await batch_store_embeddings(
            db,
            company_id,
            "knowledge_base",
            article["id"],
            chunks,
        )
    return total


__all__ = [
    "batch_store_embeddings",
    "generate_embedding",
    "index_knowledge_base",
    "search_similar_embeddings",
    "store_embedding",
]
