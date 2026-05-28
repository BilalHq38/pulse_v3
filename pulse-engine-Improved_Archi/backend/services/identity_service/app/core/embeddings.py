from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(self):
        self.model_name = os.environ.get("EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
        self.expected_dim = int(os.environ.get("EMBEDDING_DIM", "384"))
        self.cache_max_items = max(128, int(os.environ.get("EMBEDDING_CACHE_MAX_ITEMS", "2048") or 2048))
        self._model = None
        self._lock = asyncio.Lock()
        self._cache_lock = asyncio.Lock()
        self._cache: dict[str, list[float]] = {}
        self._cache_order: list[str] = []

    async def _load_model(self):
        async with self._lock:
            if self._model is None:
                logger.info("Loading embedding model %s", self.model_name)
                self._model = await asyncio.to_thread(_build_model, self.model_name)

    async def encode(self, text: str | None) -> list[float] | None:
        if not text:
            return None
        cache_key = hashlib.sha256(str(text).strip().lower().encode("utf-8")).hexdigest()
        cached = await self._cached_vector(cache_key)
        if cached is not None:
            return cached

        try:
            if self._model is None:
                await self._load_model()
            if self._model is None:
                fallback = _fallback_embedding(text, self.expected_dim)
                await self._remember_vector(cache_key, fallback)
                return fallback
            vector = await asyncio.to_thread(self._model.encode, text, normalize_embeddings=True)
            vector_list = [float(item) for item in vector.tolist()]
            if len(vector_list) != self.expected_dim:
                logger.warning(
                    "Embedding dimension mismatch. expected=%s actual=%s model=%s",
                    self.expected_dim,
                    len(vector_list),
                    self.model_name,
                )
                fallback = _fallback_embedding(text, self.expected_dim)
                await self._remember_vector(cache_key, fallback)
                return fallback
            await self._remember_vector(cache_key, vector_list)
            return vector_list
        except Exception as exc:
            logger.warning("Embedding model unavailable, using deterministic fallback: %s", exc)
            fallback = _fallback_embedding(text, self.expected_dim)
            await self._remember_vector(cache_key, fallback)
            return fallback

    async def _cached_vector(self, cache_key: str) -> list[float] | None:
        async with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is None:
                return None
            return list(cached)

    async def _remember_vector(self, cache_key: str, vector: list[float]) -> None:
        async with self._cache_lock:
            if cache_key not in self._cache:
                self._cache_order.append(cache_key)
            self._cache[cache_key] = list(vector)
            while len(self._cache_order) > self.cache_max_items:
                oldest = self._cache_order.pop(0)
                self._cache.pop(oldest, None)


@lru_cache(maxsize=1)
def _build_model(model_name: str) -> Any:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name)


def _fallback_embedding(text: str, expected_dim: int) -> list[float]:
    dimension = max(8, int(expected_dim or 384))
    bins = [0.0] * dimension
    for token in str(text or "").lower().split():
        token_hash = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(token_hash[:2], "big") % dimension
        weight = (int.from_bytes(token_hash[2:4], "big") / 65535.0) - 0.5
        bins[index] += weight
    norm = sum(value * value for value in bins) ** 0.5
    if norm <= 1e-12:
        return [0.0] * dimension
    return [value / norm for value in bins]


embedding_service = EmbeddingService()
