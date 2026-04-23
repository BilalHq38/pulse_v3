import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from postgres.bootstrap import create_postgres_schema  # noqa: E402
from postgres.models import (  # noqa: E402
    AIAgent,
    AIEmbedding,
    AISession,
    AnalyticsReport,
    APIEndpoint,
    ClientRequest,
    CompanySetting,
    ContextMemory,
    Feedback,
    JourneyTracking,
    LeadScore,
    LeadTag,
    LLMEngine,
    MCPClient,
    MCPServer,
    Metric,
    SentimentAnalysis,
    SocialAccount,
    SocialPost,
    SystemLog,
    TrainingData,
    WebhookEvent,
    WebhookHandler,
)
from postgres.session import get_engine, session_factory  # noqa: E402


def parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return default


def as_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return [value]


def as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def deterministic_id(prefix: str, *parts: Any) -> str:
    normalized = [as_text(part).strip() for part in parts if as_text(part).strip()]
    if not normalized:
        return ""
    digest = uuid.uuid5(uuid.NAMESPACE_URL, "|".join(normalized)).hex
    return f"{prefix}_{digest}"


def with_fallback_id(value: Any, prefix: str, *parts: Any) -> str:
    existing = as_text(value).strip()
    if existing:
        return existing
    return deterministic_id(prefix, *parts)


def first_dt(doc: dict[str, Any], keys: list[str]) -> datetime | None:
    for key in keys:
        parsed = parse_dt(doc.get(key))
        if parsed is not None:
            return parsed
    return None


def base_timestamps(doc: dict[str, Any]) -> tuple[datetime, datetime]:
    created = first_dt(
        doc,
        [
            "created_at",
            "generated_at",
            "registered_at",
            "requested_at",
            "recorded_at",
            "started_at",
            "received_at",
            "submitted_at",
        ],
    ) or datetime.now(timezone.utc)
    updated = first_dt(doc, ["updated_at", "last_updated", "responded_at", "processed_at", "completed_at"]) or created
    return created, updated


