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
    Channel,
    Conversation,
    ConversationLog,
    Message,
    MessageAttachment,
    Ticket,
    TicketStatus,
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


def as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


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


def map_channel(doc: dict[str, Any]) -> dict[str, Any]:
    company_id = as_text(doc.get("company_id")).strip()
    platform = as_text(doc.get("platform")).strip()
    channel_name = as_text(doc.get("channel_name")).strip()
    return {
        "id": with_fallback_id(doc.get("id"), "channel", company_id, platform, channel_name),
        "company_id": company_id,
        "channel_name": channel_name,
        "channel_type": as_text(doc.get("channel_type")).strip(),
        "platform": platform,
        "created_at": parse_dt(doc.get("created_at")) or datetime.now(timezone.utc),
        "updated_at": parse_dt(doc.get("updated_at")) or datetime.now(timezone.utc),
        "raw_data": doc,
    }


def map_ticket_status(doc: dict[str, Any]) -> dict[str, Any]:
    company_id = as_text(doc.get("company_id")).strip()
    status_name = as_text(doc.get("status_name")).strip()
    return {
        "id": with_fallback_id(doc.get("id"), "ticket_status", company_id, status_name),
        "company_id": company_id,
        "status_name": status_name,
        "color_code": as_text(doc.get("color_code")).strip(),
        "created_at": parse_dt(doc.get("created_at")) or datetime.now(timezone.utc),
        "updated_at": parse_dt(doc.get("updated_at")) or datetime.now(timezone.utc),
        "raw_data": doc,
    }


def map_conversation(doc: dict[str, Any]) -> dict[str, Any]:
    company_id = as_text(doc.get("company_id")).strip()
    customer_id = as_text(doc.get("customer_id")).strip()
    channel = as_text(doc.get("channel"), "web_chat").strip() or "web_chat"
    created_at = parse_dt(doc.get("created_at")) or datetime.now(timezone.utc)

    sentiment_score = as_float(doc.get("sentiment_score"), 0.0)
    if sentiment_score == 0.0:
        maybe_sentiment = doc.get("sentiment")
        if isinstance(maybe_sentiment, dict):
            sentiment_score = as_float(maybe_sentiment.get("score"), 0.0)

    return {
        "id": with_fallback_id(
            doc.get("id"),
            "conversation",
            company_id,
            customer_id,
            channel,
            created_at.isoformat(),
        ),
        "company_id": company_id,
        "customer_id": customer_id,
        "customer_name": as_text(doc.get("customer_name")).strip(),
        "customer_avatar": as_text(doc.get("customer_avatar")).strip(),
        "channel": channel,
        "channel_id": as_text(doc.get("channel_id")).strip(),
        "subject": as_text(doc.get("subject")).strip(),
        "status": as_text(doc.get("status"), "open").strip() or "open",
        "priority": as_text(doc.get("priority"), "medium").strip() or "medium",
        "assigned_to": as_text(doc.get("assigned_to")).strip(),
        "assigned_name": as_text(doc.get("assigned_name")).strip(),
        "ai_handled": as_bool(doc.get("ai_handled"), True),
        "sentiment_score": sentiment_score,
        "sentiment_label": as_text(doc.get("sentiment_label"), "neutral").strip() or "neutral",
        "message_count": as_int(doc.get("message_count"), 0),
        "last_message": as_text(doc.get("last_message")),
        "last_message_at": parse_dt(doc.get("last_message_at")),
        "unread_count": as_int(doc.get("unread_count"), 0),
        "tags": as_list(doc.get("tags")),
        "metadata_json": as_dict(doc.get("metadata")),
        "escalation_notice": as_text(doc.get("escalation_notice")),
        "escalated_at": parse_dt(doc.get("escalated_at")),
        "escalated_to": as_text(doc.get("escalated_to")).strip(),
        "escalated_to_name": as_text(doc.get("escalated_to_name")).strip(),
        "created_at": created_at,
        "updated_at": parse_dt(doc.get("updated_at")) or created_at,
        "raw_data": doc,
    }


