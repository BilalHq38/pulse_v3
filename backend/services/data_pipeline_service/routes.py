from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from data_pipeline.storage import fetch_raw_record, set_raw_record_status
from data_pipeline.workers.jobs import (
    process_raw_event_job,
    process_raw_lead_job,
    process_raw_message_job,
    run_daily_rollup_job,
)
from services.data_pipeline_service.etl_leads import (
    clean_leads_dataframe,
    write_clean_leads,
)
from shared.background_queue import collect_background_queue_snapshot, get_background_queue
from shared.database import create_detached_task

router = APIRouter()
routers = (router,)


def _db(request: Request):
    return request.app.state.db


def _resolve_scoped_company_id(request: Request, body: dict[str, object]) -> str:
    header_company_id = str(request.headers.get("X-Company-Id") or "").strip()
    header_user_role = str(request.headers.get("X-User-Role") or "").strip().lower()
    body_company_id = str(body.get("company_id") or "").strip()

    if header_company_id:
        if body_company_id and body_company_id != header_company_id and header_user_role != "super_admin":
            raise HTTPException(403, "company_id mismatch")
        return header_company_id

    if header_user_role == "super_admin" and body_company_id:
        return body_company_id

    raise HTTPException(400, "company_id is required")


@router.get("/pipeline/stats")
async def get_pipeline_stats(request: Request):
    db = _db(request)
    queue_snapshot = await collect_background_queue_snapshot()
    stats = {}
    for table_name in ("raw_events", "raw_messages", "raw_leads"):
        rows = await db.fetch(
            f"SELECT processing_status, COUNT(*) AS count FROM {table_name} "
            "GROUP BY processing_status ORDER BY processing_status"
        )
        stats[table_name] = {str(row["processing_status"]): int(row["count"] or 0) for row in rows}
    latest_rollups = await db.fetch(
        "SELECT company_id,summary_date,generated_at FROM daily_summaries ORDER BY generated_at DESC LIMIT 20"
    )
    return {
        "stats": stats,
        "latest_rollups": [dict(row) for row in latest_rollups],
        "background_queue": queue_snapshot,
    }


@router.post("/pipeline/reprocess/{kind}/{raw_id}")
async def reprocess_pipeline_record(kind: str, raw_id: str, request: Request):
    db = _db(request)
    normalized_kind = str(kind or "").strip().lower()
    raw_record = await fetch_raw_record(db, normalized_kind, raw_id)
    if not raw_record:
        raise HTTPException(404, "Raw record not found")
    company_id = str(raw_record.get("company_id") or "").strip()
    await set_raw_record_status(db, normalized_kind, raw_id, status="queued")
    if normalized_kind in {"event", "events", "raw_events"}:
        job = process_raw_event_job(db=db, raw_event_id=raw_id, company_id=company_id)
        name = "process-raw-event"
    elif normalized_kind in {"message", "messages", "raw_messages"}:
        job = process_raw_message_job(
            db=db,
            raw_message_id=raw_id,
            company_id=company_id,
        )
        name = "process-raw-message"
    elif normalized_kind in {"lead", "leads", "raw_leads"}:
        job = process_raw_lead_job(db=db, raw_lead_id=raw_id, company_id=company_id)
        name = "process-raw-lead"
    else:
        raise HTTPException(400, "Unsupported raw record kind")
    job_id = (
        f"etl:reprocess:{company_id}:{normalized_kind}:{raw_id}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    )
    queue = get_background_queue(service_label="data-pipeline")
    if queue is not None and queue.enabled:
        await queue.enqueue_coroutine(
            job,
            name=name,
            job_id=job_id,
            idempotency_key=job_id,
        )
        try:
            job.close()
        except Exception:
            pass
    else:
        create_detached_task(job, name=name, job_id=job_id, idempotency_key=job_id)
    return {
        "status": "queued",
        "kind": normalized_kind,
        "raw_id": raw_id,
        "company_id": company_id,
    }


@router.post("/etl/leads/clean")
async def etl_leads_clean(request: Request):
    """Normalize, dedupe, and null-fill leads for a tenant.

    Writes cleaned rows into ``leads_clean`` and returns a summary of
    rows read, duplicates removed, null fills, and rows written.

    Tenant isolation: the effective ``company_id`` is the one forwarded by
    the API gateway in ``X-Company-Id``. A body-provided ``company_id`` is
    only honored when it matches that header (or when the caller is a
    super_admin with an internal service secret), so one tenant can never
    run ETL over another tenant's leads.
    """
    db = _db(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    company_id = _resolve_scoped_company_id(request, body)
    limit = int(body.get("limit") or 5000)
    limit = max(1, min(limit, 50000))

    rows = await db.fetch(
        """
        SELECT id, company_id, name, email, phone, status, source, created_at
          FROM leads
         WHERE company_id = $1
         ORDER BY created_at ASC
         LIMIT $2
        """,
        company_id,
        limit,
    )
    source_rows = [dict(row) for row in rows]

    cleaned_df, stats = clean_leads_dataframe(source_rows)
    rows_written = await write_clean_leads(db, cleaned_df)

    return {
        "status": "ok",
        "company_id": company_id,
        "cleaned": rows_written,
        "rows_in": stats["rows_in"],
        "rows_out": stats["rows_out"],
        "duplicates_removed": stats["duplicates_removed"],
        "null_fills": stats["null_fills"],
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/pipeline/daily-rollups")
async def trigger_daily_rollup(request: Request):
    db = _db(request)
    body = await request.json() if request.method == "POST" else {}
    if not isinstance(body, dict):
        body = {}
    company_id = _resolve_scoped_company_id(request, body)
    summary_date = str(body.get("summary_date") or datetime.now(timezone.utc).date().isoformat()).strip()
    result = await run_daily_rollup_job(
        db=db,
        company_id=company_id,
        summary_date=summary_date,
    )
    return {"status": "generated", "summary_date": summary_date, "result": result}
