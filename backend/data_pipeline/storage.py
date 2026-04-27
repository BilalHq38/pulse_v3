from __future__ import annotations

import json
from typing import Any

from core.utils import make_id
from data_pipeline.constants import RAW_EVENT_TABLE, RAW_LEAD_TABLE, RAW_MESSAGE_TABLE, RAW_TABLES
from data_pipeline.utils import safe_float, safe_int, stable_json_dumps

_PIPELINE_SCHEMA = "analytics_service"


def _row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    return dict(row)


def _raw_table_name(kind: str) -> str:
    normalized = str(kind or "").strip().lower()
    mapping = {
        "event": RAW_EVENT_TABLE,
        "events": RAW_EVENT_TABLE,
        RAW_EVENT_TABLE: RAW_EVENT_TABLE,
        "message": RAW_MESSAGE_TABLE,
        "messages": RAW_MESSAGE_TABLE,
        RAW_MESSAGE_TABLE: RAW_MESSAGE_TABLE,
        "lead": RAW_LEAD_TABLE,
        "leads": RAW_LEAD_TABLE,
        RAW_LEAD_TABLE: RAW_LEAD_TABLE,
    }
    table = mapping.get(normalized, "")
    if table not in RAW_TABLES:
        raise ValueError(f"Unsupported raw table kind: {kind}")
    return f"{_PIPELINE_SCHEMA}.{table}"


def _jsonb_safe(value: Any) -> dict[str, Any]:
    """
    Ensure JSONB-safe structures for asyncpg.
    asyncpg encodes JSON without a custom default serializer, so types like Decimal
    must be normalized before being passed as query args.
    """
    try:
        return json.loads(stable_json_dumps(value or {}))
    except Exception:
        try:
            return json.loads(json.dumps(value or {}, default=str))
        except Exception:
            return {"raw_value": str(value)}


async def fetch_raw_record(db, kind: str, raw_id: str) -> dict[str, Any]:
    table = _raw_table_name(kind)
    return _row_to_dict(await db.fetchrow(f"SELECT * FROM {table} WHERE id=$1 LIMIT 1", raw_id))


async def set_raw_record_status(
    db,
    kind: str,
    raw_id: str,
    *,
    status: str,
    error: str = "",
) -> None:
    table = _raw_table_name(kind)
    await db.execute(
        f"UPDATE {table} SET processing_status=$1,last_error=$2,processed_at="
        "CASE WHEN $1='processed' THEN NOW() WHEN $1='queued' THEN NULL ELSE processed_at END,"
        "updated_at=NOW() WHERE id=$3",
        status,
        str(error or "")[:2000],
        raw_id,
    )


async def increment_raw_record_retry(
    db,
    kind: str,
    raw_id: str,
    *,
    error: str = "",
) -> None:
    table = _raw_table_name(kind)
    await db.execute(
        f"UPDATE {table} SET processing_status='failed',retry_count=retry_count+1,last_error=$1,updated_at=NOW() "
        "WHERE id=$2",
        str(error or "")[:2000],
        raw_id,
    )


async def upsert_analytics_event(db, payload: dict[str, Any]) -> dict[str, Any]:
    event_id = str(payload.get("id") or make_id())
    row = await db.fetchrow(
        f"INSERT INTO {_PIPELINE_SCHEMA}.analytics_events("
        "id,company_id,raw_table,raw_id,event_kind,event_source,entity_type,entity_id,"
        "conversation_id,lead_id,customer_id,metric_date,occurred_at,payload,created_at,updated_at"
        ") VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,NOW(),NOW()) "
        "ON CONFLICT(company_id,raw_table,raw_id,event_kind) DO UPDATE SET "
        "event_source=EXCLUDED.event_source,entity_type=EXCLUDED.entity_type,entity_id=EXCLUDED.entity_id,"
        "conversation_id=EXCLUDED.conversation_id,lead_id=EXCLUDED.lead_id,customer_id=EXCLUDED.customer_id,"
        "metric_date=EXCLUDED.metric_date,occurred_at=EXCLUDED.occurred_at,payload=EXCLUDED.payload,updated_at=NOW() "
        "RETURNING *",
        event_id,
        payload.get("company_id", ""),
        payload.get("raw_table", ""),
        payload.get("raw_id", ""),
        payload.get("event_kind", ""),
        payload.get("event_source", ""),
        payload.get("entity_type", ""),
        payload.get("entity_id", ""),
        payload.get("conversation_id", ""),
        payload.get("lead_id", ""),
        payload.get("customer_id", ""),
        payload.get("metric_date"),
        payload.get("occurred_at"),
        _jsonb_safe(payload.get("payload") or {}),
    )
    return _row_to_dict(row)


