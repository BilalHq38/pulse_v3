"""Standalone FastAPI entrypoint for the follow-up scheduler ECS service.

Run with:
    uvicorn services.followup_scheduler.entrypoint:app --host 0.0.0.0 --port 8080 --workers 1

IMPORTANT: deploy with exactly 1 ECS task. The scheduler uses
FOR UPDATE SKIP LOCKED for safe concurrency, but running 2+ instances
will schedule duplicate follow-up messages.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from services.followup_scheduler.loop import start_followup_scheduler, stop_followup_scheduler
from shared.database import get_db_pool

logger = logging.getLogger(__name__)

_scheduler_handle = None
_db_pool = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler_handle, _db_pool
    logger.info("followup_scheduler_starting")
    try:
        _db_pool = await get_db_pool()
        _scheduler_handle = await start_followup_scheduler(_db_pool)
        logger.info("followup_scheduler_started poll_interval_seconds=%s", os.environ.get("AI_FOLLOWUP_POLL_INTERVAL_SECONDS", "60"))
    except Exception:
        logger.exception("followup_scheduler_startup_failed")
        raise
    yield
    logger.info("followup_scheduler_stopping")
    await stop_followup_scheduler(_scheduler_handle)
    if _db_pool and hasattr(_db_pool, "close"):
        await _db_pool.close()
    logger.info("followup_scheduler_stopped")


app = FastAPI(title="Pulse Follow-up Scheduler", lifespan=lifespan)


@app.get("/health")
async def health():
    handle = _scheduler_handle
    if handle is None or handle.task.done():
        return JSONResponse({"status": "degraded", "scheduler": "not_running"}, status_code=503)
    exc = handle.task.exception() if handle.task.done() else None
    if exc:
        return JSONResponse({"status": "degraded", "error": str(exc)}, status_code=503)
    return {"status": "ok", "scheduler": "running"}


@app.get("/metrics")
async def metrics():
    handle = _scheduler_handle
    return {
        "scheduler_running": handle is not None and not (handle.task.done() if handle else True),
        "stop_event_set": handle.stop_event.is_set() if handle else None,
    }
