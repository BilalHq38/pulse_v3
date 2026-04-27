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

    fallback_tenant = _default_tenant_id()
    compatibility_patches = [
        "CREATE EXTENSION IF NOT EXISTS pgcrypto",
        "ALTER TABLE IF EXISTS unified_customers ADD COLUMN IF NOT EXISTS id UUID",
        "UPDATE unified_customers SET id = customer_id WHERE id IS NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_unified_customers_id ON unified_customers(id)",
        "ALTER TABLE IF EXISTS unified_customers ADD COLUMN IF NOT EXISTS primary_identity TEXT",
        "UPDATE unified_customers SET primary_identity = COALESCE(primary_identity, primary_phone_hash, primary_email_hash, customer_id::text)",  # noqa: E501
        "ALTER TABLE IF EXISTS identity_mappings ADD COLUMN IF NOT EXISTS id UUID",
        "UPDATE identity_mappings SET id = mapping_id WHERE id IS NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_identity_mappings_id ON identity_mappings(id)",
        "ALTER TABLE IF EXISTS identity_mappings ADD COLUMN IF NOT EXISTS phone TEXT",
        "ALTER TABLE IF EXISTS identity_mappings ADD COLUMN IF NOT EXISTS email TEXT",
        "ALTER TABLE IF EXISTS identity_mappings ADD COLUMN IF NOT EXISTS name TEXT",
        "ALTER TABLE IF EXISTS identity_mappings ADD COLUMN IF NOT EXISTS fingerprint TEXT",
        "ALTER TABLE IF EXISTS identity_mappings ADD COLUMN IF NOT EXISTS confidence_score DOUBLE PRECISION",
        "UPDATE identity_mappings SET confidence_score = COALESCE(confidence_score, confidence)",
        "CREATE INDEX IF NOT EXISTS idx_identity_mappings_mapping_id ON identity_mappings(mapping_id)",
        "CREATE INDEX IF NOT EXISTS idx_unified_customers_customer_id ON unified_customers(customer_id)",
        "ALTER TABLE IF EXISTS review_queue ADD COLUMN IF NOT EXISTS source VARCHAR(64)",
        "UPDATE review_queue SET source = 'internal' WHERE source IS NULL OR BTRIM(source) = ''",
        "CREATE INDEX IF NOT EXISTS idx_review_queue_source ON review_queue(source)",
        "CREATE INDEX IF NOT EXISTS idx_review_queue_status ON review_queue(status)",
        "ALTER TABLE IF EXISTS dead_letter_queue ADD COLUMN IF NOT EXISTS task_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE IF EXISTS dead_letter_queue ADD COLUMN IF NOT EXISTS event_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE IF EXISTS dead_letter_queue ADD COLUMN IF NOT EXISTS trace_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE IF EXISTS dead_letter_queue ADD COLUMN IF NOT EXISTS channel TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE IF EXISTS dead_letter_queue ADD COLUMN IF NOT EXISTS error TEXT NOT NULL DEFAULT ''",
        "UPDATE resolution_audit_log ral SET customer_id = NULL WHERE customer_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM unified_customers uc WHERE uc.customer_id = ral.customer_id)",  # noqa: E501
        "UPDATE profile_merge_history pmh SET source_customer_id = NULL WHERE source_customer_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM unified_customers uc WHERE uc.customer_id = pmh.source_customer_id)",  # noqa: E501
        "UPDATE profile_merge_history pmh SET target_customer_id = NULL WHERE target_customer_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM unified_customers uc WHERE uc.customer_id = pmh.target_customer_id)",  # noqa: E501
        "UPDATE review_queue rq SET source_customer_id = NULL WHERE source_customer_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM unified_customers uc WHERE uc.customer_id = rq.source_customer_id)",  # noqa: E501
        "UPDATE review_queue rq SET candidate_customer_id = NULL WHERE candidate_customer_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM unified_customers uc WHERE uc.customer_id = rq.candidate_customer_id)",  # noqa: E501
        "ALTER TABLE IF EXISTS profile_merge_history ALTER COLUMN source_customer_id DROP NOT NULL",
        "ALTER TABLE IF EXISTS profile_merge_history ALTER COLUMN target_customer_id DROP NOT NULL",
        "ALTER TABLE IF EXISTS review_queue ALTER COLUMN source_customer_id DROP NOT NULL",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_resolution_audit_log_customer_id') THEN ALTER TABLE resolution_audit_log ADD CONSTRAINT fk_resolution_audit_log_customer_id FOREIGN KEY (customer_id) REFERENCES unified_customers(customer_id) ON DELETE SET NULL; END IF; END $$",  # noqa: E501
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_profile_merge_history_source_customer_id') THEN ALTER TABLE profile_merge_history ADD CONSTRAINT fk_profile_merge_history_source_customer_id FOREIGN KEY (source_customer_id) REFERENCES unified_customers(customer_id) ON DELETE SET NULL; END IF; END $$",  # noqa: E501
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_profile_merge_history_target_customer_id') THEN ALTER TABLE profile_merge_history ADD CONSTRAINT fk_profile_merge_history_target_customer_id FOREIGN KEY (target_customer_id) REFERENCES unified_customers(customer_id) ON DELETE SET NULL; END IF; END $$",  # noqa: E501
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_review_queue_source_customer_id') THEN ALTER TABLE review_queue ADD CONSTRAINT fk_review_queue_source_customer_id FOREIGN KEY (source_customer_id) REFERENCES unified_customers(customer_id) ON DELETE SET NULL; END IF; END $$",  # noqa: E501
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_review_queue_candidate_customer_id') THEN ALTER TABLE review_queue ADD CONSTRAINT fk_review_queue_candidate_customer_id FOREIGN KEY (candidate_customer_id) REFERENCES unified_customers(customer_id) ON DELETE SET NULL; END IF; END $$",  # noqa: E501
        f"CREATE TABLE IF NOT EXISTS identity_history (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id VARCHAR(128) NOT NULL DEFAULT '{fallback_tenant}', customer_id UUID REFERENCES unified_customers(customer_id) ON DELETE CASCADE, event_type TEXT NOT NULL, data_snapshot JSONB NOT NULL DEFAULT '{{}}'::jsonb, timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW())",  # noqa: E501
        "ALTER TABLE IF EXISTS identity_history ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(128)",
        "CREATE INDEX IF NOT EXISTS idx_identity_history_customer ON identity_history(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_identity_history_timestamp ON identity_history(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_identity_history_tenant ON identity_history(tenant_id)",
        f"UPDATE identity_history ih SET tenant_id = COALESCE(NULLIF(ih.tenant_id, ''), (SELECT uc.tenant_id FROM unified_customers uc WHERE uc.customer_id = ih.customer_id), '{fallback_tenant}')",  # noqa: E501
        "ALTER TABLE IF EXISTS identity_history ALTER COLUMN tenant_id SET NOT NULL",
        f"CREATE TABLE IF NOT EXISTS consent_records (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id VARCHAR(128) NOT NULL DEFAULT '{fallback_tenant}', customer_id UUID REFERENCES unified_customers(customer_id) ON DELETE SET NULL, consent_given BOOLEAN NOT NULL DEFAULT FALSE, timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW())",  # noqa: E501
        "ALTER TABLE IF EXISTS consent_records ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(128)",
        "CREATE INDEX IF NOT EXISTS idx_consent_records_customer ON consent_records(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_consent_records_timestamp ON consent_records(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_consent_records_tenant ON consent_records(tenant_id)",
        f"UPDATE consent_records cr SET tenant_id = COALESCE(NULLIF(cr.tenant_id, ''), (SELECT uc.tenant_id FROM unified_customers uc WHERE uc.customer_id = cr.customer_id), '{fallback_tenant}')",  # noqa: E501
        "ALTER TABLE IF EXISTS consent_records ALTER COLUMN tenant_id SET NOT NULL",
        "CREATE TABLE IF NOT EXISTS identity_events (event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id VARCHAR(128) NOT NULL, event_type VARCHAR(64) NOT NULL, aggregate_customer_id UUID, idempotency_key VARCHAR(191), payload JSONB NOT NULL DEFAULT '{}'::jsonb, status VARCHAR(32) NOT NULL DEFAULT 'pending', error_message TEXT, published_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",  # noqa: E501
        "CREATE INDEX IF NOT EXISTS idx_identity_events_tenant ON identity_events(tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_identity_events_event_type ON identity_events(event_type)",
        "CREATE INDEX IF NOT EXISTS idx_identity_events_created_at ON identity_events(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_identity_events_status ON identity_events(status)",
        "CREATE INDEX IF NOT EXISTS idx_identity_events_aggregate_customer ON identity_events(aggregate_customer_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_identity_events_tenant_idempotency ON identity_events(tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL",  # noqa: E501
        "CREATE TABLE IF NOT EXISTS event_outbox (outbox_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), event_id UUID NOT NULL UNIQUE REFERENCES identity_events(event_id) ON DELETE CASCADE, tenant_id VARCHAR(128) NOT NULL, stream_name VARCHAR(128) NOT NULL DEFAULT 'identity.events', idempotency_key VARCHAR(191), payload JSONB NOT NULL DEFAULT '{}'::jsonb, status VARCHAR(32) NOT NULL DEFAULT 'queued', retry_count INTEGER NOT NULL DEFAULT 0, next_retry_at TIMESTAMPTZ, last_error TEXT, published_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",  # noqa: E501
        "CREATE INDEX IF NOT EXISTS idx_event_outbox_tenant ON event_outbox(tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_event_outbox_status ON event_outbox(status)",
        "CREATE INDEX IF NOT EXISTS idx_event_outbox_next_retry ON event_outbox(next_retry_at)",
        "CREATE INDEX IF NOT EXISTS idx_event_outbox_created_at ON event_outbox(created_at)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_event_outbox_tenant_idempotency ON event_outbox(tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL",  # noqa: E501
    ]

    attempts = db_startup_retries()
    base_delay = db_startup_retry_backoff_seconds()
    for attempt in range(1, attempts + 1):
        try:
            async with engine.begin() as conn:
                await _assert_database_role_security(conn)
                if _vector_enabled():
                    await conn.execute(
                        text(
                            """
                            DO $$
                            BEGIN
                                BEGIN
                                    CREATE EXTENSION IF NOT EXISTS vector;
                                EXCEPTION
                                    WHEN undefined_file THEN
                                        RAISE NOTICE 'pgvector not installed, IDENTITY_USE_VECTOR cannot be enabled.';
                                    WHEN feature_not_supported THEN
                                        RAISE NOTICE 'pgvector not supported on this PostgreSQL instance.';
                                    WHEN insufficient_privilege THEN
                                        RAISE NOTICE 'insufficient privilege to create pgvector extension.';
                                END;
                            END
                            $$;
                            """
                        )
                    )
                    extension_available = await conn.scalar(
                        text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
                    )
                    if not bool(extension_available):
                        raise RuntimeError(
                            "IDENTITY_USE_VECTOR is enabled but pgvector extension is unavailable. "
                            "Disable IDENTITY_USE_VECTOR or install pgvector."
                        )

                await conn.run_sync(Base.metadata.create_all)
                for statement in compatibility_patches:
                    await conn.execute(text(statement))
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