async def upsert_lead_metrics(db, payload: dict[str, Any]) -> dict[str, Any]:
    metric_id = str(payload.get("id") or make_id())
    row = await db.fetchrow(
        f"INSERT INTO {_PIPELINE_SCHEMA}.lead_metrics("
        "id,company_id,lead_id,metric_date,source,status,phase,grade,name,email,phone,"
        "current_score,recommended_score,recommended_grade,scoring_reason,next_action,"
        "duplicate_count,is_duplicate,is_converted,payload,created_at,updated_at"
        ") VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,NOW(),NOW()) "
        "ON CONFLICT(company_id,lead_id,metric_date) DO UPDATE SET "
        "source=EXCLUDED.source,status=EXCLUDED.status,phase=EXCLUDED.phase,grade=EXCLUDED.grade,"
        "name=EXCLUDED.name,email=EXCLUDED.email,phone=EXCLUDED.phone,current_score=EXCLUDED.current_score,"
        "recommended_score=EXCLUDED.recommended_score,recommended_grade=EXCLUDED.recommended_grade,"
        "scoring_reason=EXCLUDED.scoring_reason,next_action=EXCLUDED.next_action,"
        "duplicate_count=EXCLUDED.duplicate_count,is_duplicate=EXCLUDED.is_duplicate,"
        "is_converted=EXCLUDED.is_converted,payload=EXCLUDED.payload,updated_at=NOW() "
        "RETURNING *",
        metric_id,
        payload.get("company_id", ""),
        payload.get("lead_id", ""),
        payload.get("metric_date"),
        payload.get("source", ""),
        payload.get("status", ""),
        payload.get("phase", ""),
        payload.get("grade", ""),
        payload.get("name", ""),
        payload.get("email", ""),
        payload.get("phone", ""),
        safe_int(payload.get("current_score")),
        safe_int(payload.get("recommended_score")),
        payload.get("recommended_grade", ""),
        payload.get("scoring_reason", ""),
        payload.get("next_action", ""),
        safe_int(payload.get("duplicate_count")),
        bool(payload.get("is_duplicate")),
        bool(payload.get("is_converted")),
        _jsonb_safe(payload.get("payload") or {}),
    )
    return _row_to_dict(row)