def map_company_setting(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    company_id = as_text(doc.get("company_id")).strip() or as_text(doc.get("id")).strip()
    company_name = as_text(doc.get("company_name")).strip()
    return {
        "id": with_fallback_id(doc.get("id"), "company_setting", company_id, company_name),
        "company_id": company_id,
        "company_name": company_name,
        "industry": as_text(doc.get("industry")).strip(),
        "language": as_text(doc.get("language")).strip(),
        "timezone": as_text(doc.get("timezone"), "UTC").strip() or "UTC",
        "ai_enabled": as_bool(doc.get("ai_enabled"), True),
        "ai_confidence_threshold": as_float(doc.get("ai_confidence_threshold"), 0.7),
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_lead_score(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    lead_id = as_text(doc.get("lead_id")).strip()
    return {
        "id": with_fallback_id(doc.get("id"), "lead_score", lead_id, created.isoformat()),
        "lead_id": lead_id,
        "score": as_int(doc.get("score"), 0),
        "grade": as_text(doc.get("grade")).strip(),
        "reasoning": as_text(doc.get("reasoning")),
        "next_action": as_text(doc.get("next_action")),
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_lead_tag(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    lead_id = as_text(doc.get("lead_id")).strip()
    tag_name = as_text(doc.get("tag_name") or doc.get("tag")).strip()
    return {
        "id": with_fallback_id(doc.get("id"), "lead_tag", lead_id, tag_name, created.isoformat()),
        "lead_id": lead_id,
        "tag_name": tag_name,
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_llm_engine(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    provider = as_text(doc.get("provider")).strip()
    model_name = as_text(doc.get("model_name")).strip()
    return {
        "id": with_fallback_id(doc.get("id"), "llm_engine", provider, model_name, as_text(doc.get("version")).strip()),
        "provider": provider,
        "model_name": model_name,
        "api_endpoint": as_text(doc.get("api_endpoint")),
        "temperature": as_float(doc.get("temperature"), 0.7),
        "max_tokens": as_int(doc.get("max_tokens"), 2048),
        "version": as_text(doc.get("version"), "current").strip() or "current",
        "last_updated": first_dt(doc, ["last_updated", "updated_at"]),
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_ai_agent(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    company_id = as_text(doc.get("company_id")).strip()
    llm_id = as_text(doc.get("llm_id")).strip()
    registered_at = first_dt(doc, ["registered_at", "created_at"])
    return {
        "id": with_fallback_id(
            doc.get("id"),
            "ai_agent",
            company_id,
            llm_id,
            as_text(doc.get("agent_type")).strip(),
            registered_at.isoformat() if registered_at else "",
        ),
        "company_id": company_id,
        "llm_id": llm_id,
        "mcp_server_id": as_text(doc.get("mcp_server_id")).strip(),
        "agent_type": as_text(doc.get("agent_type")).strip(),
        "provider": as_text(doc.get("provider")).strip(),
        "is_active": as_bool(doc.get("is_active"), True),
        "registered_at": registered_at,
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_ai_session(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    company_id = as_text(doc.get("company_id")).strip()
    convo_id = as_text(doc.get("convo_id")).strip()
    agent_id = as_text(doc.get("agent_id")).strip()
    return {
        "id": with_fallback_id(doc.get("id"), "ai_session", company_id, convo_id, agent_id, created.isoformat()),
        "company_id": company_id,
        "convo_id": convo_id,
        "agent_id": agent_id,
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_training_data(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    company_id = as_text(doc.get("company_id")).strip()
    data_category = as_text(doc.get("data_category")).strip()
    input_text = as_text(doc.get("input_text"))
    return {
        "id": with_fallback_id(
            doc.get("id"), "training_data", company_id, data_category, created.isoformat(), input_text[:80]
        ),
        "company_id": company_id,
        "data_category": data_category,
        "input_text": input_text,
        "output_text": as_text(doc.get("output_text")),
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_context_memory(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    company_id = as_text(doc.get("company_id")).strip()
    entity_id = as_text(doc.get("entity_id")).strip()
    memory_type = as_text(doc.get("memory_type")).strip()
    return {
        "id": with_fallback_id(
            doc.get("id"), "context_memory", company_id, entity_id, memory_type, created.isoformat()
        ),
        "company_id": company_id,
        "convo_id": as_text(doc.get("convo_id")).strip(),
        "entity_id": entity_id,
        "entity_type": as_text(doc.get("entity_type")).strip(),
        "memory_type": memory_type,
        "relevance_score": as_float(doc.get("relevance_score"), 0.0),
        "memory_content": as_text(doc.get("memory_content")),
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_ai_embedding(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    memory_id = as_text(doc.get("memory_id")).strip()
    model = as_text(doc.get("embedding_model")).strip()
    return {
        "id": with_fallback_id(
            doc.get("id"), "ai_embedding", memory_id, model, as_int(doc.get("dimension"), 0), created.isoformat()
        ),
        "company_id": as_text(doc.get("company_id")).strip(),
        "memory_id": memory_id,
        "embedding_model": model,
        "dimension": as_int(doc.get("dimension"), 0),
        "embedding_vector": as_list(doc.get("embedding_vector")),
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_sentiment_analysis(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    analyzed_at = first_dt(doc, ["analyzed_at", "created_at"])
    company_id = as_text(doc.get("company_id")).strip()
    entity_id = as_text(doc.get("entity_id")).strip()
    entity_type = as_text(doc.get("entity_type")).strip()
    return {
        "id": with_fallback_id(
            doc.get("id"),
            "sentiment_analysis",
            company_id,
            entity_id,
            entity_type,
            analyzed_at.isoformat() if analyzed_at else "",
        ),
        "company_id": company_id,
        "entity_id": entity_id,
        "entity_type": entity_type,
        "sentiment_label": as_text(doc.get("sentiment_label")).strip(),
        "sentiment_score": as_float(doc.get("sentiment_score"), as_float(doc.get("sentiment"), 0.0)),
        "analyzed_at": analyzed_at,
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_mcp_server(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    endpoint = as_text(doc.get("endpoint")).strip()
    return {
        "id": with_fallback_id(doc.get("id"), "mcp_server", endpoint),
        "endpoint": endpoint,
        "status": as_text(doc.get("status"), "active").strip() or "active",
        "region": as_text(doc.get("region")).strip(),
        "capabilities": as_dict(doc.get("capabilities")),
        "last_heartbeat": first_dt(doc, ["last_heartbeat", "updated_at"]),
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_mcp_client(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    company_id = as_text(doc.get("company_id")).strip()
    server_id = as_text(doc.get("server_id")).strip()
    client_name = as_text(doc.get("client_name")).strip()
    user_id = as_text(doc.get("user_id")).strip()
    return {
        "id": with_fallback_id(doc.get("id"), "mcp_client", company_id, server_id, client_name, user_id),
        "company_id": company_id,
        "server_id": server_id,
        "user_id": user_id,
        "client_type": as_text(doc.get("client_type")).strip(),
        "client_name": client_name,
        "platform": as_text(doc.get("platform")).strip(),
        "version": as_text(doc.get("version")).strip(),
        "configuration": as_dict(doc.get("configuration")),
        "last_connected": first_dt(doc, ["last_connected", "updated_at"]),
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_api_endpoint(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    server_id = as_text(doc.get("server_id")).strip()
    endpoint_path = as_text(doc.get("endpoint_path")).strip()
    method = as_text(doc.get("http_method"), "POST").strip() or "POST"
    return {
        "id": with_fallback_id(doc.get("id"), "api_endpoint", server_id, endpoint_path, method),
        "server_id": server_id,
        "endpoint_path": endpoint_path,
        "http_method": method,
        "endpoint_type": as_text(doc.get("endpoint_type")).strip(),
        "request_schema": as_dict(doc.get("request_schema")),
        "response_schema": as_dict(doc.get("response_schema")),
        "is_active": as_bool(doc.get("is_active"), True),
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_client_request(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    client_id = as_text(doc.get("client_id")).strip()
    endpoint_id = as_text(doc.get("endpoint_id")).strip()
    requested_at = first_dt(doc, ["requested_at", "created_at"])
    return {
        "id": with_fallback_id(
            doc.get("id"),
            "client_request",
            client_id,
            endpoint_id,
            requested_at.isoformat() if requested_at else "",
            as_text(doc.get("convo_id")).strip(),
            as_text(doc.get("message_id")).strip(),
        ),
        "company_id": as_text(doc.get("company_id")).strip(),
        "client_id": client_id,
        "endpoint_id": endpoint_id,
        "convo_id": as_text(doc.get("convo_id")).strip(),
        "message_id": as_text(doc.get("message_id")).strip(),
        "response_status": as_int(doc.get("response_status"), 0),
        "requested_at": requested_at,
        "responded_at": first_dt(doc, ["responded_at", "updated_at"]),
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_webhook_handler(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    client_id = as_text(doc.get("client_id")).strip()
    platform = as_text(doc.get("platform")).strip()
    webhook_url = as_text(doc.get("webhook_url")).strip()
    return {
        "id": with_fallback_id(doc.get("id"), "webhook_handler", client_id, platform, webhook_url),
        "company_id": as_text(doc.get("company_id")).strip(),
        "client_id": client_id,
        "platform": platform,
        "webhook_url": webhook_url,
        "verification_token_ref": as_text(doc.get("verification_token_ref")),
        "handler_config": as_dict(doc.get("handler_config")),
        "is_active": as_bool(doc.get("is_active"), True),
        "last_triggered": first_dt(doc, ["last_triggered", "updated_at"]),
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_webhook_event(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    handler_id = as_text(doc.get("handler_id")).strip()
    event_type = as_text(doc.get("event_type")).strip()
    received_at = first_dt(doc, ["received_at", "created_at"])
    return {
        "id": with_fallback_id(
            doc.get("id"),
            "webhook_event",
            handler_id,
            event_type,
            received_at.isoformat() if received_at else "",
            as_text(doc.get("status")).strip(),
        ),
        "company_id": as_text(doc.get("company_id")).strip(),
        "handler_id": handler_id,
        "event_type": event_type,
        "status": as_text(doc.get("status")).strip(),
        "payload": as_dict(doc.get("payload")),
        "error_message": as_text(doc.get("error_message")),
        "received_at": received_at,
        "processed_at": first_dt(doc, ["processed_at", "updated_at"]),
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_analytics_report(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    company_id = as_text(doc.get("company_id")).strip()
    report_type = as_text(doc.get("report_type")).strip()
    period = as_text(doc.get("period")).strip()
    generated_at = first_dt(doc, ["generated_at", "created_at"])
    return {
        "id": with_fallback_id(
            doc.get("id"),
            "analytics_report",
            company_id,
            report_type,
            period,
            generated_at.isoformat() if generated_at else "",
        ),
        "company_id": company_id,
        "report_type": report_type,
        "period": period,
        "report_data": as_dict(doc.get("report_data")),
        "generated_at": generated_at,
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_metric(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    report_id = as_text(doc.get("report_id")).strip()
    metric_name = as_text(doc.get("metric_name")).strip()
    recorded_at = first_dt(doc, ["recorded_at", "created_at"])
    return {
        "id": with_fallback_id(
            doc.get("id"), "metric", report_id, metric_name, recorded_at.isoformat() if recorded_at else ""
        ),
        "report_id": report_id,
        "company_id": as_text(doc.get("company_id")).strip(),
        "metric_name": metric_name,
        "metric_value": as_float(doc.get("metric_value"), 0.0),
        "unit": as_text(doc.get("unit")).strip(),
        "recorded_at": recorded_at,
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_social_account(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    company_id = as_text(doc.get("company_id")).strip()
    platform = as_text(doc.get("platform")).strip()
    handle = as_text(doc.get("account_handle")).strip()
    page_id = as_text(doc.get("page_id")).strip()
    return {
        "id": with_fallback_id(doc.get("id"), "social_account", company_id, platform, handle, page_id),
        "company_id": company_id,
        "platform": platform,
        "account_handle": handle,
        "access_token_ref": as_text(doc.get("access_token_ref")),
        "page_id": page_id,
        "app_id": as_text(doc.get("app_id")).strip(),
        "phone_number_id": as_text(doc.get("phone_number_id")).strip(),
        "is_active": as_bool(doc.get("is_active"), True),
        "last_sync": first_dt(doc, ["last_sync", "updated_at"]),
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_social_post(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    company_id = as_text(doc.get("company_id")).strip()
    account_id = as_text(doc.get("account_id")).strip()
    platform = as_text(doc.get("platform")).strip()
    posted_at = first_dt(doc, ["posted_at", "created_at"])
    return {
        "id": with_fallback_id(
            doc.get("id"),
            "social_post",
            company_id,
            account_id,
            platform,
            posted_at.isoformat() if posted_at else "",
            as_text(doc.get("content"))[:80],
        ),
        "company_id": company_id,
        "account_id": account_id,
        "platform": platform,
        "post_type": as_text(doc.get("post_type")).strip(),
        "content": as_text(doc.get("content")),
        "post_url": as_text(doc.get("post_url")),
        "engagement_count": as_int(doc.get("engagement_count"), 0),
        "comments_count": as_int(doc.get("comments_count"), 0),
        "sentiment": as_float(doc.get("sentiment"), 0.0),
        "posted_at": posted_at,
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_journey_tracking(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    company_id = as_text(doc.get("company_id")).strip()
    user_id = as_text(doc.get("user_id")).strip()
    phase = as_text(doc.get("phase")).strip()
    started_at = first_dt(doc, ["started_at", "created_at"])
    return {
        "id": with_fallback_id(
            doc.get("id"),
            "journey_tracking",
            company_id,
            user_id,
            phase,
            started_at.isoformat() if started_at else "",
            as_text(doc.get("lead_id")).strip(),
            as_text(doc.get("customer_id")).strip(),
        ),
        "company_id": company_id,
        "user_id": user_id,
        "lead_id": as_text(doc.get("lead_id")).strip(),
        "customer_id": as_text(doc.get("customer_id")).strip(),
        "purchase_id": as_text(doc.get("purchase_id")).strip(),
        "session_id": as_text(doc.get("session_id")).strip(),
        "phase": phase,
        "started_at": started_at,
        "completed_at": first_dt(doc, ["completed_at", "updated_at"]),
        "metadata_json": as_dict(doc.get("metadata")),
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_feedback(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    company_id = as_text(doc.get("company_id")).strip()
    session_id = as_text(doc.get("session_id")).strip()
    submitted_at = first_dt(doc, ["submitted_at", "created_at"])
    return {
        "id": with_fallback_id(
            doc.get("id"), "feedback", company_id, session_id, submitted_at.isoformat() if submitted_at else ""
        ),
        "company_id": company_id,
        "session_id": session_id,
        "rating": as_int(doc.get("rating"), 0),
        "comments": as_text(doc.get("comments")),
        "source": as_text(doc.get("source")).strip(),
        "submitted_at": submitted_at,
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def map_system_log(doc: dict[str, Any]) -> dict[str, Any]:
    created, updated = base_timestamps(doc)
    company_id = as_text(doc.get("company_id")).strip()
    user_id = as_text(doc.get("user_id")).strip()
    entity_type = as_text(doc.get("entity_type")).strip()
    entity_id = as_text(doc.get("entity_id")).strip()
    action_type = as_text(doc.get("action_type")).strip()
    return {
        "id": with_fallback_id(
            doc.get("id"), "system_log", company_id, user_id, entity_type, entity_id, action_type, created.isoformat()
        ),
        "company_id": company_id,
        "user_id": user_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "action_type": action_type,
        "details": as_dict(doc.get("details") or doc.get("metadata")),
        "created_at": created,
        "updated_at": updated,
        "raw_data": doc,
    }


def build_upsert_statement(model, rows: list[dict[str, Any]], conflict_columns: list[str]):
    stmt = pg_insert(model).values(rows)
    update_columns = {
        column.name: getattr(stmt.excluded, column.name)
        for column in model.__table__.columns
        if column.name not in {"id", *conflict_columns}
    }
    if update_columns:
        return stmt.on_conflict_do_update(index_elements=conflict_columns, set_=update_columns)
    return stmt.on_conflict_do_nothing(index_elements=conflict_columns)


async def upsert_batch(
    session: AsyncSession,
    model,
    rows: list[dict[str, Any]],
    conflict_columns: list[str],
) -> None:
    if not rows:
        return
    await session.execute(build_upsert_statement(model, rows, conflict_columns))
    await session.commit()


async def migrate_collection(
    session: AsyncSession,
    collection_name: str,
    model,
    mapper: Callable[[dict[str, Any]], dict[str, Any]],
    conflict_columns: list[str],
    required_fields: list[str] | None = None,
    batch_size: int = 500,
) -> int:
    raise RuntimeError(
        "Legacy-source migration is disabled in Postgres-only mode. "
        "Use existing PostgreSQL tables as the source of truth."
    )


MIGRATION_PLAN = [
    ("company_settings", CompanySetting, map_company_setting, ["id"], []),
    ("lead_scores", LeadScore, map_lead_score, ["id"], []),
    ("lead_tags", LeadTag, map_lead_tag, ["id"], []),
    ("llm_engines", LLMEngine, map_llm_engine, ["id"], []),
    ("ai_agents", AIAgent, map_ai_agent, ["id"], []),
    ("ai_sessions", AISession, map_ai_session, ["id"], []),
    ("training_data", TrainingData, map_training_data, ["id"], []),
    ("context_memories", ContextMemory, map_context_memory, ["id"], []),
    ("ai_embeddings", AIEmbedding, map_ai_embedding, ["id"], []),
    ("sentiment_analyses", SentimentAnalysis, map_sentiment_analysis, ["id"], []),
    ("mcp_servers", MCPServer, map_mcp_server, ["id"], []),
    ("mcp_clients", MCPClient, map_mcp_client, ["id"], []),
    ("api_endpoints", APIEndpoint, map_api_endpoint, ["id"], []),
    ("client_requests", ClientRequest, map_client_request, ["id"], []),
    ("webhook_handlers", WebhookHandler, map_webhook_handler, ["id"], []),
    ("webhook_events", WebhookEvent, map_webhook_event, ["id"], []),
    ("analytics_reports", AnalyticsReport, map_analytics_report, ["id"], []),
    ("metrics", Metric, map_metric, ["id"], []),
    ("social_accounts", SocialAccount, map_social_account, ["id"], []),
    ("social_posts", SocialPost, map_social_post, ["id"], []),
    ("journey_tracking", JourneyTracking, map_journey_tracking, ["id"], []),
    ("feedback", Feedback, map_feedback, ["id"], []),
    ("system_logs", SystemLog, map_system_log, ["id"], []),
]


async def run(drop_existing: bool = False, batch_size: int = 500) -> None:
    await create_postgres_schema(drop_existing=drop_existing)

    factory = session_factory()
    async with factory() as session:
        for collection_name, model, mapper, conflict_columns, required_fields in MIGRATION_PLAN:
            count = await migrate_collection(
                session=session,
                collection_name=collection_name,
                model=model,
                mapper=mapper,
                conflict_columns=conflict_columns,
                required_fields=required_fields,
                batch_size=batch_size,
            )
            print(f"[migrate] {collection_name} -> {model.__tablename__}: {count} rows")

    await get_engine().dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate analytics + AI/support collections (phase 4) from a legacy source to PostgreSQL."
    )
    parser.add_argument(
        "--drop-existing",
        action="store_true",
        help="Drop existing Postgres tables before creating schema.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Batch size for upsert operations.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run(drop_existing=args.drop_existing, batch_size=args.batch_size))
