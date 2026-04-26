from __future__ import annotations

import json
from typing import Any

from core.utils import make_id
from data_pipeline.bootstrap import ensure_pipeline_tables
from data_pipeline.constants import PIPELINE_SERVICE_LABEL
from data_pipeline.utils import (
    dedupe_key,
    metric_date_for,
    normalize_email,
    normalize_phone,
    parse_timestamp,
    stable_json_dumps,
    stable_json_hash,
)
from shared.database import company_context
from shared.webhook_task_runner import create_safe_detached_task


def _row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    return dict(row)


def _normalize_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        # Ensure JSONB-safe payloads (asyncpg can't encode datetimes by default).
        try:
            return json.loads(stable_json_dumps(payload))
        except Exception:
            return json.loads(json.dumps(payload, default=str))
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {"raw_text": text}
        return parsed if isinstance(parsed, dict) else {"raw_payload": parsed}
    return {"value": payload}


async def _enqueue_pipeline_job(
    db,
    coro,
    *,
    name: str,
    job_id: str,
    company_id: str,
    channel: str,
    event_id: str = "",
    payload: Any | None = None,
) -> None:
    create_safe_detached_task(
        db,
        coro,
        name=name,
        job_id=job_id,
        idempotency_key=job_id,
        company_id=str(company_id or "").strip(),
        channel=str(channel or "pipeline"),
        event_id=str(event_id or "").strip(),
        payload=payload or {},
        source_queue=PIPELINE_SERVICE_LABEL,
        service_label=PIPELINE_SERVICE_LABEL,
    )