async def upsert_conversation_metrics(db, payload: dict[str, Any]) -> dict[str, Any]:
    metric_id = str(payload.get("id") or make_id())
    row = await db.fetchrow(
        f"INSERT INTO {_PIPELINE_SCHEMA}.conversation_metrics("
        "id,company_id,conversation_id,metric_date,channel,status,customer_id,ai_handled,escalated,"
        "total_messages,customer_messages,agent_messages,ai_messages,system_messages,unread_count,"
        "avg_sentiment,latest_sentiment_label,latest_sentiment_score,latest_intent_type,"
        "first_message_at,last_message_at,response_time_minutes,payload,created_at,updated_at"
        ") VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,NOW(),NOW()) "
        "ON CONFLICT(company_id,conversation_id,metric_date) DO UPDATE SET "
        "channel=EXCLUDED.channel,status=EXCLUDED.status,customer_id=EXCLUDED.customer_id,"
        "ai_handled=EXCLUDED.ai_handled,escalated=EXCLUDED.escalated,total_messages=EXCLUDED.total_messages,"
        "customer_messages=EXCLUDED.customer_messages,agent_messages=EXCLUDED.agent_messages,"
        "ai_messages=EXCLUDED.ai_messages,system_messages=EXCLUDED.system_messages,unread_count=EXCLUDED.unread_count,"
        "avg_sentiment=EXCLUDED.avg_sentiment,latest_sentiment_label=EXCLUDED.latest_sentiment_label,"
        "latest_sentiment_score=EXCLUDED.latest_sentiment_score,latest_intent_type=EXCLUDED.latest_intent_type,"
        "first_message_at=EXCLUDED.first_message_at,last_message_at=EXCLUDED.last_message_at,"
        "response_time_minutes=EXCLUDED.response_time_minutes,payload=EXCLUDED.payload,updated_at=NOW() "
        "RETURNING *",
        metric_id,
        payload.get("company_id", ""),
        payload.get("conversation_id", ""),
        payload.get("metric_date"),
        payload.get("channel", ""),
        payload.get("status", ""),
        payload.get("customer_id", ""),
        bool(payload.get("ai_handled")),
        bool(payload.get("escalated")),
        safe_int(payload.get("total_messages")),
        safe_int(payload.get("customer_messages")),
        safe_int(payload.get("agent_messages")),
        safe_int(payload.get("ai_messages")),
        safe_int(payload.get("system_messages")),
        safe_int(payload.get("unread_count")),
        float(safe_float(payload.get("avg_sentiment"), 0.0) or 0.0),
        payload.get("latest_sentiment_label", ""),
        safe_float(payload.get("latest_sentiment_score")),
        payload.get("latest_intent_type", ""),
        payload.get("first_message_at"),
        payload.get("last_message_at"),
        float(safe_float(payload.get("response_time_minutes"), 0.0) or 0.0),
        _jsonb_safe(payload.get("payload") or {}),
    )
    return _row_to_dict(row)


async def upsert_sentiment_log(db, payload: dict[str, Any]) -> dict[str, Any]:
    log_id = str(payload.get("id") or make_id())
    row = await db.fetchrow(
        f"INSERT INTO {_PIPELINE_SCHEMA}.sentiment_logs("
        "id,company_id,raw_table,raw_id,source,entity_type,entity_id,conversation_id,message_id,"
        "sentiment_label,sentiment_score,emotion,confidence,intent_type,analyzed_text,occurred_at,created_at"
        ") VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,NOW()) "
        "ON CONFLICT(company_id,raw_table,raw_id) DO UPDATE SET "
        "source=EXCLUDED.source,entity_type=EXCLUDED.entity_type,entity_id=EXCLUDED.entity_id,"
        "conversation_id=EXCLUDED.conversation_id,message_id=EXCLUDED.message_id,"
        "sentiment_label=EXCLUDED.sentiment_label,sentiment_score=EXCLUDED.sentiment_score,"
        "emotion=EXCLUDED.emotion,confidence=EXCLUDED.confidence,intent_type=EXCLUDED.intent_type,"
        "analyzed_text=EXCLUDED.analyzed_text,occurred_at=EXCLUDED.occurred_at "
        "RETURNING *",
        log_id,
        payload.get("company_id", ""),
        payload.get("raw_table", ""),
        payload.get("raw_id", ""),
        payload.get("source", ""),
        payload.get("entity_type", ""),
        payload.get("entity_id", ""),
        payload.get("conversation_id", ""),
        payload.get("message_id", ""),
        payload.get("sentiment_label", ""),
        safe_float(payload.get("sentiment_score")),
        payload.get("emotion", ""),
        safe_float(payload.get("confidence")),
        payload.get("intent_type", ""),
        payload.get("analyzed_text", ""),
        payload.get("occurred_at"),
    )
    return _row_to_dict(row)


