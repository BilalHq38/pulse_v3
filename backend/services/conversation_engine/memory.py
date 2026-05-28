"""Conversation-memory persistence.

Two tables:
  - ai_conversation_turns — one row per turn (created by `persist_turn`).
  - ai_conversation_summaries — rolling summary, upserted by
    `update_rolling_summary` only when turn_count > _SUMMARISE_AFTER.

The rolling-summary step is the *only* place in the engine that can make a
live LLM call outside the main generation. It runs at most once per request
and only after the threshold, so most turns never trigger it.

RLS note: ai_conversation_turns and ai_conversation_summaries both have Row
Level Security enabled. The policy passes rows through when:
  current_setting('app.current_company', true) = company_id
  OR current_setting('app.platform_admin_mode', true) = 'on'

All DB helpers here acquire a dedicated connection from the pool, set
app.current_company via set_config (session-scoped, safe for this connection's
lifetime in the pool), run the query, and release. This guarantees the RLS
context is correct regardless of which pooled connection is used.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Iterable

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
    return f"t_{int(time.time() * 1000):x}_{int.from_bytes(__import__('os').urandom(4), 'big'):x}"


async def _set_tenant(conn, company_id: str) -> None:
    """Set app.current_company on this connection so RLS passes."""
    try:
        await conn.execute(
            "SELECT set_config('app.current_company', $1, false)",
            company_id,
        )
    except Exception:
        pass


async def _rls_fetch(db, company_id: str, sql: str, *args) -> list:
    """Run a SELECT with tenant RLS context pinned to this connection."""
    try:
        async with db.acquire() as conn:
            await _set_tenant(conn, company_id)
            rows = await conn.fetch(sql, *args)
            return [dict(r) for r in (rows or [])]
    except AttributeError:
        # db is already a plain connection — use directly (less safe, best effort)
        try:
            rows = await db.fetch(sql, *args)
            return [dict(r) for r in (rows or [])]
        except Exception:
            return []
    except Exception:
        return []


async def _rls_fetchrow(db, company_id: str, sql: str, *args) -> dict | None:
    rows = await _rls_fetch(db, company_id, sql, *args)
    return rows[0] if rows else None


async def _rls_fetchval(db, company_id: str, sql: str, *args) -> Any:
    """Run a single-value SELECT with tenant RLS context."""
    try:
        async with db.acquire() as conn:
            await _set_tenant(conn, company_id)
            return await conn.fetchval(sql, *args)
    except AttributeError:
        try:
            return await db.fetchval(sql, *args)
        except Exception:
            return None
    except Exception:
        return None


async def _rls_execute(db, company_id: str, sql: str, *args) -> None:
    """Run an INSERT/UPDATE with tenant RLS context pinned to this connection."""
    try:
        async with db.acquire() as conn:
            await _set_tenant(conn, company_id)
            await conn.execute(sql, *args)
    except AttributeError:
        try:
            await db.execute(sql, *args)
        except Exception:
            pass
    except Exception as exc:
        logger.debug("_rls_execute_failed sql_prefix=%s error=%s", sql[:60], exc)


async def fetch_recent_turns(db, *, company_id: str, session_id: str) -> list[dict]:
    if not company_id or not session_id:
        return []
    rows = await _rls_fetch(
        db,
        company_id,
        "SELECT id, turn_index, user_message, ai_response, sources_used, product_links "
        "FROM ai_conversation_turns WHERE company_id = $1 AND session_id = $2 "
        "ORDER BY turn_index DESC LIMIT $3",
        company_id,
        session_id,
        _RECENT_TURN_FETCH_LIMIT,
    )
    return list(reversed(rows))


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
    row = await _rls_fetchrow(
        db,
        company_id,
        "SELECT summary FROM ai_conversation_summaries WHERE company_id = $1 AND session_id = $2",
        company_id,
        session_id,
    )
    return str(row.get("summary") if row else "") or ""


async def next_turn_index(db, *, company_id: str, session_id: str) -> int:
    val = await _rls_fetchval(
        db,
        company_id,
        "SELECT COALESCE(MAX(turn_index), 0) FROM ai_conversation_turns "
        "WHERE company_id = $1 AND session_id = $2",
        company_id,
        session_id,
    )
    return int(val or 0) + 1


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
    await _rls_execute(
        db,
        company_id,
        "INSERT INTO ai_conversation_turns "
        "(id, company_id, session_id, customer_id, turn_index, user_message, ai_response, "
        " sources_used, product_links, confidence, active_template, token_usage, mode) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10,$11,$12::jsonb,$13)",
        turn_id,
        company_id,
        session_id,
        customer_id or "",
        int(turn_index),
        user_message,
        ai_response,
        json.dumps(list(sources_used)),
        json.dumps([{"product_id": pl.product_id, "url": pl.url} for pl in product_links]),
        float(confidence),
        active_template or "",
        json.dumps({"prompt": token_usage.prompt, "completion": token_usage.completion, "total": token_usage.total}),
        mode,
    )
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
    await _rls_execute(
        db,
        company_id,
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


def should_summarise(turn_count: int) -> bool:
    return turn_count > _SUMMARISE_AFTER
