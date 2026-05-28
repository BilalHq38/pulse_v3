"""Conversation-memory persistence.

Two tables:
  - ai_conversation_turns — one row per turn (created by `persist_turn`).
  - ai_conversation_summaries — rolling summary, upserted by
    `update_rolling_summary` only when turn_count > _SUMMARISE_AFTER.

The rolling-summary step is the *only* place in the engine that can make a
live LLM call outside the main generation. It runs at most once per request
and only after the threshold, so most turns never trigger it.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Iterable

from services.conversation_engine.schemas import (
    Mode,
    ProductLink,
    SourceType,
    TokenUsage,
)


logger = logging.getLogger(__name__)
_SUMMARISE_AFTER = 10  # only summarise once a session crosses this many turns
_RECENT_TURN_FETCH_LIMIT = 20


def _new_turn_id() -> str:
    # The id column is plain TEXT; we use a monotonic + entropy id rather than
    # a UUID to keep the database friendly to range scans by created_at.
    return f"t_{int(time.time() * 1000):x}_{int.from_bytes(__import__('os').urandom(4), 'big'):x}"


async def fetch_recent_turns(db, *, company_id: str, session_id: str) -> list[dict]:
    if not company_id or not session_id:
        return []
    try:
        rows = await db.fetch(
            "SELECT id, turn_index, user_message, ai_response, sources_used, product_links "
            "FROM ai_conversation_turns WHERE company_id = $1 AND session_id = $2 "
            "ORDER BY turn_index DESC LIMIT $3",
            company_id,
            session_id,
            _RECENT_TURN_FETCH_LIMIT,
        )
    except Exception:
        return []
    return [dict(row) for row in reversed(rows or [])]


def history_as_dialogue(turns: Iterable[dict]) -> list[str]:
    """Render persisted turns into a flat dialogue list for the prompt builder."""
    lines: list[str] = []
    for turn in turns:
        user = (turn.get("user_message") or "").strip()
        ai = (turn.get("ai_response") or "").strip()
        if user:
            lines.append(f"User: {user}")
        if ai:
            lines.append(f"Assistant: {ai}")
    return lines


async def fetch_rolling_summary(db, *, company_id: str, session_id: str) -> str:
    try:
        row = await db.fetchrow(
            "SELECT summary FROM ai_conversation_summaries WHERE company_id = $1 AND session_id = $2",
            company_id,
            session_id,
        )
    except Exception:
        return ""
    return str(row.get("summary") if row else "") or ""


async def next_turn_index(db, *, company_id: str, session_id: str) -> int:
    try:
        row = await db.fetchrow(
            "SELECT COALESCE(MAX(turn_index), 0) AS max_idx FROM ai_conversation_turns "
            "WHERE company_id = $1 AND session_id = $2",
            company_id,
            session_id,
        )
    except Exception:
        return 1
    return int((row or {}).get("max_idx") or 0) + 1


async def persist_turn(
    db,
    *,
    company_id: str,
    session_id: str,
    customer_id: str,
    turn_index: int,
    user_message: str,
    ai_response: str,
    sources_used: list[SourceType],
    product_links: list[ProductLink],
    confidence: float,
    active_template: str,
    token_usage: TokenUsage,
    mode: Mode,
) -> str:
    turn_id = _new_turn_id()
    payload = {
        "id": turn_id,
        "company_id": company_id,
        "session_id": session_id,
        "customer_id": customer_id or "",
        "turn_index": int(turn_index),
        "user_message": user_message,
        "ai_response": ai_response,
        "sources_used": json.dumps(list(sources_used)),
        "product_links": json.dumps([{"product_id": pl.product_id, "url": pl.url} for pl in product_links]),
        "confidence": float(confidence),
        "active_template": active_template or "",
        "token_usage": json.dumps({
            "prompt": token_usage.prompt,
            "completion": token_usage.completion,
            "total": token_usage.total,
        }),
        "mode": mode,
    }
    try:
        await db.execute(
            "INSERT INTO ai_conversation_turns "
            "(id, company_id, session_id, customer_id, turn_index, user_message, ai_response, "
            " sources_used, product_links, confidence, active_template, token_usage, mode) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10,$11,$12::jsonb,$13)",
            payload["id"], payload["company_id"], payload["session_id"], payload["customer_id"],
            payload["turn_index"], payload["user_message"], payload["ai_response"],
            payload["sources_used"], payload["product_links"], payload["confidence"],
            payload["active_template"], payload["token_usage"], payload["mode"],
        )
    except Exception as exc:
        logger.warning("persist_turn_failed company_id=%s session_id=%s error=%s", company_id, session_id, exc)
    return turn_id


async def upsert_rolling_summary(
    db,
    *,
    company_id: str,
    session_id: str,
    summary: str,
    covers_through_turn: int,
) -> None:
    summary = (summary or "").strip()
    if not summary:
        return
    try:
        await db.execute(
            "INSERT INTO ai_conversation_summaries (id, company_id, session_id, summary, covers_through_turn) "
            "VALUES ($1, $2, $3, $4, $5) "
            "ON CONFLICT (company_id, session_id) DO UPDATE SET "
            "  summary = EXCLUDED.summary, "
            "  covers_through_turn = EXCLUDED.covers_through_turn, "
            "  updated_at = NOW()",
            f"s_{int(time.time() * 1000):x}",
            company_id,
            session_id,
            summary,
            int(covers_through_turn),
        )
    except Exception as exc:
        logger.warning("upsert_rolling_summary_failed error=%s", exc)


def should_summarise(turn_count: int) -> bool:
    return turn_count > _SUMMARISE_AFTER
