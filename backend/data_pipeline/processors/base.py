from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from data_pipeline.utils import parse_timestamp


def row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    return dict(row)


def utc_day_bounds(target: datetime) -> tuple[datetime, datetime]:
    day_start = target.astimezone(timezone.utc).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return day_start, day_start + timedelta(days=1)


def compute_average_response_minutes(messages: list[dict[str, Any]]) -> float:
    pending_customer_at: datetime | None = None
    response_times: list[float] = []
    ordered = sorted(
        messages,
        key=lambda item: parse_timestamp(item.get("created_at")),
    )
    for message in ordered:
        created_at = parse_timestamp(message.get("created_at"))
        sender_type = str(message.get("sender_type") or "").strip().lower()
        if sender_type == "customer":
            pending_customer_at = pending_customer_at or created_at
            continue
        if sender_type not in {"agent", "ai"}:
            continue
        if pending_customer_at is None:
            continue
        delta = max((created_at - pending_customer_at).total_seconds() / 60.0, 0.0)
        response_times.append(delta)
        pending_customer_at = None
    if not response_times:
        return 0.0
    return round(sum(response_times) / len(response_times), 2)


class ProcessorBase:
    def __init__(self, db) -> None:
        self.db = db

    async def fetch_message_with_attachments(self, message_id: str) -> dict[str, Any]:
        message = row_to_dict(await self.db.fetchrow("SELECT * FROM messages WHERE id=$1 LIMIT 1", message_id))
        if not message:
            return {}
        attachments = await self.db.fetch(
            "SELECT id,file_type,file_url,file_name,file_size,created_at "
            "FROM message_attachments WHERE message_id=$1 ORDER BY created_at ASC",
            message_id,
        )
        message["attachments"] = [
            {
                "id": row["id"],
                "type": row["file_type"],
                "url": row["file_url"],
                "name": row["file_name"],
                "size": row["file_size"],
            }
            for row in attachments
        ]
        return message

    async def fetch_conversation_messages(
        self,
        conversation_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        rows = await self.db.fetch(
            "SELECT * FROM messages WHERE conversation_id=$1 ORDER BY created_at ASC LIMIT $2",
            conversation_id,
            max(1, int(limit)),
        )
        return [row_to_dict(row) for row in rows]

    async def fetch_messages_for_day(
        self,
        conversation_id: str,
        occurred_at: datetime,
    ) -> list[dict[str, Any]]:
        day_start, day_end = utc_day_bounds(occurred_at)
        rows = await self.db.fetch(
            "SELECT * FROM messages WHERE conversation_id=$1 AND created_at >= $2 AND created_at < $3 "
            "ORDER BY created_at ASC",
            conversation_id,
            day_start,
            day_end,
        )
        return [row_to_dict(row) for row in rows]
