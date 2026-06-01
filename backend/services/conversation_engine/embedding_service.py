from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from typing import Optional

from core.utils import make_id
from shared.config import ai_api_call_timeout_seconds, ai_embedding_unsupported_cooldown_seconds
from shared.cache import get_cache_client
from services.ai_runtime.llm_client import (
    GEMINI_EMBEDDING_MODEL,
    OPENAI_EMBEDDING_MODEL,
    _default_engine,
    _engine_for_provider,
    get_provider_runtime_info,
)
from services.ai_service.model_catalog import GEMINI_PROVIDER_KEYS
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
_UNSUPPORTED_MODEL_COOLDOWNS: dict[str, float] = {}
_UNSUPPORTED_MODEL_LOGGED_UNTIL: dict[str, float] = {}
_EMBEDDING_UNAVAILABLE_LOGGED_UNTIL: dict[str, float] = {}
# Shared Redis cache key prefix for cross-worker embedding unavailability.
_EMBED_UNAVAILABLE_CACHE_KEY_PREFIX = "ai:embed:unavailable:"
# Minimum cooldown even if config returns 0.
_EMBED_MIN_COOLDOWN_SECONDS = 300  # 5 minutes


def _normalize_embedding_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _embedding_cache_key(text: str, *, provider: str = "", model: str = "", company_id: str = "") -> str:
    normalized = _normalize_embedding_text(text)[:8000]
    scope = f"{company_id.strip()}:{provider.strip().lower()}:{model.strip().lower()}:{normalized}"
    return "embed:" + hashlib.sha256(scope.encode("utf-8")).hexdigest()


def _embedding_model_for_provider(provider: str) -> str:
    if provider in GEMINI_PROVIDER_KEYS:
        return GEMINI_EMBEDDING_MODEL
    if provider == "openai":
        return OPENAI_EMBEDDING_MODEL
    return ""


def _embedding_provider_signature(provider: str) -> str:
    return f"{provider}:{_embedding_model_for_provider(provider)}"


def _classify_embedding_error(exc: Exception) -> str:
    message = str(exc or "").lower()
    # A bare 404 from an embedding endpoint means the model is unavailable on this provider,
    # regardless of whether "embed"/"model" appear in the error string.
    if "404" in message or "not found" in message:
        return "unsupported_embedding_model"
    if (
        "not_supported" in message
        or "not supported" in message
        or "unsupported" in message
    ):
        return "unsupported_embedding_model"
    if "quota" in message or "rate limit" in message or "resource_exhausted" in message:
        return "quota_exhausted"
    if "api key" in message or "permission" in message or "unauthorized" in message or "forbidden" in message:
        return "provider_not_configured"
    return "provider_error"


def _cooldown_key(provider: str, model: str, error_type: str) -> str:
    return f"{provider.strip().lower()}:{model.strip().lower()}:{error_type}"


def _is_embedding_model_in_cooldown(provider: str, model: str, error_type: str = "unsupported_embedding_model") -> bool:
    key = _cooldown_key(provider, model, error_type)
    expires_at = float(_UNSUPPORTED_MODEL_COOLDOWNS.get(key) or 0)
    if expires_at <= time.monotonic():
        _UNSUPPORTED_MODEL_COOLDOWNS.pop(key, None)
        _UNSUPPORTED_MODEL_LOGGED_UNTIL.pop(key, None)
        return False
    return True


def _is_embedding_provider_in_any_cooldown(provider: str, model: str) -> bool:
    return any(
        _is_embedding_model_in_cooldown(provider, model, error_type)
        for error_type in ("unsupported_embedding_model", "provider_not_configured", "quota_exhausted", "provider_error")
    )


