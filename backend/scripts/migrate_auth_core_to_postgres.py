import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from postgres.bootstrap import create_postgres_schema  # noqa: E402
from postgres.models import AuthLog, Company, LoginHistory, LoginSession, Role, User, UserSession  # noqa: E402
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


def map_role(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": as_text(doc.get("id")),
        "role_name": as_text(doc.get("role_name")),
        "description": as_text(doc.get("description")),
        "is_system": as_bool(doc.get("is_system"), False),
        "created_at": parse_dt(doc.get("created_at")) or datetime.now(timezone.utc),
        "updated_at": parse_dt(doc.get("updated_at")) or datetime.now(timezone.utc),
        "raw_data": doc,
    }


def map_company(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": as_text(doc.get("id")),
        "company_name": as_text(doc.get("company_name") or doc.get("name")),
        "name": as_text(doc.get("name") or doc.get("company_name")),
        "type": as_text(doc.get("type")),
        "industry": as_text(doc.get("industry")),
        "timezone": as_text(doc.get("timezone"), "UTC"),
        "status": as_text(doc.get("status"), "active"),
        "created_at": parse_dt(doc.get("created_at")) or datetime.now(timezone.utc),
        "updated_at": parse_dt(doc.get("updated_at")) or datetime.now(timezone.utc),
        "raw_data": doc,
    }


def map_user(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": as_text(doc.get("id")),
        "email": as_text(doc.get("email")).lower(),
        "password": as_text(doc.get("password")),
        "name": as_text(doc.get("name")),
        "role": as_text(doc.get("role"), "admin"),
        "role_id": as_text(doc.get("role_id")) or None,
        "sub_role": as_text(doc.get("sub_role")),
        "status": as_text(doc.get("status"), "active"),
        "avatar": as_text(doc.get("avatar")),
        "company_id": as_text(doc.get("company_id")) or None,
        "phone": as_text(doc.get("phone")),
        "onboarding_completed": as_bool(doc.get("onboarding_completed"), False),
        "auth_provider": as_text(doc.get("auth_provider"), "email"),
        "email_verified": as_bool(doc.get("email_verified"), False),
        "created_at": parse_dt(doc.get("created_at")) or datetime.now(timezone.utc),
        "updated_at": parse_dt(doc.get("updated_at")) or datetime.now(timezone.utc),
        "last_login": parse_dt(doc.get("last_login")),
        "raw_data": doc,
    }


def map_login_history(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": as_text(doc.get("id")),
        "user_id": as_text(doc.get("user_id")),
        "email": as_text(doc.get("email")).lower(),
        "event": as_text(doc.get("event")),
        "ip_address": as_text(doc.get("ip_address")),
        "created_at": parse_dt(doc.get("created_at")) or datetime.now(timezone.utc),
        "raw_data": doc,
    }


def map_user_session(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": as_text(doc.get("id")),
        "session_token": as_text(doc.get("session_token")),
        "user_id": as_text(doc.get("user_id")),
        "user_agent": as_text(doc.get("user_agent")),
        "ip_address": as_text(doc.get("ip_address")),
        "created_at": parse_dt(doc.get("created_at")) or datetime.now(timezone.utc),
        "expires_at": parse_dt(doc.get("expires_at")),
        "last_activity": parse_dt(doc.get("last_activity")),
        "raw_data": doc,
    }


def map_login_session(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": as_text(doc.get("id")),
        "user_id": as_text(doc.get("user_id")),
        "company_id": as_text(doc.get("company_id")),
        "ip_address": as_text(doc.get("ip_address")),
        "device": as_text(doc.get("device")),
        "session_token": as_text(doc.get("session_token")),
        "status": as_text(doc.get("status"), "active"),
        "login_time": parse_dt(doc.get("login_time")),
        "logout_time": parse_dt(doc.get("logout_time")),
        "is_active": as_bool(doc.get("is_active"), True),
        "created_at": parse_dt(doc.get("created_at")) or datetime.now(timezone.utc),
        "updated_at": parse_dt(doc.get("updated_at")) or datetime.now(timezone.utc),
        "raw_data": doc,
    }


def map_auth_log(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": as_text(doc.get("id")),
        "user_id": as_text(doc.get("user_id")),
        "event_type": as_text(doc.get("event_type")),
        "ip_address": as_text(doc.get("ip_address")),
        "device": as_text(doc.get("device")),
        "success": as_bool(doc.get("success"), False),
        "email": as_text(doc.get("email")).lower(),
        "event_time": parse_dt(doc.get("event_time")),
        "created_at": parse_dt(doc.get("created_at")) or datetime.now(timezone.utc),
        "raw_data": doc,
    }


def build_upsert_statement(model, rows: list[dict[str, Any]]):
    stmt = pg_insert(model).values(rows)
    update_columns = {
        column.name: getattr(stmt.excluded, column.name) for column in model.__table__.columns if column.name != "id"
    }
    return stmt.on_conflict_do_update(index_elements=["id"], set_=update_columns)


async def upsert_batch(session: AsyncSession, model, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    await session.execute(build_upsert_statement(model, rows))
    await session.commit()


async def migrate_collection(
    session: AsyncSession,
    collection_name: str,
    model,
    mapper: Callable[[dict[str, Any]], dict[str, Any]],
    batch_size: int = 500,
) -> int:
    raise RuntimeError(
        "Legacy-source migration is disabled in Postgres-only mode. "
        "Use existing PostgreSQL tables as the source of truth."
    )


MIGRATION_PLAN = [
    ("roles", Role, map_role),
    ("companies", Company, map_company),
    ("users", User, map_user),
    ("login_history", LoginHistory, map_login_history),
    ("user_sessions", UserSession, map_user_session),
    ("login_sessions", LoginSession, map_login_session),
    ("auth_logs", AuthLog, map_auth_log),
]


async def run(drop_existing: bool = False, batch_size: int = 500) -> None:
    await create_postgres_schema(drop_existing=drop_existing)

    factory = session_factory()
    async with factory() as session:
        for collection_name, model, mapper in MIGRATION_PLAN:
            count = await migrate_collection(
                session=session,
                collection_name=collection_name,
                model=model,
                mapper=mapper,
                batch_size=batch_size,
            )
            print(f"[migrate] {collection_name} -> {model.__tablename__}: {count} rows")

    await get_engine().dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate auth/core collections from a legacy source to PostgreSQL.")
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
