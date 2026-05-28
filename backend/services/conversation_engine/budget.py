"""Adaptive token budget — picks a tier, then trims the prompt to fit.

Tiers and trimming order match the plan. Tokens are estimated with tiktoken
(cl100k_base — a good approximation for Gemini's tokenizer; differs by ~5%
which is well within the per-tier headroom).
"""

from __future__ import annotations

from typing import Literal

try:
    import tiktoken

    _ENCODER = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover — tiktoken is optional; degrade gracefully
    _ENCODER = None

from services.conversation_engine.compression import _PRECEDENCE
from services.conversation_engine.schemas import ContextChunk

Tier = Literal["simple", "medium", "heavy"]


_TIER_TARGETS: dict[Tier, int] = {
    "simple": 20_000,
    "medium": 80_000,
    "heavy": 250_000,
}
_TIER_CEILINGS: dict[Tier, int] = {
    "simple": 30_000,
    "medium": 120_000,
    "heavy": 400_000,
}


def count_tokens(text: str) -> int:
    if not text:
        return 0
    if _ENCODER is None:
        return max(1, len(text) // 4)  # ~4 chars per token
    return len(_ENCODER.encode(text))


def select_tier(query: str, chunks: list[ContextChunk]) -> Tier:
    source_count = len({chunk.source_type for chunk in chunks})
    chunk_count = len(chunks)
    query_len = len(query or "")
    if source_count >= 3 or chunk_count >= 15 or query_len > 300:
        return "heavy"
    if source_count >= 2 or chunk_count >= 4 or query_len >= 80:
        return "medium"
    return "simple"


def target_tokens(tier: Tier) -> int:
    return _TIER_TARGETS[tier]


def ceiling_tokens(tier: Tier) -> int:
    return _TIER_CEILINGS[tier]


def trim_to_budget(
    chunks: list[ContextChunk],
    history_turns: list[str],
    *,
    system_tokens: int,
    target_total_tokens: int,
) -> tuple[list[ContextChunk], list[str]]:
    """Return chunks/history that together fit inside the target token budget.

    Trimming order (per the plan):
      1. Drop oldest history turns first.
      2. Drop lowest relevance_score chunks.
      3. Higher-precedence sources are trimmed last.
    """
    chunks = list(chunks)
    history = list(history_turns)
    budget = max(1000, target_total_tokens - system_tokens)

    def _total() -> int:
        return sum(count_tokens(c.content) for c in chunks) + sum(count_tokens(t) for t in history)

    # Step 1: trim oldest history.
    while history and _total() > budget:
        history.pop(0)

    if _total() <= budget:
        return chunks, history

    # Step 2 + 3: drop lowest-score chunks first, but never drop higher
    # precedence before lower precedence. Score = (precedence, relevance).
    # We sort the "drop queue" so least-valuable chunks come first.
    drop_order = sorted(
        range(len(chunks)),
        key=lambda i: (-_PRECEDENCE.get(chunks[i].source_type, 9), chunks[i].relevance_score),
    )
    surviving = list(chunks)
    for idx in drop_order:
        if _total() <= budget:
            break
        try:
            target = chunks[idx]
            surviving.remove(target)
            chunks = surviving
        except ValueError:
            continue

    return chunks, history
