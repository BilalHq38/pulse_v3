import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from shared.config import (
    database_url as shared_database_url,
    db_startup_retries,
    db_startup_retry_backoff_seconds,
    identity_default_tenant_id,
    identity_public_tenant_id,
    is_production,
    is_truthy,
)

logger = logging.getLogger(__name__)
IDENTITY_SCHEMA_MIGRATION = "backend/sql_migrations/012_identity_runtime_schema_hardening.sql"

PROJECT_ROOT = Path(__file__).resolve().parents[5]
load_dotenv(PROJECT_ROOT / ".env", override=False)

DATABASE_URL = (
    os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("POSTGRES_DSN") or ""
).strip() or shared_database_url()

if not DATABASE_URL:
    raise RuntimeError("Identity service DATABASE_URL (or POSTGRES_* settings) is required")

ASYNC_DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
REDIS_URL = (
    os.environ.get("CACHE_REDIS_URL") or os.environ.get("REDIS_URL") or os.environ.get("RATE_LIMIT_REDIS_URL") or ""
)

engine = create_async_engine(
    ASYNC_DATABASE_URL,
    pool_size=int(os.environ.get("DB_POOL_SIZE", "20")),
    max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "40")),
    pool_timeout=int(os.environ.get("DB_POOL_TIMEOUT", "30")),
    pool_recycle=int(os.environ.get("DB_POOL_RECYCLE", "1800")),
    pool_pre_ping=True,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

Base = declarative_base()


def _allow_insecure_db_role() -> bool:
    return is_truthy(os.environ.get("ALLOW_INSECURE_DB_ROLE"))


async def _assert_database_role_security(conn) -> None:
    row = await conn.execute(text("SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"))
    role = row.first()
    if not role:
        return
    role_name = str(role[0] or "")
    is_superuser = bool(role[1])
    bypass_rls = bool(role[2])
    if not is_superuser and not bypass_rls:
        return
    message = f"Identity service DB role '{role_name}' is over-privileged (superuser/BYPASSRLS)."
    if is_production() and not _allow_insecure_db_role():
        raise RuntimeError(message)


def _vector_enabled() -> bool:
    return (os.environ.get("IDENTITY_USE_VECTOR", "false") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


async def _identity_schema_ready(conn) -> bool:
    required_relations = (
        "unified_customers",
        "identity_mappings",
        "review_queue",
        "identity_events",
        "event_outbox",
        "dead_letter_queue",
    )
    missing: list[str] = []
    for relation in required_relations:
        exists = await conn.scalar(text("SELECT to_regclass(:relation) IS NOT NULL"), {"relation": relation})
        if not bool(exists):
            missing.append(f"relation:{relation}")
    if missing:
        logger.error(
            "runtime_schema_migration_required area=identity_service migration=%s missing=%s",
            IDENTITY_SCHEMA_MIGRATION,
            ",".join(missing),
        )
        return False
    return True


def _default_tenant_id() -> str:
    return identity_default_tenant_id("demo_tenant") or "demo_tenant"


def _tenant_id_for_request(request: Request | None) -> str:
    if request is None:
        return _default_tenant_id()
    request_tenant = str(getattr(request.state, "identity_tenant_id", "") or "").strip()
    if request_tenant:
        return request_tenant
    path = str(getattr(request.url, "path", "") or "")
    if path == "/api/unification/public" or path.startswith("/api/unification/public/"):
        return identity_public_tenant_id()
    header_tenant = str(request.headers.get("X-Tenant-ID") or "").strip()
    return header_tenant or _default_tenant_id()


async def get_db(request: Request = None):
    tenant_id = _tenant_id_for_request(request)
    async with AsyncSessionLocal() as session:
        try:
            await session.execute(
                text("SELECT set_config('app.current_company', :tenant_id, false)"),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text("SELECT set_config('app.current_tenant', :tenant_id, false)"),
                {"tenant_id": tenant_id},
            )
            await session.execute(text("SELECT set_config('app.platform_admin_mode', '', false)"))
            yield session
        finally:
            for setting in (
                "app.current_company",
                "app.current_tenant",
                "app.platform_admin_mode",
            ):
                try:
                    await session.execute(text(f"RESET {setting}"))
                except Exception:
                    pass
            await session.close()


async def close_db_engine():
    await engine.dispose()


async def init_db_schema() -> None:
    # Import models lazily so metadata includes every mapped table before create_all.
    from services.identity_service.app.db import models  # noqa: F401

    # Runtime schema mutation was moved to sql_migrations/012_identity_runtime_schema_hardening.sql.

    attempts = db_startup_retries()
    base_delay = db_startup_retry_backoff_seconds()
    for attempt in range(1, attempts + 1):
        try:
            async with engine.begin() as conn:
                await _assert_database_role_security(conn)
                if _vector_enabled():
                    extension_available = await conn.scalar(
                        text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
                    )
                    if not bool(extension_available):
                        raise RuntimeError(
                            "IDENTITY_USE_VECTOR is enabled but pgvector extension is unavailable. "
                            "Disable IDENTITY_USE_VECTOR or install pgvector."
                        )
                if not await _identity_schema_ready(conn):
                    raise RuntimeError(f"Identity DB schema is missing required objects. Run {IDENTITY_SCHEMA_MIGRATION}.")
            return
        except Exception as exc:
            if attempt >= attempts:
                raise
            delay_seconds = round(base_delay * attempt, 2)
            logger.warning(
                "Identity DB init retry attempt=%s/%s delay_seconds=%s error=%s",
                attempt,
                attempts,
                delay_seconds,
                exc,
            )
            await asyncio.sleep(delay_seconds)