async def capture_raw_event(
    db,
    *,
    company_id: str,
    source: str,
    payload: Any,
    event_type: str = "",
    event_id: str = "",
    external_id: str = "",
    occurred_at: Any = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from data_pipeline.workers.jobs import process_raw_event_job

    scoped_company_id = str(company_id or "").strip()
    if not scoped_company_id:
        return {}
    normalized_payload = _normalize_payload(payload)
    normalized_metadata = dict(metadata or {})
    normalized_source = str(source or "event").strip().lower() or "event"
    normalized_type = str(event_type or normalized_metadata.get("event_type") or normalized_source).strip().lower()
    occurred_dt = parse_timestamp(occurred_at or normalized_metadata.get("occurred_at"))
    resolved_event_id = str(event_id or normalized_metadata.get("event_id") or external_id).strip() or dedupe_key(
        scoped_company_id,
        normalized_source,
        normalized_type,
        stable_json_hash(normalized_payload),
    )
    async with company_context(db, scoped_company_id):
        # Pipeline tables live in analytics_service schema; callers may be using
        # another schema (e.g. lead_service/customer_service). Force search_path
        # for this connection so raw_* inserts land where the pipeline worker reads.
        await db.execute('SET search_path TO "analytics_service", public')
        await ensure_pipeline_tables(db)
        row = await db.fetchrow(
            "INSERT INTO raw_events("
            "id,company_id,source,event_type,event_id,external_id,payload,metadata,payload_hash,"
            "processing_status,retry_count,last_error,occurred_at,created_at,updated_at"
            ") VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,'queued',0,'',$10,NOW(),NOW()) "
            "ON CONFLICT(company_id,source,event_id) DO UPDATE SET "
            "event_type=EXCLUDED.event_type,external_id=EXCLUDED.external_id,payload=EXCLUDED.payload,"
            "metadata=EXCLUDED.metadata,payload_hash=EXCLUDED.payload_hash,"
            "processing_status=CASE WHEN raw_events.processing_status='processed' THEN raw_events.processing_status ELSE 'queued' END,"  # noqa: E501
            "last_error='',updated_at=NOW() "
            "RETURNING *",
            make_id(),
            scoped_company_id,
            normalized_source,
            normalized_type,
            resolved_event_id,
            str(external_id or "").strip(),
            normalized_payload,
            normalized_metadata,
            stable_json_hash(normalized_payload),
            occurred_dt,
        )
    record = _row_to_dict(row)
    if record:
        await _enqueue_pipeline_job(
            db,
            process_raw_event_job(
                db=db,
                raw_event_id=record["id"],
                company_id=scoped_company_id,
            ),
            name="process-raw-event",
            job_id=f"etl:event:{scoped_company_id}:{record['id']}",
            company_id=scoped_company_id,
            channel=normalized_source,
            event_id=resolved_event_id,
            payload={"raw_event_id": record["id"], "event_type": normalized_type},
        )
    return record


async def capture_raw_message(
    db,
    *,
    conversation: dict[str, Any],
    message: dict[str, Any],
    source: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from data_pipeline.workers.jobs import process_raw_message_job

    scoped_company_id = str(conversation.get("company_id") or message.get("company_id") or "").strip()
    if not scoped_company_id:
        return {}
    payload = _normalize_payload(dict(message or {}))
    message_id = str(payload.get("id") or "").strip()
    metadata_payload = _normalize_payload(
        {
            "channel": conversation.get("channel", "web_chat"),
            "conversation_id": conversation.get("id", message.get("conversation_id", "")),
            "customer_id": conversation.get("customer_id", ""),
            "customer_name": conversation.get("customer_name", ""),
            **dict(metadata or {}),
        }
    )
    normalized_source = (
        str(source or metadata_payload.get("channel") or conversation.get("channel") or "message").strip().lower()
        or "message"
    )
    occurred_dt = parse_timestamp(payload.get("created_at") or metadata_payload.get("occurred_at"))
    resolved_dedupe_key = message_id or dedupe_key(
        scoped_company_id,
        metadata_payload.get("conversation_id", ""),
        payload.get("sender_type", ""),
        payload.get("content", ""),
        occurred_dt.isoformat(),
    )
    resolved_event_id = str(payload.get("event_id") or "").strip() or resolved_dedupe_key
    async with company_context(db, scoped_company_id):
        await db.execute('SET search_path TO "analytics_service", public')
        await ensure_pipeline_tables(db)
        row = await db.fetchrow(
            "INSERT INTO raw_messages("
            "id,company_id,source,event_id,dedupe_key,canonical_message_id,external_message_id,conversation_id,"
            "sender_type,payload,metadata,payload_hash,processing_status,retry_count,last_error,"
            "occurred_at,created_at,updated_at"
            ") VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,'queued',0,'',$13,NOW(),NOW()) "
            "ON CONFLICT(company_id,dedupe_key) DO UPDATE SET "
            "source=EXCLUDED.source,canonical_message_id=EXCLUDED.canonical_message_id,"
            "external_message_id=EXCLUDED.external_message_id,conversation_id=EXCLUDED.conversation_id,"
            "sender_type=EXCLUDED.sender_type,payload=EXCLUDED.payload,metadata=EXCLUDED.metadata,"
            "payload_hash=EXCLUDED.payload_hash,"
            "processing_status=CASE WHEN raw_messages.processing_status='processed' THEN raw_messages.processing_status ELSE 'queued' END,"  # noqa: E501
            "last_error='',updated_at=NOW() "
            "RETURNING *",
            make_id(),
            scoped_company_id,
            normalized_source,
            resolved_event_id,
            resolved_dedupe_key,
            message_id,
            str(payload.get("external_message_id") or payload.get("external_id") or "").strip(),
            metadata_payload.get("conversation_id", ""),
            str(payload.get("sender_type") or "").strip().lower(),
            payload,
            metadata_payload,
            stable_json_hash(payload),
            occurred_dt,
        )
    record = _row_to_dict(row)
    if record:
        await _enqueue_pipeline_job(
            db,
            process_raw_message_job(
                db=db,
                raw_message_id=record["id"],
                company_id=scoped_company_id,
            ),
            name="process-raw-message",
            job_id=f"etl:message:{scoped_company_id}:{record['id']}",
            company_id=scoped_company_id,
            channel=str(source or "message"),
            event_id=message_id or record["id"],
            payload={"raw_message_id": record["id"], "source": str(source or "message")},
        )
    return record


async def capture_raw_lead(
    db,
    *,
    company_id: str,
    lead: dict[str, Any],
    source: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from data_pipeline.workers.jobs import process_raw_lead_job

    scoped_company_id = str(company_id or lead.get("company_id") or "").strip()
    if not scoped_company_id:
        return {}
    payload = _normalize_payload(dict(lead or {}))
    lead_id = str(payload.get("id") or "").strip()
    email = normalize_email(payload.get("email"))
    phone = normalize_phone(payload.get("phone"))
    metadata_payload = _normalize_payload(
        {
            "action": str((metadata or {}).get("action") or "lead_snapshot").strip().lower(),
            **dict(metadata or {}),
        }
    )
    occurred_dt = parse_timestamp(
        payload.get("updated_at") or payload.get("created_at") or metadata_payload.get("occurred_at")
    )
    normalized_source = str(source or payload.get("source") or "lead").strip().lower() or "lead"
    resolved_dedupe_key = dedupe_key(
        scoped_company_id,
        lead_id or email or phone or "lead",
        normalized_source,
        metadata_payload.get("action", ""),
        occurred_dt.isoformat(),
        stable_json_hash(payload),
        email,
        phone,
        metric_date_for(occurred_dt).isoformat(),
    )
    resolved_event_id = (
        str(payload.get("event_id") or metadata_payload.get("event_id") or "").strip() or resolved_dedupe_key
    )
    async with company_context(db, scoped_company_id):
        await db.execute('SET search_path TO "analytics_service", public')
        await ensure_pipeline_tables(db)
        row = await db.fetchrow(
            "INSERT INTO raw_leads("
            "id,company_id,source,event_id,dedupe_key,canonical_lead_id,email,phone,payload,metadata,payload_hash,"
            "processing_status,retry_count,last_error,occurred_at,created_at,updated_at"
            ") VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'queued',0,'',$12,NOW(),NOW()) "
            "ON CONFLICT(company_id,dedupe_key) DO UPDATE SET "
            "source=EXCLUDED.source,canonical_lead_id=EXCLUDED.canonical_lead_id,email=EXCLUDED.email,"
            "phone=EXCLUDED.phone,payload=EXCLUDED.payload,metadata=EXCLUDED.metadata,payload_hash=EXCLUDED.payload_hash,"
            "processing_status=CASE WHEN raw_leads.processing_status='processed' THEN raw_leads.processing_status ELSE 'queued' END,"  # noqa: E501
            "last_error='',updated_at=NOW() "
            "RETURNING *",
            make_id(),
            scoped_company_id,
            normalized_source,
            resolved_event_id,
            resolved_dedupe_key,
            lead_id,
            email,
            phone,
            payload,
            metadata_payload,
            stable_json_hash(payload),
            occurred_dt,
        )
    record = _row_to_dict(row)
    if record:
        await _enqueue_pipeline_job(
            db,
            process_raw_lead_job(
                db=db,
                raw_lead_id=record["id"],
                company_id=scoped_company_id,
            ),
            name="process-raw-lead",
            job_id=f"etl:lead:{scoped_company_id}:{record['id']}",
            company_id=scoped_company_id,
            channel=str(source or "lead"),
            event_id=lead_id or record["id"],
            payload={"raw_lead_id": record["id"], "source": str(source or "lead")},
        )
    return record