def map_message(doc: dict[str, Any]) -> dict[str, Any]:
    conversation_id = as_text(doc.get("conversation_id")).strip()
    created_at = parse_dt(doc.get("created_at")) or datetime.now(timezone.utc)
    sender_id = as_text(doc.get("sender_id")).strip()
    content = as_text(doc.get("content"))
    return {
        "id": with_fallback_id(
            doc.get("id"),
            "message",
            conversation_id,
            sender_id,
            created_at.isoformat(),
            content[:128],
        ),
        "company_id": as_text(doc.get("company_id")).strip(),
        "conversation_id": conversation_id,
        "content": content,
        "sender_type": as_text(doc.get("sender_type"), "agent").strip() or "agent",
        "sender_id": sender_id,
        "sender_name": as_text(doc.get("sender_name")).strip(),
        "attachments": as_list(doc.get("attachments")),
        "sentiment": doc.get("sentiment"),
        "intent": doc.get("intent"),
        "read": as_bool(doc.get("read"), False),
        "edited_at": parse_dt(doc.get("edited_at")),
        "created_at": created_at,
        "raw_data": doc,
    }


def map_ticket(doc: dict[str, Any]) -> dict[str, Any]:
    company_id = as_text(doc.get("company_id")).strip()
    ticket_number = as_text(doc.get("ticket_number")).strip()
    conversation_id = as_text(doc.get("conversation_id")).strip()
    created_at = parse_dt(doc.get("created_at")) or datetime.now(timezone.utc)
    return {
        "id": with_fallback_id(
            doc.get("id"),
            "ticket",
            company_id,
            ticket_number,
            conversation_id,
            created_at.isoformat(),
        ),
        "ticket_number": ticket_number,
        "conversation_id": conversation_id,
        "customer_id": as_text(doc.get("customer_id")).strip(),
        "subject": as_text(doc.get("subject")).strip(),
        "description": as_text(doc.get("description")),
        "priority": as_text(doc.get("priority"), "medium").strip() or "medium",
        "category": as_text(doc.get("category"), "general").strip() or "general",
        "status": as_text(doc.get("status"), "open").strip() or "open",
        "status_id": as_text(doc.get("status_id")).strip(),
        "assigned_to": as_text(doc.get("assigned_to")).strip(),
        "assigned_name": as_text(doc.get("assigned_name")).strip(),
        "resolution": as_text(doc.get("resolution")),
        "sla_deadline": parse_dt(doc.get("sla_deadline")),
        "notes": as_list(doc.get("notes")),
        "company_id": company_id,
        "created_at": created_at,
        "updated_at": parse_dt(doc.get("updated_at")) or created_at,
        "resolved_at": parse_dt(doc.get("resolved_at")),
        "raw_data": doc,
    }


def map_conversation_log(doc: dict[str, Any]) -> dict[str, Any]:
    convo_id = as_text(doc.get("convo_id")).strip()
    action_type = as_text(doc.get("action_type")).strip()
    logged_at = parse_dt(doc.get("logged_at"))
    user_id = as_text(doc.get("user_id")).strip()
    return {
        "id": with_fallback_id(
            doc.get("id"),
            "conversation_log",
            convo_id,
            action_type,
            user_id,
            logged_at.isoformat() if logged_at else "",
        ),
        "convo_id": convo_id,
        "user_id": user_id,
        "action_type": action_type,
        "metadata_json": as_dict(doc.get("metadata")),
        "logged_at": logged_at,
        "created_at": parse_dt(doc.get("created_at")) or datetime.now(timezone.utc),
        "raw_data": doc,
    }


def map_message_attachment(doc: dict[str, Any]) -> dict[str, Any]:
    message_id = as_text(doc.get("message_id")).strip()
    file_url = as_text(doc.get("file_url")).strip()
    uploaded_at = parse_dt(doc.get("uploaded_at"))
    file_type = as_text(doc.get("file_type"), "unknown").strip() or "unknown"
    return {
        "id": with_fallback_id(
            doc.get("id"),
            "message_attachment",
            message_id,
            file_url,
            file_type,
            uploaded_at.isoformat() if uploaded_at else "",
        ),
        "message_id": message_id,
        "file_type": file_type,
        "file_url": file_url,
        "file_size": as_int(doc.get("file_size"), 0),
        "uploaded_at": uploaded_at,
        "created_at": parse_dt(doc.get("created_at")) or datetime.now(timezone.utc),
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
    ("channels", Channel, map_channel, ["id"], []),
    ("ticket_statuses", TicketStatus, map_ticket_status, ["id"], []),
    ("conversations", Conversation, map_conversation, ["id"], []),
    ("messages", Message, map_message, ["id"], ["conversation_id"]),
    ("tickets", Ticket, map_ticket, ["id"], []),
    ("conversation_logs", ConversationLog, map_conversation_log, ["id"], ["convo_id"]),
    ("message_attachments", MessageAttachment, map_message_attachment, ["id"], ["message_id"]),
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
        description="Migrate CRM phase-3 collections (tickets + conversations + messages) from a legacy source to PostgreSQL."  # noqa: E501
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
