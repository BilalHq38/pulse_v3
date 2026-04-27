from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from services.identity_service.app.db.database import AsyncSessionLocal
from services.identity_service.app.db.models import EventOutbox, IdentityEvent
from services.identity_service.app.services import cache as cache_service
from services.identity_service.app.services.observability import increment_counter
from services.identity_service.app.utils.utils import utcnow

logger = logging.getLogger(__name__)

_EVENT_STREAM_NAME = (
    os.environ.get("IDENTITY_EVENT_STREAM_NAME", "identity.events") or "identity.events"
).strip() or "identity.events"
_EVENT_RETRY_POLL_SECONDS = max(1.0, float(os.environ.get("IDENTITY_EVENT_RETRY_POLL_SECONDS", "5") or 5))
_EVENT_MAX_RETRIES = max(1, int(os.environ.get("IDENTITY_EVENT_MAX_RETRIES", "8") or 8))
_EVENT_RETRY_BACKOFF_SECONDS = max(1.0, float(os.environ.get("IDENTITY_EVENT_RETRY_BACKOFF_SECONDS", "2") or 2))
_EVENT_MAX_BACKOFF_SECONDS = max(10.0, float(os.environ.get("IDENTITY_EVENT_MAX_BACKOFF_SECONDS", "300") or 300))


async def _ensure_dead_letter_queue_table(db) -> None:
    await db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS dead_letter_queue (
                id TEXT PRIMARY KEY,
                task_name TEXT NOT NULL DEFAULT '',
                event_id TEXT NOT NULL DEFAULT '',
                trace_id TEXT NOT NULL DEFAULT '',
                company_id TEXT NOT NULL DEFAULT '',
                channel TEXT NOT NULL DEFAULT '',
                source_queue TEXT NOT NULL DEFAULT '',
                event_type TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                retry_count INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 3,
                status TEXT NOT NULL DEFAULT 'failed',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                resolved_at TIMESTAMPTZ
            )
            """
        )
    )
    for ddl in (
        "ALTER TABLE IF EXISTS dead_letter_queue ADD COLUMN IF NOT EXISTS task_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE IF EXISTS dead_letter_queue ADD COLUMN IF NOT EXISTS event_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE IF EXISTS dead_letter_queue ADD COLUMN IF NOT EXISTS trace_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE IF EXISTS dead_letter_queue ADD COLUMN IF NOT EXISTS channel TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE IF EXISTS dead_letter_queue ADD COLUMN IF NOT EXISTS error TEXT NOT NULL DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS idx_dlq_status ON dead_letter_queue(status)",
        "CREATE INDEX IF NOT EXISTS idx_dlq_company_id ON dead_letter_queue(company_id)",
        "CREATE INDEX IF NOT EXISTS idx_dlq_created_at ON dead_letter_queue(created_at)",
    ):
        await db.execute(text(ddl))


async def _write_dead_letter_record(
    db,
    *,
    task_name: str,
    event: IdentityEvent,
    outbox: EventOutbox,
    payload: dict[str, Any],
    error_message: str,
) -> None:
    await _ensure_dead_letter_queue_table(db)
    trace_id = str((payload or {}).get("trace_id") or "").strip()
    event_id = str(event.event_id)
    company_id = str(event.tenant_id or "").strip()
    channel = "identity"
    serialized_payload = json.dumps(payload or {}, ensure_ascii=True, separators=(",", ":"), default=str)
    capped_error = str(error_message or "unknown error")[:4000]
    await db.execute(
        text(
            """
            INSERT INTO dead_letter_queue(
                id, task_name, event_id, trace_id, company_id, channel,
                source_queue, event_type, payload, error, error_message,
                retry_count, max_retries, status, created_at, updated_at
            ) VALUES(
                :id, :task_name, :event_id, :trace_id, :company_id, :channel,
                :source_queue, :event_type, :payload, :error, :error_message,
                :retry_count, :max_retries, 'failed', NOW(), NOW()
            )
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "task_name": str(task_name or "identity_event_retry_worker").strip() or "identity_event_retry_worker",
            "event_id": event_id,
            "trace_id": trace_id,
            "company_id": company_id,
            "channel": channel,
            "source_queue": "identity.events",
            "event_type": str(event.event_type or "").strip(),
            "payload": serialized_payload,
            "error": capped_error,
            "error_message": capped_error,
            "retry_count": int(outbox.retry_count or 0),
            "max_retries": int(_EVENT_MAX_RETRIES),
        },
    )