async def _maybe_sync_embedding_cooldown_from_cache(provider: str, model: str) -> None:
    """Seed this worker's cooldown dict from the shared cache when available."""
    for error_type in ("unsupported_embedding_model", "provider_not_configured", "quota_exhausted", "provider_error"):
        key = _cooldown_key(provider, model, error_type)
        if _is_embedding_model_in_cooldown(provider, model, error_type):
            return
        try:
            cache_key = f"{_EMBED_UNAVAILABLE_CACHE_KEY_PREFIX}{key}"
            result = await _EMBEDDING_CACHE.get_json(cache_key)
            if isinstance(result, dict) and result.get("unavailable"):
                # Keep the local seed short so the worker periodically re-checks the shared TTL.
                _UNSUPPORTED_MODEL_COOLDOWNS[key] = time.monotonic() + 60
                logger.info(
                    "embedding_cooldown_seeded_from_cache provider=%s model=%s error_type=%s",
                    provider,
                    model,
                    error_type,
                )
                return
        except Exception:
            pass


def _should_log_embedding_unavailable(signature: str) -> bool:
    cooldown_seconds = max(60, ai_embedding_unsupported_cooldown_seconds())
    now = time.monotonic()
    expires_at = float(_EMBEDDING_UNAVAILABLE_LOGGED_UNTIL.get(signature) or 0)
    if expires_at > now:
        return False
    _EMBEDDING_UNAVAILABLE_LOGGED_UNTIL[signature] = now + cooldown_seconds
    return True


def _mark_embedding_model_cooldown(provider: str, model: str, error_type: str, exc: Exception) -> None:
    # All embedding errors get a cooldown to avoid hammering a broken endpoint.
    if error_type in {"unsupported_embedding_model", "provider_not_configured"}:
        cooldown_seconds = max(_EMBED_MIN_COOLDOWN_SECONDS, ai_embedding_unsupported_cooldown_seconds())
    elif error_type == "quota_exhausted":
        cooldown_seconds = 60
    else:
        cooldown_seconds = 120
    key = _cooldown_key(provider, model, error_type)
    expires_at = time.monotonic() + cooldown_seconds
    _UNSUPPORTED_MODEL_COOLDOWNS[key] = expires_at
    # Write to shared cache so all workers can respect the cooldown without each making a failing call.
    try:
        cache_key = f"{_EMBED_UNAVAILABLE_CACHE_KEY_PREFIX}{key}"
        loop = asyncio.get_running_loop()
        if not loop.is_closed():
            loop.create_task(
                _EMBEDDING_CACHE.set_json(
                    cache_key,
                    {"unavailable": True, "error_type": error_type},
                    ttl_seconds=int(cooldown_seconds),
                )
            )
    except Exception:
        pass
    if float(_UNSUPPORTED_MODEL_LOGGED_UNTIL.get(key) or 0) <= time.monotonic():
        _UNSUPPORTED_MODEL_LOGGED_UNTIL[key] = expires_at
        logger.warning(
            "embedding_provider_disabled_temporarily provider=%s model=%s error_type=%s cooldown_seconds=%s error=%s",
            provider,
            model,
            error_type,
            cooldown_seconds,
            exc,
        )


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


