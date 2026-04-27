"""Durable event outbox pattern for reliable event dispatch.

Events are written to the ``event_outbox`` table within the *same*
database transaction as the business operation, then a background
worker polls, dispatches, and marks them as processed with retry
logic and idempotency guarantees.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_OUTBOX_TABLE = "event_outbox"
_MAX_RETRIES = 5
_RETRY_BACKOFF_SECONDS = 2.0
_POLL_INTERVAL_SECONDS = 5.0


async def enqueue_event(
    db,
    *,
    event_type: str,
    payload: dict[str, Any],
    tenant_id: str = "",
    idempotency_key: str = "",
    aggregate_id: str = "",
) -> str:
    """Write an event to the outbox within the caller's transaction."""
    event_id = str(uuid.uuid4())
    idem_key = idempotency_key or event_id
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        f"""
        INSERT INTO {_OUTBOX_TABLE}
            (id, event_type, payload, tenant_id, idempotency_key, aggregate_id,
             retry_count, created_at, processed_at)
        VALUES ($1, $2, $3::jsonb, $4, $5, $6, 0, $7, NULL)
        ON CONFLICT (idempotency_key) DO NOTHING
        """,
        event_id,
        event_type,
        json.dumps(payload, default=str),
        tenant_id,
        idem_key,
        aggregate_id,
        now,
    )
    return event_id


async def mark_processed(db, event_id: str) -> None:
    """Mark an outbox event as successfully processed."""
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        f"UPDATE {_OUTBOX_TABLE} SET processed_at=$1 WHERE id=$2",
        now,
        event_id,
    )


async def increment_retry(db, event_id: str) -> None:
    """Increment the retry counter for a failed event."""
    await db.execute(
        f"UPDATE {_OUTBOX_TABLE} SET retry_count=retry_count+1 WHERE id=$1",
        event_id,
    )


async def fetch_pending_events(
    db,
    *,
    batch_size: int = 20,
    max_retries: int = _MAX_RETRIES,
) -> list[dict[str, Any]]:
    """Fetch unprocessed events that have not exceeded the retry limit."""
    rows = await db.fetch(
        f"""
        SELECT id, event_type, payload, tenant_id, idempotency_key,
               aggregate_id, retry_count, created_at
        FROM {_OUTBOX_TABLE}
        WHERE processed_at IS NULL AND retry_count < $1
        ORDER BY created_at ASC
        LIMIT $2
        """,
        max_retries,
        batch_size,
    )
    return [dict(row) for row in rows]


class OutboxWorker:
    """Background worker that polls and dispatches outbox events."""

    def __init__(
        self,
        db,
        *,
        handler=None,
        poll_interval: float = _POLL_INTERVAL_SECONDS,
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        self._db = db
        self._handler = handler  # async callable(event: dict) -> None
        self._poll_interval = poll_interval
        self._max_retries = max_retries
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="outbox_worker")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while self._running:
            try:
                events = await fetch_pending_events(self._db, max_retries=self._max_retries)
                for event in events:
                    await self._dispatch(event)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Outbox worker poll error")
            await asyncio.sleep(self._poll_interval)

    async def _dispatch(self, event: dict) -> None:
        event_id = event.get("id", "")
        try:
            if self._handler:
                await self._handler(event)
            await mark_processed(self._db, event_id)
        except Exception:
            logger.warning(
                "Outbox event dispatch failed event_id=%s retry=%s",
                event_id,
                event.get("retry_count", 0),
            )
            await increment_retry(self._db, event_id)