@dataclass(slots=True)
class EventRetryWorkerHandle:
    task: asyncio.Task
    stop_event: asyncio.Event


class IdentityEventPublisher:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker = AsyncSessionLocal,
        stream_name: str = _EVENT_STREAM_NAME,
    ) -> None:
        self._session_factory = session_factory
        self._stream_name = (stream_name or _EVENT_STREAM_NAME).strip() or _EVENT_STREAM_NAME
        self._max_retries = _EVENT_MAX_RETRIES

    async def emit(
        self,
        *,
        tenant_id: str,
        event_type: str,
        payload: dict[str, Any],
        aggregate_customer_id: str | None = None,
        idempotency_key: str | None = None,
        dispatch_now: bool = True,
    ) -> str:
        safe_tenant_id = str(tenant_id or "").strip()
        safe_event_type = str(event_type or "").strip()
        safe_idempotency_key = str(idempotency_key or "").strip() or None
        if not safe_tenant_id:
            raise ValueError("tenant_id is required for identity event emission")
        if not safe_event_type:
            raise ValueError("event_type is required for identity event emission")

        event_id: uuid.UUID | None = None
        outbox_id: uuid.UUID | None = None

        async with self._session_factory() as db:
            existing_event = None
            if safe_idempotency_key:
                result = await db.execute(
                    select(IdentityEvent).where(
                        IdentityEvent.tenant_id == safe_tenant_id,
                        IdentityEvent.idempotency_key == safe_idempotency_key,
                    )
                )
                existing_event = result.scalar_one_or_none()

            if existing_event is not None:
                event_id = existing_event.event_id
                outbox_result = await db.execute(
                    select(EventOutbox).where(EventOutbox.event_id == existing_event.event_id)
                )
                existing_outbox = outbox_result.scalar_one_or_none()
                if existing_outbox is not None:
                    outbox_id = existing_outbox.outbox_id
                else:
                    created_outbox = EventOutbox(
                        event_id=existing_event.event_id,
                        tenant_id=safe_tenant_id,
                        stream_name=self._stream_name,
                        idempotency_key=safe_idempotency_key,
                        payload=self._build_envelope(
                            event_id=str(existing_event.event_id),
                            tenant_id=safe_tenant_id,
                            event_type=safe_event_type,
                            payload=payload,
                            aggregate_customer_id=aggregate_customer_id,
                            created_at=existing_event.created_at.isoformat(),
                        ),
                        status="queued",
                    )
                    db.add(created_outbox)
                    await db.commit()
                    outbox_id = created_outbox.outbox_id
            else:
                created_at = utcnow()
                created_event = IdentityEvent(
                    tenant_id=safe_tenant_id,
                    event_type=safe_event_type,
                    aggregate_customer_id=self._parse_uuid(aggregate_customer_id),
                    idempotency_key=safe_idempotency_key,
                    payload=payload,
                    status="pending",
                    created_at=created_at,
                )
                db.add(created_event)
                await db.flush()

                created_outbox = EventOutbox(
                    event_id=created_event.event_id,
                    tenant_id=safe_tenant_id,
                    stream_name=self._stream_name,
                    idempotency_key=safe_idempotency_key,
                    payload=self._build_envelope(
                        event_id=str(created_event.event_id),
                        tenant_id=safe_tenant_id,
                        event_type=safe_event_type,
                        payload=payload,
                        aggregate_customer_id=aggregate_customer_id,
                        created_at=created_at.isoformat(),
                    ),
                    status="queued",
                    retry_count=0,
                )
                db.add(created_outbox)

                try:
                    await db.commit()
                except IntegrityError:
                    await db.rollback()
                    if not safe_idempotency_key:
                        raise
                    existing = await db.execute(
                        select(IdentityEvent).where(
                            IdentityEvent.tenant_id == safe_tenant_id,
                            IdentityEvent.idempotency_key == safe_idempotency_key,
                        )
                    )
                    deduped = existing.scalar_one_or_none()
                    if deduped is None:
                        raise
                    event_id = deduped.event_id
                    existing_outbox = await db.execute(
                        select(EventOutbox).where(EventOutbox.event_id == deduped.event_id)
                    )
                    outbox = existing_outbox.scalar_one_or_none()
                    outbox_id = outbox.outbox_id if outbox is not None else None
                else:
                    event_id = created_event.event_id
                    outbox_id = created_outbox.outbox_id

        if event_id is None:
            raise RuntimeError("Failed to persist identity event")

        increment_counter(
            "identity.events.persisted", labels={"event_type": safe_event_type, "tenant_id": safe_tenant_id}
        )

        if dispatch_now and outbox_id is not None:
            asyncio.create_task(self.dispatch_outbox_record(outbox_id))

        return str(event_id)

    async def dispatch_outbox_record(self, outbox_id: str | uuid.UUID) -> bool:
        safe_outbox_id = self._parse_uuid(outbox_id)
        if safe_outbox_id is None:
            return False

        async with self._session_factory() as db:
            result = await db.execute(
                select(EventOutbox, IdentityEvent)
                .join(IdentityEvent, IdentityEvent.event_id == EventOutbox.event_id)
                .where(EventOutbox.outbox_id == safe_outbox_id)
            )
            row = result.first()
            if row is None:
                return False

            outbox, event = row
            if outbox.status == "published":
                return True

            envelope = outbox.payload or {
                "event_id": str(event.event_id),
                "tenant_id": event.tenant_id,
                "event_type": event.event_type,
                "payload": event.payload,
                "created_at": event.created_at.isoformat(),
            }

            try:
                await self._publish_to_stream(outbox.stream_name or self._stream_name, envelope)
            except Exception as exc:
                self._mark_retry(outbox, event, str(exc))
                if outbox.status == "failed":
                    await _write_dead_letter_record(
                        db,
                        task_name="identity_event_retry_worker",
                        event=event,
                        outbox=outbox,
                        payload=envelope,
                        error_message=str(exc),
                    )
                await db.commit()
                increment_counter(
                    "identity.events.publish_failed",
                    labels={"event_type": event.event_type, "tenant_id": event.tenant_id},
                )
                logger.warning(
                    "identity event publish failed tenant_id=%s event_type=%s outbox_id=%s retry_count=%s error=%s",
                    event.tenant_id,
                    event.event_type,
                    outbox.outbox_id,
                    outbox.retry_count,
                    exc,
                )
                return False

            published_at = utcnow()
            outbox.status = "published"
            outbox.published_at = published_at
            outbox.last_error = None
            outbox.next_retry_at = None
            outbox.updated_at = published_at
            event.status = "published"
            event.error_message = None
            event.published_at = published_at
            await db.commit()

            increment_counter(
                "identity.events.published",
                labels={"event_type": event.event_type, "tenant_id": event.tenant_id},
            )
            return True

    async def dispatch_due_outbox(self, *, batch_size: int = 100) -> int:
        now = utcnow()
        async with self._session_factory() as db:
            result = await db.execute(
                select(EventOutbox.outbox_id)
                .where(
                    EventOutbox.status.in_(("queued", "failed")),
                    EventOutbox.retry_count < self._max_retries,
                    or_(EventOutbox.next_retry_at.is_(None), EventOutbox.next_retry_at <= now),
                )
                .order_by(EventOutbox.created_at.asc())
                .limit(max(1, int(batch_size)))
            )
            due_ids = [row[0] for row in result.all()]

        delivered = 0
        for due_id in due_ids:
            if await self.dispatch_outbox_record(due_id):
                delivered += 1
        return delivered

    async def _publish_to_stream(self, stream_name: str, envelope: dict[str, Any]) -> None:
        redis_client = cache_service.redis_client
        if redis_client is None:
            raise RuntimeError("Redis stream publishing unavailable")

        stream_payload = json.dumps(envelope, default=str, ensure_ascii=True, separators=(",", ":"))
        await redis_client.xadd(
            stream_name,
            {"event": stream_payload},
            maxlen=20000,
            approximate=True,
        )

    def _mark_retry(self, outbox: EventOutbox, event: IdentityEvent, error_text: str) -> None:
        outbox.retry_count = int(outbox.retry_count or 0) + 1
        capped_error = str(error_text or "unknown error")[:1000]
        now = utcnow()

        event.error_message = capped_error
        outbox.last_error = capped_error
        outbox.updated_at = now

        if outbox.retry_count >= self._max_retries:
            outbox.status = "failed"
            outbox.next_retry_at = None
            event.status = "failed"
            return

        delay_seconds = min(
            _EVENT_MAX_BACKOFF_SECONDS,
            _EVENT_RETRY_BACKOFF_SECONDS * (2 ** max(0, outbox.retry_count - 1)),
        )
        outbox.status = "queued"
        outbox.next_retry_at = now + timedelta(seconds=delay_seconds)
        event.status = "pending"

    @staticmethod
    def _build_envelope(
        *,
        event_id: str,
        tenant_id: str,
        event_type: str,
        payload: dict[str, Any],
        aggregate_customer_id: str | None,
        created_at: str,
    ) -> dict[str, Any]:
        return {
            "event_id": event_id,
            "tenant_id": tenant_id,
            "event_type": event_type,
            "aggregate_customer_id": aggregate_customer_id,
            "payload": payload,
            "created_at": created_at,
        }

    @staticmethod
    def _parse_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError):
            return None


