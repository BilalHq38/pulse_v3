"""Ingest-time knowledge-base summariser.

A KB article is summarised exactly once at write time (one Gemini call), and
the summary is cached on the row. The request-time compression layer in
`conversation_engine.compression` reads the cached summary and never makes a
live LLM call. If summary generation fails or the fact-preservation guard
flags a regression, we leave `summary` empty and the compression layer falls
back to the original `content` — so the engine still works without a summary.

Entry points:
- `enqueue_kb_summary_job(db, *, company_id, kb_id, content)` — fire-and-forget
  hook to call from KB create/update routes.
- `summarise_kb_row(db, *, company_id, kb_id)` — the actual worker job; safe
  to call directly from a backfill script.
"""

from __future__ import annotations

import logging
import re

from services.ai_service.llm_client import call_gemini
from shared.background_queue import get_background_queue


logger = logging.getLogger(__name__)

_MIN_LENGTH_TO_SUMMARISE = 800
_SUMMARY_MAX_TOKENS_HINT = 200
_PROMPT = (
    "Summarise the knowledge-base article below in at most 200 tokens. "
    "Preserve every proper-noun product name, policy name, dollar amount, "
    "percentage, time period, and named procedure exactly as written. Plain "
    "prose, no markdown, no preamble.\n\n"
    "Article:\n---\n{content}\n---\nSummary:"
)
# Tokens we count as 'facts' for the preservation guard. The intent is to
# catch dropped product names and price/percentage figures.
_FACT_RE = re.compile(r"(?:[A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+){0,3}|\$\s?\d[\d,]*(?:\.\d+)?|\d+\s?%|\d+\s?(?:days?|hours?|weeks?))")


def _extract_facts(text: str) -> set[str]:
    return {m.group(0).strip() for m in _FACT_RE.finditer(text or "")}


def _summary_preserves_facts(source: str, summary: str) -> bool:
    """At least 75% of the facts mined from the source must appear in the
    summary. Threshold is chosen to allow legitimate paraphrase while
    catching wholesale drops of key terms."""
    source_facts = _extract_facts(source)
    if not source_facts:
        return True
    summary_norm = (summary or "").lower()
    preserved = sum(1 for fact in source_facts if fact.lower() in summary_norm)
    return preserved >= max(1, int(0.75 * len(source_facts)))


async def summarise_kb_row(db, *, company_id: str, kb_id: str) -> bool:
    """Generate and persist a summary for a single KB row.

    Returns True if a non-empty summary was written, False otherwise.
    Idempotent — re-running for the same row simply overwrites the summary.
    """
    if not company_id or not kb_id:
        return False
    try:
        row = await db.fetchrow(
            "SELECT id, content FROM knowledge_base WHERE company_id = $1 AND id = $2",
            company_id,
            kb_id,
        )
    except Exception as exc:
        logger.warning("kb_summary_lookup_failed kb_id=%s error=%s", kb_id, exc)
        return False
    if not row:
        return False
    content = str(row.get("content") or "").strip()
    if not content or len(content) < _MIN_LENGTH_TO_SUMMARISE:
        # Short rows don't need a summary; the compression layer will use the
        # full content directly. Mark the timestamp so we don't keep retrying.
        try:
            await db.execute(
                "UPDATE knowledge_base SET summary = '', summary_generated_at = NOW() WHERE id = $1",
                kb_id,
            )
        except Exception as exc:
            logger.debug("kb_summary_short_row_timestamp_failed kb_id=%s error=%s", kb_id, exc)
        return False

    try:
        raw = await call_gemini(_PROMPT.format(content=content))
    except Exception as exc:
        logger.warning("kb_summary_llm_call_failed kb_id=%s error=%s", kb_id, exc)
        return False

    summary = (raw or "").strip()
    if not summary:
        return False

    if not _summary_preserves_facts(content, summary):
        logger.info("kb_summary_failed_fact_guard kb_id=%s — leaving summary empty", kb_id)
        try:
            await db.execute(
                "UPDATE knowledge_base SET summary = '', summary_generated_at = NOW() WHERE id = $1",
                kb_id,
            )
        except Exception as exc:
            logger.debug("kb_summary_fact_guard_timestamp_failed kb_id=%s error=%s", kb_id, exc)
        return False

    # Trim hard at 4 * token_hint chars (≈ 4 chars/token) as a defensive cap.
    summary = summary[: _SUMMARY_MAX_TOKENS_HINT * 4]
    try:
        await db.execute(
            "UPDATE knowledge_base SET summary = $1, summary_generated_at = NOW() WHERE id = $2",
            summary,
            kb_id,
        )
    except Exception as exc:
        logger.warning("kb_summary_persist_failed kb_id=%s error=%s", kb_id, exc)
        return False
    logger.info("kb_summary_generated kb_id=%s len=%s", kb_id, len(summary))
    return True


async def enqueue_kb_summary_job(db, *, company_id: str, kb_id: str, content: str | None = None) -> None:
    """Schedule a summary job for the given KB row. No-op when the background
    queue is disabled (e.g. in unit tests)."""
    if not company_id or not kb_id:
        return
    if content is not None and len(content) < _MIN_LENGTH_TO_SUMMARISE:
        # Don't waste a queue slot for content too short to need summarisation.
        return
    queue = get_background_queue()
    if queue is None:
        return
    try:
        await queue.enqueue_coroutine(
            summarise_kb_row(db, company_id=company_id, kb_id=kb_id),
            name=f"kb-summary-{kb_id}",
            idempotency_key=f"kb_summary:{company_id}:{kb_id}",
        )
    except Exception as exc:
        logger.debug("kb_summary_enqueue_failed kb_id=%s error=%s", kb_id, exc)
