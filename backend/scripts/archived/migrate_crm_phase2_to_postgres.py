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
from postgres.models import Customer, CustomerProfile, Lead, LeadStatus, Purchase, Source  # noqa: E402
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


def map_lead_status(doc: dict[str, Any]) -> dict[str, Any]:
    company_id = as_text(doc.get("company_id"))
    status_name = as_text(doc.get("status_name"))
    return {
        "id": with_fallback_id(doc.get("id"), "lead_status", company_id, status_name),
        "company_id": company_id,
        "status_name": status_name,
        "description": as_text(doc.get("description")),
        "order_index": as_int(doc.get("order_index"), 0),
        "created_at": parse_dt(doc.get("created_at")) or datetime.now(timezone.utc),
        "updated_at": parse_dt(doc.get("updated_at")) or datetime.now(timezone.utc),
        "raw_data": doc,
    }


def map_source(doc: dict[str, Any]) -> dict[str, Any]:
    company_id = as_text(doc.get("company_id"))
    source_name = as_text(doc.get("source_name"))
    return {
        "id": with_fallback_id(doc.get("id"), "source", company_id, source_name),
        "company_id": company_id,
        "source_name": source_name,
        "source_type": as_text(doc.get("source_type")),
        "platform": as_text(doc.get("platform")),
        "created_at": parse_dt(doc.get("created_at")) or datetime.now(timezone.utc),
        "updated_at": parse_dt(doc.get("updated_at")) or datetime.now(timezone.utc),
        "raw_data": doc,
    }


def map_customer(doc: dict[str, Any]) -> dict[str, Any]:
    email = as_text(doc.get("email")).strip().lower()
    phone = as_text(doc.get("phone")).strip()
    name = as_text(doc.get("name")).strip()
    company_id = as_text(doc.get("company_id")).strip()
    return {
        "id": with_fallback_id(doc.get("id"), "customer", company_id, email, phone, name),
        "company_id": company_id,
        "lead_id": as_text(doc.get("lead_id")).strip(),
        "name": name,
        "email": email,
        "phone": phone,
        "company": as_text(doc.get("company")).strip(),
        "channels": as_list(doc.get("channels")),
        "tags": as_list(doc.get("tags")),
        "segment": as_text(doc.get("segment"), "general"),
        "avatar": as_text(doc.get("avatar")),
        "lifecycle_stage": as_text(doc.get("lifecycle_stage"), "lead"),
        "lifetime_value": as_float(doc.get("lifetime_value"), 0.0),
        "avg_sentiment": as_float(doc.get("avg_sentiment"), 0.0),
        "recent_tickets": as_int(doc.get("recent_tickets"), 0),
        "complaint_count": as_int(doc.get("complaint_count"), 0),
        "days_since_last_contact": as_int(doc.get("days_since_last_contact"), 0),
        "total_conversations": as_int(doc.get("total_conversations"), 0),
        "created_at": parse_dt(doc.get("created_at")) or datetime.now(timezone.utc),
        "updated_at": parse_dt(doc.get("updated_at")) or datetime.now(timezone.utc),
        "raw_data": doc,
    }


def map_customer_profile(doc: dict[str, Any]) -> dict[str, Any]:
    customer_id = as_text(doc.get("customer_id")).strip()
    return {
        "id": with_fallback_id(doc.get("id"), "customer_profile", customer_id),
        "customer_id": customer_id,
        "company_id": as_text(doc.get("company_id")).strip(),
        "preferences": as_dict(doc.get("preferences")),
        "behavioral_data": as_dict(doc.get("behavioral_data")),
        "engagement_level": as_text(doc.get("engagement_level"), "general"),
        "last_interaction": parse_dt(doc.get("last_interaction")),
        "created_at": parse_dt(doc.get("created_at")) or datetime.now(timezone.utc),
        "updated_at": parse_dt(doc.get("updated_at")) or datetime.now(timezone.utc),
        "raw_data": doc,
    }


def map_lead(doc: dict[str, Any]) -> dict[str, Any]:
    company_id = as_text(doc.get("company_id")).strip()
    email = as_text(doc.get("email")).strip().lower()
    phone = as_text(doc.get("phone")).strip()
    name = as_text(doc.get("name")).strip()
    status = as_text(doc.get("status"), "new").strip() or "new"
    source = as_text(doc.get("source"), "web_chat").strip() or "web_chat"
    return {
        "id": with_fallback_id(doc.get("id"), "lead", company_id, email, phone, name),
        "company_id": company_id,
        "name": name,
        "email": email,
        "phone": phone,
        "company": as_text(doc.get("company")).strip(),
        "source": source,
        "source_id": as_text(doc.get("source_id")).strip() or None,
        "status": status,
        "status_id": as_text(doc.get("status_id")).strip() or None,
        "score": as_int(doc.get("score"), 50),
        "grade": as_text(doc.get("grade"), "warm"),
        "notes": as_text(doc.get("notes")),
        "assigned_to": as_text(doc.get("assigned_to")).strip(),
        "assigned_name": as_text(doc.get("assigned_name")).strip(),
        "activities": as_list(doc.get("activities")),
        "scoring_reason": as_text(doc.get("scoring_reason")),
        "next_action": as_text(doc.get("next_action")),
        "created_at": parse_dt(doc.get("created_at")) or datetime.now(timezone.utc),
        "updated_at": parse_dt(doc.get("updated_at")) or datetime.now(timezone.utc),
        "raw_data": doc,
    }


def map_purchase(doc: dict[str, Any]) -> dict[str, Any]:
    customer_id = as_text(doc.get("customer_id")).strip()
    company_id = as_text(doc.get("company_id")).strip()
    purchase_date = parse_dt(doc.get("purchase_date"))
    return {
        "id": with_fallback_id(
            doc.get("id"),
            "purchase",
            customer_id,
            company_id,
            purchase_date.isoformat() if purchase_date else "",
            as_float(doc.get("amount"), 0.0),
            as_text(doc.get("currency"), "USD"),
        ),
        "customer_id": customer_id,
        "company_id": company_id,
        "amount": as_float(doc.get("amount"), 0.0),
        "currency": as_text(doc.get("currency"), "USD"),
        "product_category": as_text(doc.get("product_category"), "general"),
        "product_details": as_dict(doc.get("product_details")),
        "purchase_date": purchase_date,
        "created_at": parse_dt(doc.get("created_at")) or datetime.now(timezone.utc),
        "updated_at": parse_dt(doc.get("updated_at")) or datetime.now(timezone.utc),
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
    ("lead_statuses", LeadStatus, map_lead_status, ["id"], []),
    ("sources", Source, map_source, ["id"], []),
    ("customers", Customer, map_customer, ["id"], []),
    ("leads", Lead, map_lead, ["id"], []),
    ("customer_profiles", CustomerProfile, map_customer_profile, ["customer_id"], ["customer_id"]),
    ("purchases", Purchase, map_purchase, ["id"], ["customer_id"]),
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
        description="Migrate CRM phase-2 collections (customers + leads) from a legacy source to PostgreSQL."
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