_publisher: IdentityEventPublisher | None = None
_retry_worker_handle: EventRetryWorkerHandle | None = None


async def init_event_publisher() -> None:
    global _publisher
    if _publisher is None:
        _publisher = IdentityEventPublisher()


async def start_event_retry_worker() -> EventRetryWorkerHandle | None:
    global _publisher, _retry_worker_handle
    if _publisher is None:
        await init_event_publisher()
    if _publisher is None:
        return None
    if _retry_worker_handle is not None and not _retry_worker_handle.task.done():
        return _retry_worker_handle

    stop_event = asyncio.Event()
    task = asyncio.create_task(_retry_worker_loop(_publisher, stop_event), name="identity-event-retry-worker")
    _retry_worker_handle = EventRetryWorkerHandle(task=task, stop_event=stop_event)
    return _retry_worker_handle


async def stop_event_retry_worker() -> None:
    global _retry_worker_handle
    if _retry_worker_handle is None:
        return
    _retry_worker_handle.stop_event.set()
    _retry_worker_handle.task.cancel()
    try:
        await _retry_worker_handle.task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("identity event retry worker shutdown failed")
    _retry_worker_handle = None


async def close_event_publisher() -> None:
    global _publisher
    await stop_event_retry_worker()
    _publisher = None


async def emit_identity_event(
    *,
    tenant_id: str,
    event_type: str,
    payload: dict[str, Any],
    aggregate_customer_id: str | None = None,
    idempotency_key: str | None = None,
    dispatch_now: bool = True,
) -> str | None:
    if _publisher is None:
        await init_event_publisher()
    if _publisher is None:
        return None

    return await _publisher.emit(
        tenant_id=tenant_id,
        event_type=event_type,
        payload=payload,
        aggregate_customer_id=aggregate_customer_id,
        idempotency_key=idempotency_key,
        dispatch_now=dispatch_now,
    )


async def event_system_snapshot() -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        pending = await db.scalar(
            select(func.count()).select_from(EventOutbox).where(EventOutbox.status.in_(("queued", "failed")))
        )

    return {
        "publisher_initialized": _publisher is not None,
        "retry_worker_running": bool(_retry_worker_handle and not _retry_worker_handle.task.done()),
        "pending_outbox": int(pending or 0),
        "stream_name": _EVENT_STREAM_NAME,
    }


async def _retry_worker_loop(publisher: IdentityEventPublisher, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await publisher.dispatch_due_outbox(batch_size=100)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("identity event retry worker iteration failed: %s", exc)
        await asyncio.sleep(_EVENT_RETRY_POLL_SECONDS)
