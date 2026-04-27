from __future__ import annotations

import logging
import os

from data_pipeline.bootstrap import ensure_pipeline_tables
from data_pipeline.processors import EventProcessor, LeadProcessor, MessageProcessor
from data_pipeline.schedulers.daily_rollups import generate_daily_rollup
from data_pipeline.storage import (
    fetch_raw_record,
    increment_raw_record_retry,
    set_raw_record_status,
)
from shared.database import company_context
from shared.metrics import increment_counter

logger = logging.getLogger(__name__)

# How many minutes a record can stay "processing" before being considered stale
_STALE_PROCESSING_MINUTES = int(os.environ.get("ETL_STALE_PROCESSING_MINUTES", "10") or 10)


async def _process_raw_job(
    db,
    *,
    kind: str,
    raw_id: str,
    company_id: str,
    processor,
) -> dict:
    scoped_company_id = str(company_id or "").strip()
    if not scoped_company_id or not str(raw_id or "").strip():
        return {}
    await ensure_pipeline_tables(db)
    async with company_context(db, scoped_company_id):
        raw_record = await fetch_raw_record(db, kind, raw_id)
        if not raw_record:
            return {}
        status = str(raw_record.get("processing_status") or "").strip().lower()
        if status == "processed":
            return raw_record
        # Recover stale "processing" records that never finished
        if status == "processing":
            stale = await db.fetchval(
                f"SELECT id FROM {kind}s WHERE id=$1 "
                f"AND updated_at < NOW() - INTERVAL '{_STALE_PROCESSING_MINUTES} minutes' LIMIT 1",
                raw_id,
            )
            if not stale:
                logger.warning(
                    "Skipping %s %s — still processing (within stale window)",
                    kind,
                    raw_id,
                )
                return raw_record
            logger.warning(
                "Recovering stale %s %s — resetting to queued",
                kind,
                raw_id,
            )
        await set_raw_record_status(db, kind, raw_id, status="processing")
        try:
            result = await processor.process(raw_record)
        except Exception as exc:
            await increment_raw_record_retry(db, kind, raw_id, error=str(exc))
            increment_counter(
                "data_pipeline.job.failed",
                labels={"kind": kind, "company_id": scoped_company_id},
            )
            logger.exception(
                "raw %s processing failed company_id=%s raw_id=%s",
                kind,
                scoped_company_id,
                raw_id,
            )
            raise
        await set_raw_record_status(db, kind, raw_id, status="processed")
        increment_counter(
            "data_pipeline.job.processed",
            labels={"kind": kind, "company_id": scoped_company_id},
        )
        return result


async def process_raw_event_job(
    db,
    *,
    raw_event_id: str,
    company_id: str,
) -> dict:
    return await _process_raw_job(
        db,
        kind="event",
        raw_id=raw_event_id,
        company_id=company_id,
        processor=EventProcessor(db),
    )


async def process_raw_message_job(
    db,
    *,
    raw_message_id: str,
    company_id: str,
) -> dict:
    return await _process_raw_job(
        db,
        kind="message",
        raw_id=raw_message_id,
        company_id=company_id,
        processor=MessageProcessor(db),
    )


async def process_raw_lead_job(
    db,
    *,
    raw_lead_id: str,
    company_id: str,
) -> dict:
    return await _process_raw_job(
        db,
        kind="lead",
        raw_id=raw_lead_id,
        company_id=company_id,
        processor=LeadProcessor(db),
    )


async def run_daily_rollup_job(
    db,
    *,
    company_id: str,
    summary_date: str,
) -> dict:
    scoped_company_id = str(company_id or "").strip()
    if not scoped_company_id:
        return {}
    await ensure_pipeline_tables(db)
    async with company_context(db, scoped_company_id):
        return await generate_daily_rollup(
            db,
            company_id=scoped_company_id,
            summary_date=summary_date,
        )