async def upsert_customer_interaction_summary(
    db,
    payload: dict[str, Any],
) -> dict[str, Any]:
    existing = await db.fetchrow(
        "SELECT id FROM customer_interaction_summaries "
        "WHERE company_id=$1 AND customer_id=$2 AND conversation_id=$3 AND summary_date=$4 "
        "ORDER BY created_at DESC LIMIT 1",
        payload.get("company_id", ""),
        payload.get("customer_id", ""),
        payload.get("conversation_id", ""),
        payload.get("summary_date"),
    )
    existing_id = str((_row_to_dict(existing) or {}).get("id") or "")
    if existing_id:
        await db.execute(
            "UPDATE customer_interaction_summaries SET "
            "customer_name=$1,summary_text=$2,total_messages=$3,avg_sentiment=$4,"
            "escalated=$5,ai_handled=$6,created_at=NOW() WHERE id=$7",
            payload.get("customer_name", ""),
            payload.get("summary_text", ""),
            safe_int(payload.get("total_messages")),
            float(safe_float(payload.get("avg_sentiment"), 0.0) or 0.0),
            bool(payload.get("escalated")),
            bool(payload.get("ai_handled")),
            existing_id,
        )
        return _row_to_dict(await db.fetchrow("SELECT * FROM customer_interaction_summaries WHERE id=$1", existing_id))
    summary_id = str(payload.get("id") or make_id())
    row = await db.fetchrow(
        "INSERT INTO customer_interaction_summaries("
        "id,company_id,customer_id,customer_name,conversation_id,summary_date,summary_text,"
        "total_messages,avg_sentiment,escalated,ai_handled,created_at"
        ") VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,NOW()) RETURNING *",
        summary_id,
        payload.get("company_id", ""),
        payload.get("customer_id", ""),
        payload.get("customer_name", ""),
        payload.get("conversation_id", ""),
        payload.get("summary_date"),
        payload.get("summary_text", ""),
        safe_int(payload.get("total_messages")),
        float(safe_float(payload.get("avg_sentiment"), 0.0) or 0.0),
        bool(payload.get("escalated")),
        bool(payload.get("ai_handled")),
    )
    return _row_to_dict(row)


async def upsert_legacy_sentiment_record(db, payload: dict[str, Any]) -> None:
    existing = await db.fetchrow(
        "SELECT id FROM sentiment_analyses "
        "WHERE company_id=$1 AND entity_id=$2 AND entity_type=$3 AND analyzed_text=$4 "
        "ORDER BY created_at DESC LIMIT 1",
        payload.get("company_id", ""),
        payload.get("entity_id", ""),
        payload.get("entity_type", ""),
        payload.get("analyzed_text", ""),
    )
    existing_id = str((_row_to_dict(existing) or {}).get("id") or "")
    if existing_id:
        await db.execute(
            "UPDATE sentiment_analyses SET sentiment_score=$1,sentiment_label=$2,confidence_score=$3,analyzed_at=NOW() WHERE id=$4",  # noqa: E501
            float(safe_float(payload.get("sentiment_score"), 0.0) or 0.0),
            str(payload.get("sentiment_label") or "neutral").strip().lower() or "neutral",
            float(safe_float(payload.get("confidence"), 0.0) or 0.0),
            existing_id,
        )
        return
    await db.execute(
        "INSERT INTO sentiment_analyses("
        "id,company_id,agent_id,entity_id,entity_type,analyzed_text,sentiment_score,sentiment_label,"
        "confidence_score,emotion_joy,emotion_anger,emotion_sadness,emotion_fear,emotion_surprise,analyzed_at,created_at"
        ") VALUES($1,$2,'',$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,NOW(),NOW())",
        make_id(),
        payload.get("company_id", ""),
        payload.get("entity_id", ""),
        payload.get("entity_type", ""),
        payload.get("analyzed_text", ""),
        float(safe_float(payload.get("sentiment_score"), 0.0) or 0.0),
        payload.get("sentiment_label", "neutral"),
        float(safe_float(payload.get("confidence"), 0.0) or 0.0),
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    )
