from __future__ import annotations

from datetime import datetime
from typing import Any

from core.utils import parse_dt


def row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    return dict(row)


async def fetch_customer(db, customer_id: str) -> dict[str, Any]:
    if not str(customer_id or "").strip():
        return {}
    return row_to_dict(await db.fetchrow("SELECT * FROM customers WHERE id=$1 LIMIT 1", customer_id))


async def fetch_lead(db, lead_id: str) -> dict[str, Any]:
    if not str(lead_id or "").strip():
        return {}
    return row_to_dict(await db.fetchrow("SELECT * FROM leads WHERE id=$1 LIMIT 1", lead_id))


async def fetch_conversation(db, conversation_id: str) -> dict[str, Any]:
    if not str(conversation_id or "").strip():
        return {}
    return row_to_dict(await db.fetchrow("SELECT * FROM conversations WHERE id=$1 LIMIT 1", conversation_id))


async def fetch_recent_messages(
    db,
    conversation_id: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if not str(conversation_id or "").strip():
        return []
    rows = await db.fetch(
        "SELECT * FROM messages WHERE conversation_id=$1 ORDER BY created_at DESC LIMIT $2",
        conversation_id,
        max(1, int(limit)),
    )
    return [row_to_dict(row) for row in reversed(rows)]


async def fetch_company_ai_threshold(db, company_id: str, default: float = 0.7) -> float:
    if not str(company_id or "").strip():
        return default
    row = row_to_dict(
        await db.fetchrow(
            "SELECT ai_confidence_threshold FROM company_settings WHERE company_id=$1 LIMIT 1",
            company_id,
        )
    )
    try:
        value = float(row.get("ai_confidence_threshold") or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


async def fetch_messages_for_day(
    db,
    conversation_id: str,
    occurred_at: datetime | None,
) -> list[dict[str, Any]]:
    if not str(conversation_id or "").strip():
        return []
    target = occurred_at or datetime.utcnow()
    rows = await db.fetch(
        "SELECT * FROM messages "
        "WHERE conversation_id=$1 AND DATE(created_at AT TIME ZONE 'UTC')=$2 "
        "ORDER BY created_at ASC",
        conversation_id,
        target.date(),
    )
    return [row_to_dict(row) for row in rows]


def average_response_minutes(messages: list[dict[str, Any]]) -> float:
    pending_at: datetime | None = None
    samples: list[float] = []
    for message in messages:
        sender_type = str(message.get("sender_type") or "").strip().lower()
        created_at = parse_dt(message.get("created_at"))
        if created_at is None:
            continue
        if sender_type == "customer":
            pending_at = pending_at or created_at
            continue
        if pending_at is None:
            continue
        samples.append(max((created_at - pending_at).total_seconds() / 60.0, 0.0))
        pending_at = None
    if not samples:
        return 0.0
    return round(sum(samples) / len(samples), 2)