def _log_embedding_call(
    *,
    outcome: str,
    provider: str,
    model: str,
    attempt_number: int,
    text: str,
    latency_ms: float,
    company_id: str = "",
    fallback_used: bool = False,
    error: Exception | None = None,
) -> None:
    context = get_llm_context()
    prompt_tokens = max(0, len(str(text or "").strip()) // 4)
    payload = {
        "event": "llm_call",
        "outcome": outcome,
        "model": model or "-",
        "provider": provider or "-",
        "function": "generate_embedding",
        "company_id": company_id or (context.company_id if context else "") or "-",
        "latency_ms": round(float(latency_ms or 0.0), 2),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": 0,
        "total_tokens": prompt_tokens,
        "attempt": attempt_number,
        "fallback_used": bool(fallback_used),
        "message_id": (context.message_id if context else "") or "-",
        "conversation_id": (context.conversation_id if context else "") or "-",
        "workflow_id": (context.workflow_id if context else "") or "-",
        "agent": (context.agent_name if context else "") or "-",
        "purpose": "embedding",
        "call_type": "embedding",
    }
    if error is None:
        logger.info(payload)
        return
    payload["error_type"] = error.__class__.__name__
    payload["error"] = str(error).splitlines()[0][:240]
    if outcome == "timeout":
        logger.warning(payload)
    else:
        logger.error(payload)


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
        if provider not in {"openai", *GEMINI_PROVIDER_KEYS}:
            continue
        ready, _ = get_provider_runtime_info(provider)
        if not ready:
            continue
        candidate = base_engine if provider == str(base_engine.get("provider") or "").strip().lower() else _engine_for_provider(base_engine, provider)
        signature = _embedding_provider_signature(provider)
        if signature in seen:
            continue
        seen.add(signature)
        candidates.append(candidate)
    return candidates


async def generate_embedding(text: str, engine: dict | None = None, *, company_id: str = "") -> Optional[list[float]]:
    if not text or not text.strip():
        return None

    from services.ai_runtime.llm_client import _gemini_client, _gemini_client_for_model, _openai_client

    candidates = _embedding_candidate_engines(engine)
    if not _EMBEDDING_PROVIDER_FALLBACK:
        candidates = candidates[:1]
    for attempt_number, selected in enumerate(candidates, start=1):
        provider = (selected.get("provider") or "").lower()
        model = _embedding_model_for_provider(provider)
        if not model:
            continue
        await _maybe_sync_embedding_cooldown_from_cache(provider, model)
        if _is_embedding_provider_in_any_cooldown(provider, model):
            logger.debug(
                "embedding_provider_skipped_cooldown provider=%s model=%s",
                provider,
                model,
            )
            continue
        cache_key = _embedding_cache_key(text, provider=provider, model=model, company_id=company_id)
        cached = await _EMBEDDING_CACHE.get_json(cache_key)
        if isinstance(cached, list):
            logger.info(
                "embedding_cache_hit provider=%s model=%s text_hash=%s dimensions=%s",
                provider,
                model,
                cache_key[6:18],
                len(cached),
            )
            return [float(item) for item in cached]
        logger.info(
            "embedding_cache_miss provider=%s model=%s text_hash=%s text_len=%s",
            provider,
            model,
            cache_key[6:18],
            len(text or ""),
        )
        started = time.perf_counter()
        try:
            gemini_client = (
                (_gemini_client if provider == "gemini" else None) or _gemini_client_for_model(model, provider)
                if provider in GEMINI_PROVIDER_KEYS
                else None
            )
            if provider in GEMINI_PROVIDER_KEYS and gemini_client:
                try:
                    reserve_embedding_call(
                        function_name="generate_embedding",
                        provider=provider,
                        model=model,
                        call_purpose="embedding",
                        attempt_number=attempt_number,
                        fallback_used=attempt_number > 1,
                        token_estimate=len(text.strip()) // 4,
                    )
                except RuntimeError:
                    logger.warning(
                        "embedding_skipped_budget_exhausted provider=%s model=%s",
                        provider,
                        model,
                    )
                    return None
                async with asyncio.timeout(ai_api_call_timeout_seconds()):
                    result = await gemini_client.aio.models.embed_content(
                        model=model,
                        contents=text.strip()[:8000],
                    )
                values = getattr((getattr(result, "embeddings", []) or [None])[0], "values", None)
                if isinstance(values, list):
                    _log_embedding_call(
                        outcome="success",
                        provider=provider,
                        model=model,
                        attempt_number=attempt_number,
                        text=text,
                        latency_ms=(time.perf_counter() - started) * 1000.0,
                        company_id=company_id,
                        fallback_used=attempt_number > 1,
                    )
                    await _EMBEDDING_CACHE.set_json(cache_key, values, ttl_seconds=_EMBEDDING_CACHE_TTL_SECONDS)
                    return values
            if provider == "openai" and _openai_client:
                try:
                    reserve_embedding_call(
                        function_name="generate_embedding",
                        provider=provider,
                        model=model,
                        call_purpose="embedding",
                        attempt_number=attempt_number,
                        fallback_used=attempt_number > 1,
                        token_estimate=len(text.strip()) // 4,
                    )
                except RuntimeError:
                    logger.warning(
                        "embedding_skipped_budget_exhausted provider=%s model=%s",
                        provider,
                        model,
                    )
                    return None
                async with asyncio.timeout(ai_api_call_timeout_seconds()):
                    response = await _openai_client.embeddings.create(
                        model=model,
                        input=text.strip()[:8000],
                    )
                values = response.data[0].embedding
                if isinstance(values, list):
                    _log_embedding_call(
                        outcome="success",
                        provider=provider,
                        model=model,
                        attempt_number=attempt_number,
                        text=text,
                        latency_ms=(time.perf_counter() - started) * 1000.0,
                        company_id=company_id,
                        fallback_used=attempt_number > 1,
                    )
                    await _EMBEDDING_CACHE.set_json(cache_key, values, ttl_seconds=_EMBEDDING_CACHE_TTL_SECONDS)
                    return values
        except Exception as exc:
            error_type = _classify_embedding_error(exc)
            _mark_embedding_model_cooldown(provider, model, error_type, exc)
            _log_embedding_call(
                outcome="timeout" if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) else "error",
                provider=provider,
                model=model,
                attempt_number=attempt_number,
                text=text,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                company_id=company_id,
                fallback_used=attempt_number > 1,
                error=exc,
            )
            continue
    unavailable_signature = ",".join(
        _embedding_provider_signature(str(item.get("provider") or "").lower()) for item in candidates
    ) or "-"
    if _should_log_embedding_unavailable(unavailable_signature):
        logger.warning(
            "embedding_generation_unavailable company_id=%s candidates=%s",
            company_id or "",
            unavailable_signature,
        )
    else:
        logger.debug(
            "embedding_generation_unavailable_suppressed company_id=%s candidates=%s",
            company_id or "",
            unavailable_signature,
        )
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
    embedding = await generate_embedding(content, engine, company_id=company_id)
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
    source_ids: list[str] | None = None,
) -> list[dict]:
    if _should_skip_embedding_search(query_text):
        logger.info("embedding_search_skipped reason=low_value_query query_len=%s", len(query_text or ""))
        return []
    embedding = await generate_embedding(query_text, engine, company_id=company_id)
    if embedding is None:
        return []
    vector_value = "[" + ",".join(str(item) for item in embedding) + "]"
    try:
        cleaned_source_ids = [str(item).strip() for item in (source_ids or []) if str(item).strip()]
        if source_type:
            if cleaned_source_ids:
                rows = await db.fetch(
                    "SELECT id,source_type,source_id,content,metadata,"
                    "1 - (embedding <=> $1::vector) AS similarity "
                    "FROM embeddings WHERE company_id=$2 AND source_type=$3 AND source_id = ANY($4::text[]) "
                    "AND embedding IS NOT NULL ORDER BY embedding <=> $1::vector LIMIT $5",
                    vector_value,
                    company_id,
                    source_type,
                    cleaned_source_ids,
                    top_k,
                )
            else:
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
            if cleaned_source_ids:
                rows = await db.fetch(
                    "SELECT id,source_type,source_id,content,metadata,"
                    "1 - (embedding <=> $1::vector) AS similarity "
                    "FROM embeddings WHERE company_id=$2 AND source_id = ANY($3::text[]) AND embedding IS NOT NULL "
                    "ORDER BY embedding <=> $1::vector LIMIT $4",
                    vector_value,
                    company_id,
                    cleaned_source_ids,
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
            "embedding_search company_id=%s source_type=%s source_id_scope=%s query_len=%s result_count=%s",
            company_id,
            source_type or "*",
            len(cleaned_source_ids),
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
