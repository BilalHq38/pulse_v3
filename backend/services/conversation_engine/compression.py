"""Request-time compression — deterministic, no live LLM calls.

Pipeline order matches the plan:
1. Substitute pre-computed KB summary for long KB chunks.
2. Hash-dedup identical (source_type, source_id, content_hash) chunks.
3. Merge adjacent same-source chunks under the per-chunk token cap.
4. Cross-source redundancy elimination — keep the higher-precedence source.

The compressor never calls the LLM. Rolling history summarisation (which is
the only step that *can* make an LLM call) lives in `memory.py`, gated behind
a turn-count threshold.
"""

from __future__ import annotations

import hashlib
from typing import Final

from services.conversation_engine.schemas import ContextChunk, SourceType


_KB_SUMMARY_TRIGGER_LEN: Final = 800
_PER_CHUNK_TOKEN_CAP: Final = 1200  # roughly 4800 chars — matches the budgeter's chunk slot
_CROSS_SOURCE_DEDUP_THRESHOLD: Final = 0.92

# Precedence: lower index = higher precedence. Matches the plan's
# Company Data > Product DB > Templates/FAQs > Knowledge Base.
_PRECEDENCE: dict[SourceType, int] = {
    "company_data": 0,
    "product": 1,
    "template": 2,
    "faq": 2,
    "knowledge_base": 3,
}


def _content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _shingles(text: str, k: int = 5) -> set[str]:
    """5-word shingles used as a cheap proxy for content similarity. Avoids the
    cost of loading embeddings at request time. Jaccard on shingles is a
    reasonable stand-in for cosine similarity when chunks are short."""
    tokens = (text or "").lower().split()
    if len(tokens) < k:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersect = len(a & b)
    union = len(a | b)
    return intersect / union if union else 0.0


def _apply_kb_summaries(chunks: list[ContextChunk]) -> list[ContextChunk]:
    out: list[ContextChunk] = []
    for chunk in chunks:
        if chunk.source_type == "knowledge_base" and len(chunk.content or "") > _KB_SUMMARY_TRIGGER_LEN:
            summary = (chunk.metadata or {}).get("summary") or ""
            if summary:
                chunk.metadata = {**(chunk.metadata or {}), "summarised": True}
                chunk.content = summary
        out.append(chunk)
    return out


def _dedup_exact(chunks: list[ContextChunk]) -> list[ContextChunk]:
    seen: set[tuple[str, str, str]] = set()
    out: list[ContextChunk] = []
    for chunk in chunks:
        key = (chunk.source_type, chunk.source_id, _content_hash(chunk.content))
        if key in seen:
            continue
        seen.add(key)
        out.append(chunk)
    return out


def _merge_adjacent_same_source(chunks: list[ContextChunk]) -> list[ContextChunk]:
    if not chunks:
        return []
    out: list[ContextChunk] = [chunks[0]]
    for chunk in chunks[1:]:
        prev = out[-1]
        same_source = prev.source_type == chunk.source_type and prev.source_id == chunk.source_id
        merged_len = len(prev.content) + len(chunk.content) + 2
        # Token cap is char-based for simplicity — ~4 chars per token.
        if same_source and merged_len <= _PER_CHUNK_TOKEN_CAP * 4:
            prev.content = (prev.content + "\n\n" + chunk.content).strip()
            prev.relevance_score = max(prev.relevance_score, chunk.relevance_score)
        else:
            out.append(chunk)
    return out


def _cross_source_eliminate(chunks: list[ContextChunk]) -> list[ContextChunk]:
    keep: list[ContextChunk] = []
    shingles_by_index: list[set[str]] = []
    for chunk in chunks:
        candidate_shingles = _shingles(chunk.content)
        is_dup_of: int | None = None
        for idx, existing_shingles in enumerate(shingles_by_index):
            if _jaccard(candidate_shingles, existing_shingles) >= _CROSS_SOURCE_DEDUP_THRESHOLD:
                is_dup_of = idx
                break
        if is_dup_of is None:
            keep.append(chunk)
            shingles_by_index.append(candidate_shingles)
            continue
        existing = keep[is_dup_of]
        # Keep whichever has higher precedence (lower number). On tie, keep the
        # higher-score one. Always carry forward the max relevance.
        new_pred = _PRECEDENCE.get(chunk.source_type, 9)
        old_pred = _PRECEDENCE.get(existing.source_type, 9)
        if (new_pred < old_pred) or (new_pred == old_pred and chunk.relevance_score > existing.relevance_score):
            chunk.relevance_score = max(chunk.relevance_score, existing.relevance_score)
            keep[is_dup_of] = chunk
            shingles_by_index[is_dup_of] = candidate_shingles
        else:
            existing.relevance_score = max(existing.relevance_score, chunk.relevance_score)
    return keep


def compress(chunks: list[ContextChunk]) -> list[ContextChunk]:
    """Run the full compression pipeline. Pure function — same input always
    produces the same output."""
    if not chunks:
        return []
    after_summary = _apply_kb_summaries(list(chunks))
    after_dedup = _dedup_exact(after_summary)
    after_merge = _merge_adjacent_same_source(after_dedup)
    after_cross = _cross_source_eliminate(after_merge)
    return after_cross
