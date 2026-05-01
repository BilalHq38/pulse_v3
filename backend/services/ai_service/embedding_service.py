from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Optional

from core.utils import make_id
from shared.cache import get_cache_client
from services.ai_service.llm_client import (
    GEMINI_EMBEDDING_MODEL,
    OPENAI_EMBEDDING_MODEL,
    _default_engine,
    _engine_for_provider,
    get_provider_runtime_info,
)
from services.ai_service.llm_tracking import get_llm_context, reserve_embedding_call

logger = logging.getLogger(__name__)

_PREFERRED_EMBEDDING_PROVIDER = (os.getenv("AI_EMBEDDING_PROVIDER", "") or "").strip().lower()
_EMBEDDING_CACHE = get_cache_client(namespace="ai-embeddings")
_EMBEDDING_CACHE_TTL_SECONDS = max(300, int(os.getenv("AI_EMBEDDING_CACHE_TTL_SECONDS", "86400") or 86400))
_EMBEDDING_PROVIDER_FALLBACK = os.getenv("AI_ENABLE_EMBEDDING_PROVIDER_FALLBACK", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _normalize_embedding_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _embedding_cache_key(text: str) -> str:
    normalized = _normalize_embedding_text(text)[:8000]
    return "embed:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _should_skip_embedding_search(text: str) -> bool:
    normalized = _normalize_embedding_text(text)
    if len(normalized) < 12:
        return True
    tokens = re.findall(r"[a-z0-9]+", normalized)
    if len(tokens) <= 2 and not any(
        token in normalized
        for token in ("product", "catalog", "price", "buy", "order", "plan", "service", "feature")
    ):
        return True
    return False


def _log_embedding_attempt(provider: str, model: str, attempt_number: int, text: str) -> None:
    context = get_llm_context()
    logger.info(
        "llm_call outcome=attempt message_id=%s conversation_id=%s workflow_id=%s company_id=%s "
        "agent=%s function=generate_embedding provider=%s model=%s purpose=embedding "
        "call_type=embedding attempt=%s fallback_used=%s token_estimate=%s",
        (context.message_id if context else "") or "-",
        (context.conversation_id if context else "") or "-",
        (context.workflow_id if context else "") or "-",
        (context.company_id if context else "") or "-",
        (context.agent_name if context else "") or "-",
        provider or "-",
        model or "-",
        attempt_number,
        attempt_number > 1,
        len(text.strip()) // 4,
    )


def _embedding_candidate_engines(engine: dict | None = None) -> list[dict]:
    candidates: list[dict] = []
    seen: set[str] = set()
    base_engine = dict(engine or _default_engine())
    providers = [
        str(base_engine.get("provider") or "").strip().lower(),
        _PREFERRED_EMBEDDING_PROVIDER,
        "openai",
        "gemini",
    ]
    for provider in providers:
        if provider not in {"openai", "gemini"}:
            continue
        ready, _ = get_provider_runtime_info(provider)
        if not ready:
            continue
        candidate = base_engine if provider == str(base_engine.get("provider") or "").strip().lower() else _engine_for_provider(base_engine, provider)
        signature = f"{provider}:{candidate.get('model_name', '')}"
        if signature in seen:
            continue
        seen.add(signature)
        candidates.append(candidate)
    return candidates


async def generate_embedding(text: str, engine: dict | None = None) -> Optional[list[float]]:
    if not text or not text.strip():
        return None
    cache_key = _embedding_cache_key(text)
    cached = await _EMBEDDING_CACHE.get_json(cache_key)
    if isinstance(cached, list):
        logger.info("embedding_cache_hit text_hash=%s dimensions=%s", cache_key[6:18], len(cached))
        return [float(item) for item in cached]
    logger.info("embedding_cache_miss text_hash=%s text_len=%s", cache_key[6:18], len(text or ""))

    from services.ai_service.llm_client import _gemini_client, _openai_client

    candidates = _embedding_candidate_engines(engine)
    if not _EMBEDDING_PROVIDER_FALLBACK:
        candidates = candidates[:1]
    for attempt_number, selected in enumerate(candidates, start=1):
        provider = (selected.get("provider") or "").lower()
        try:
            if provider == "gemini" and _gemini_client:
                _log_embedding_attempt(provider, GEMINI_EMBEDDING_MODEL, attempt_number, text)
                try:
                    reserve_embedding_call(
                        function_name="generate_embedding",
                        provider=provider,
                        model=GEMINI_EMBEDDING_MODEL,
                        call_purpose="embedding",
                        attempt_number=attempt_number,
                        fallback_used=attempt_number > 1,
                        token_estimate=len(text.strip()) // 4,
                    )
                except RuntimeError:
                    logger.warning(
                        "embedding_skipped_budget_exhausted provider=%s model=%s",
                        provider,
                        GEMINI_EMBEDDING_MODEL,
                    )
                    return None
                result = await _gemini_client.aio.models.embed_content(
                    model=GEMINI_EMBEDDING_MODEL,
                    contents=text.strip()[:8000],
                )
                values = getattr((getattr(result, "embeddings", []) or [None])[0], "values", None)
                if isinstance(values, list):
                    logger.info(
                        "embedding_generated provider=%s model=%s dimensions=%s",
                        provider,
                        GEMINI_EMBEDDING_MODEL,
                        len(values),
                    )
                    await _EMBEDDING_CACHE.set_json(cache_key, values, ttl_seconds=_EMBEDDING_CACHE_TTL_SECONDS)
                    return values
            if provider == "openai" and _openai_client:
                _log_embedding_attempt(provider, OPENAI_EMBEDDING_MODEL, attempt_number, text)
                try:
                    reserve_embedding_call(
                        function_name="generate_embedding",
                        provider=provider,
                        model=OPENAI_EMBEDDING_MODEL,
                        call_purpose="embedding",
                        attempt_number=attempt_number,
                        fallback_used=attempt_number > 1,
                        token_estimate=len(text.strip()) // 4,
                    )
                except RuntimeError:
                    logger.warning(
                        "embedding_skipped_budget_exhausted provider=%s model=%s",
                        provider,
                        OPENAI_EMBEDDING_MODEL,
                    )
                    return None
                response = await _openai_client.embeddings.create(
                    model=OPENAI_EMBEDDING_MODEL,
                    input=text.strip()[:8000],
                )
                values = response.data[0].embedding
                if isinstance(values, list):
                    logger.info(
                        "embedding_generated provider=%s model=%s dimensions=%s",
                        provider,
                        OPENAI_EMBEDDING_MODEL,
                        len(values),
                    )
                    await _EMBEDDING_CACHE.set_json(cache_key, values, ttl_seconds=_EMBEDDING_CACHE_TTL_SECONDS)
                    return values
        except Exception as exc:
            logger.warning(
                "embedding_generation_failed provider=%s model=%s error=%s",
                provider,
                OPENAI_EMBEDDING_MODEL if provider == "openai" else GEMINI_EMBEDDING_MODEL,
                exc,
            )
            continue
    logger.warning("Embedding generation unavailable for current runtime")
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
            logger.debug(
                "embedding_upsert company_id=%s source_type=%s source_id=%s chunk_index=%s embedding_id=%s",
                company_id,
                source_type,
                source_id,
                chunk_index,
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
        logger.debug(
            "embedding_insert company_id=%s source_type=%s source_id=%s chunk_index=%s embedding_id=%s",
            company_id,
            source_type,
            source_id,
            chunk_index,
            embedding_id,
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
    if _should_skip_embedding_search(query_text):
        logger.info("embedding_search_skipped reason=low_value_query query_len=%s", len(query_text or ""))
        return []
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
        results = [dict(row) for row in rows]
        logger.debug(
            "embedding_search company_id=%s source_type=%s query_len=%s result_count=%s",
            company_id,
            source_type or "*",
            len(query_text or ""),
            len(results),
        )
        return results
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
