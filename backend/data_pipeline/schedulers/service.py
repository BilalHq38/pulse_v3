from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from data_pipeline.constants import (
    DEFAULT_BATCH_INITIAL_DELAY_SECONDS,
    DEFAULT_BATCH_INTERVAL_SECONDS,
    PIPELINE_SERVICE_LABEL,
)
from shared.background_queue import get_background_queue
from shared.database import platform_admin_context

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PipelineSchedulerHandle:
    task: asyncio.Task
    stop_event: asyncio.Event


async def _enqueue_rollup(db, *, company_id: str, summary_date: str) -> None:
    # Local import to avoid circular imports:
    # - schedulers.service is imported by services/data_pipeline_service/main.py
    # - workers.jobs imports schedulers.daily_rollups
    # Importing workers.jobs at module import time can race with other imports.
    from data_pipeline.workers.jobs import run_daily_rollup_job

    queue = get_background_queue(service_label=PIPELINE_SERVICE_LABEL)
    job_id = f"etl:rollup:{company_id}:{summary_date}:{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
    coro = run_daily_rollup_job(
        db=db,
        company_id=company_id,
        summary_date=summary_date,
    )
    if queue is not None and queue.enabled:
        await queue.enqueue_coroutine(
            coro,
            name="daily-rollup",
            job_id=job_id,
            idempotency_key=job_id,
        )
        try:
            coro.close()
        except Exception:
            pass
        return
    await coro


async def _scheduler_loop(
    db,
    stop_event: asyncio.Event,
    *,
    interval_seconds: int,
    initial_delay_seconds: int,
) -> None:
    if initial_delay_seconds > 0:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=initial_delay_seconds)
            return
        except asyncio.TimeoutError:
            pass
    while not stop_event.is_set():
        async with platform_admin_context(db):
            try:
                rows = await db.fetch(
                    "SELECT id FROM companies WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT 1000"
                )
            except Exception:
                rows = await db.fetch("SELECT id FROM companies ORDER BY created_at DESC LIMIT 1000")
        today = datetime.now(timezone.utc).date()
        for row in rows:
            company_id = str((dict(row) if row else {}).get("id") or "").strip()
            if not company_id:
                continue
            for target_date in (
                today.isoformat(),
                (today - timedelta(days=1)).isoformat(),
            ):
                try:
                    await _enqueue_rollup(
                        db,
                        company_id=company_id,
                        summary_date=target_date,
                    )
                except Exception as exc:
                    logger.warning(
                        "pipeline scheduler enqueue failed company_id=%s date=%s error=%s",
                        company_id,
                        target_date,
                        exc,
                    )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(30, interval_seconds))
        except asyncio.TimeoutError:
            continue


async def start_pipeline_scheduler(
    db,
    *,
    interval_seconds: int = DEFAULT_BATCH_INTERVAL_SECONDS,
    initial_delay_seconds: int = DEFAULT_BATCH_INITIAL_DELAY_SECONDS,
) -> PipelineSchedulerHandle:
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        _scheduler_loop(
            db,
            stop_event,
            interval_seconds=interval_seconds,
            initial_delay_seconds=initial_delay_seconds,
        ),
        name="data-pipeline-scheduler",
    )
    return PipelineSchedulerHandle(task=task, stop_event=stop_event)


async def stop_pipeline_scheduler(handle: PipelineSchedulerHandle | None) -> None:
    if handle is None:
        return
    handle.stop_event.set()
    handle.task.cancel()
    try:
        await handle.task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("pipeline scheduler shutdown failed")
