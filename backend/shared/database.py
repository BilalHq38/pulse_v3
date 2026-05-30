from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Any, Optional

import asyncpg

from shared.background_queue import get_background_queue
from shared.config import (
    database_url,
    db_command_timeout_seconds,
    db_connect_timeout_seconds,
    db_pool_max_size,
    db_pool_min_size,
    db_schema,
    db_startup_retries,
    db_startup_retry_backoff_seconds,
    is_production,
    is_truthy,
    service_name,
)

logger = logging.getLogger(__name__)
_request_conn: contextvars.ContextVar[Optional[asyncpg.Connection]] = contextvars.ContextVar(
    "request_conn", default=None
)
_request_company_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_company_id", default=None)
_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PLATFORM_ADMIN_RLS_TABLES = (
    "companies",
    "company_settings",
    "users",
    "sessions",
    "security_events",
    "api_keys",
    "leads",
    "lead_activities",
    "lead_nurture_messages",
    "customers",
    "customer_profiles",
    "conversations",
    "messages",
    "tickets",
    "company_products",
    "ai_sessions",
    "webhook_events",
    "system_logs",
    "system_log_metadata",
    "tenant_meta_config",
    "whatsapp_channels",
    "meta_message_templates",
    "meta_api_usage",
    "billing_customers",
    "subscriptions",
    "usage_ledger",
    "stripe_webhook_events",
    "consent_ledger",
    "unified_customers",
    "identity_mappings",
    "device_fingerprints",
    "resolution_audit_log",
    "profile_merge_history",
    "review_queue",
    "identity_history",
    "consent_records",
    "identity_events",
    "event_outbox",
    "raw_events",
    "raw_messages",
    "raw_leads",
    "analytics_events",
    "lead_metrics",
    "conversation_metrics",
    "sentiment_logs",
    # Additional tables requiring platform-admin visibility
    "notifications",
    "notification_settings",
    "context_memories",
    "user_ai_memories",
    "journey_tracking",
    "embeddings",
    "embedding_jobs",
    "sentiment_analyses",
)
_PLATFORM_ADMIN_POLICY_LOCK_ID = 90210418


def _log_background_task_result(task: asyncio.Task) -> None:
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Failed to inspect background task state")
        return
    if exc is not None:
        logger.warning("Background task failed", exc_info=exc)


def _close_unstarted_coroutines(value, seen: set[int] | None = None) -> None:
    if seen is None:
        seen = set()
    value_id = id(value)
    if value_id in seen:
        return
    seen.add(value_id)
    if inspect.iscoroutine(value):
        try:
            if inspect.getcoroutinestate(value) != "CORO_CREATED":
                return
        except Exception:
            return
        frame = getattr(value, "cr_frame", None)
        if frame is not None:
            for local_value in list(frame.f_locals.values()):
                _close_unstarted_coroutines(local_value, seen)
        value.close()
        return
    if isinstance(value, dict):
        for item in value.values():
            _close_unstarted_coroutines(item, seen)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _close_unstarted_coroutines(item, seen)


async def _dispatch_detached_task(
    coro,
    *,
    name: str | None = None,
    job_id: str | None = None,
    idempotency_key: str | None = None,
    timeout_seconds: float | None = None,
    service_label: str | None = None,
) -> None:
    queue = get_background_queue(service_label=service_label)
    enqueue_failed = False
    if queue is not None:
        try:
            await queue.enqueue_coroutine(
                coro,
                name=name,
                job_id=job_id,

                idempotency_key=idempotency_key,
                timeout_seconds=timeout_seconds,
            )
            _close_unstarted_coroutines(coro)
            return
        except Exception as exc:
            logger.warning("Background queue enqueue failed, falling back locally: %s", exc)
            enqueue_failed = True

    # Local fallback — reached only when (a) no queue is configured (queue is None)
    # or (b) enqueue raised. The coroutine was never started in either case, so
    # awaiting it here is safe.
    try:
        if timeout_seconds:
            await asyncio.wait_for(coro, timeout=timeout_seconds)
        else:
            await coro
    except Exception as exc:
        logger.error(
            "Detached task local fallback failed name=%s: %s",
            name or "unnamed",
            exc,
        )


def create_detached_task(
    coro,
    *,
    name: str | None = None,
    job_id: str | None = None,
    idempotency_key: str | None = None,
    timeout_seconds: float | None = None,
    service_label: str | None = None,
) -> asyncio.Task:
    """Spawn a task without inheriting the request-bound database connection."""

    ctx = contextvars.copy_context()
    ctx.run(_request_conn.set, None)
    ctx.run(_request_company_id.set, None)

    def _spawn() -> asyncio.Task:
        if name:
            return asyncio.create_task(
                _dispatch_detached_task(
                    coro,
                    name=name,
                    job_id=job_id,
                    idempotency_key=idempotency_key,
                    timeout_seconds=timeout_seconds,
                    service_label=service_label,
                ),
                name=name,
            )
        return asyncio.create_task(
            _dispatch_detached_task(
                coro,
                job_id=job_id,
                idempotency_key=idempotency_key,
                timeout_seconds=timeout_seconds,
                service_label=service_label,
            )
        )

    task = ctx.run(_spawn)
    task.add_done_callback(_log_background_task_result)
    return task


@asynccontextmanager
async def company_context(db: "Database", company_id: str, *, role: str = "admin"):
    scoped_company_id = (company_id or "").strip()
    if not scoped_company_id:
        raise ValueError("company_id is required for company_context")
    pool = await db._get_pool()
    async with pool.acquire() as conn:
        conn_token, company_token = await db.bind_request_connection(
            conn,
            scoped_company_id,
            auth_context={"company_id": scoped_company_id, "role": role},
        )
        try:
            yield conn
        finally:
            await db.unbind_request_connection(conn, conn_token, company_token)


@asynccontextmanager
async def platform_admin_context(db: "Database"):
    pool = await db._get_pool()
    async with pool.acquire() as conn:
        conn_token, company_token = await db.bind_request_connection(
            conn,
            None,
            auth_context={"role": "super_admin"},
        )
        try:
            yield conn
        finally:
            await db.unbind_request_connection(conn, conn_token, company_token)


def _normalize_schema(schema: str | None) -> str:
    candidate = (schema or "").strip() or "public"
    if not _SCHEMA_RE.fullmatch(candidate):
        raise ValueError(f"Invalid PostgreSQL schema name: {candidate}")
    return candidate


def _allow_insecure_db_role() -> bool:
    return is_truthy(os.environ.get("ALLOW_INSECURE_DB_ROLE"))


def _check_production_tls() -> None:
    """Raise in production when the database connection is not TLS-encrypted."""
    if not is_production():
        return
    ssl_mode = os.environ.get("POSTGRES_SSLMODE", os.environ.get("PGSSLMODE", "disable")).strip().lower()
    if ssl_mode not in ("require", "verify-ca", "verify-full"):
        raise RuntimeError(
            f"Production requires POSTGRES_SSLMODE=require (got '{ssl_mode}'). "
            "Set POSTGRES_SSLMODE=require in your environment or Secrets Manager entry."
        )


async def _validate_database_role_security(conn: asyncpg.Connection, *, app_name: str) -> None:
    role_row = await conn.fetchrow("SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    if not role_row:
        return
    role_name = str(role_row.get("rolname") or "")
    is_superuser = bool(role_row.get("rolsuper"))
    has_bypassrls = bool(role_row.get("rolbypassrls"))
    if not is_superuser and not has_bypassrls:
        return

    message = (
        f"Database role '{role_name}' for {app_name} is over-privileged "
        "(superuser/BYPASSRLS). Use a least-privilege role without BYPASSRLS."
    )
    if is_production() and not _allow_insecure_db_role():
        raise RuntimeError(message)
    logger.warning(message)


async def _ensure_platform_admin_rls_policies(conn: asyncpg.Connection) -> None:
    await conn.execute("SELECT pg_advisory_lock($1)", _PLATFORM_ADMIN_POLICY_LOCK_ID)
    try:
        for table_name in _PLATFORM_ADMIN_RLS_TABLES:
            exists = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", table_name)
            if not exists:
                continue
            policy_name = f"p_{table_name}_platform_admin"
            try:
                await conn.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"')
                await conn.execute(
                    f"""
                    CREATE POLICY "{policy_name}" ON "{table_name}"
                    USING (current_setting('app.platform_admin_mode', true) = 'on')
                    WITH CHECK (current_setting('app.platform_admin_mode', true) = 'on')
                    """
                )
            except Exception:
                logger.exception(
                    "Failed to apply platform admin RLS policy",
                    extra={"table_name": table_name, "policy_name": policy_name},
                )
                raise
    finally:
        try:
            await conn.execute("SELECT pg_advisory_unlock($1)", _PLATFORM_ADMIN_POLICY_LOCK_ID)
        except Exception:
            logger.warning("Failed to release platform admin RLS advisory lock")


def _build_init_connection(schema: str, application_name: str):
    async def _init_connection(conn: asyncpg.Connection) -> None:
        for pg_type in ("json", "jsonb"):
            try:
                await conn.set_type_codec(
                    pg_type,
                    encoder=json.dumps,
                    decoder=json.loads,
                    schema="pg_catalog",
                    format="text",
                )
            except Exception:
                pass
        await conn.execute(f'SET search_path TO "{schema}", public')
        if application_name:
            await conn.execute("SELECT set_config('application_name', $1, false)", application_name)

    return _init_connection


class Database:
    def __init__(
        self,
        *,
        url: str | None = None,
        schema: str | None = None,
        application_name: str | None = None,
        min_size: int | None = None,
        max_size: int | None = None,
    ) -> None:
        self._url = url or database_url()
        self._schema = _normalize_schema(schema or db_schema())
        self._application_name = (application_name or service_name()).strip()
        self._min_size = min_size or db_pool_min_size()
        self._max_size = max_size or db_pool_max_size()
        self._connect_timeout = db_connect_timeout_seconds()
        self._command_timeout = db_command_timeout_seconds()
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()
        self._init_connection = _build_init_connection(self._schema, self._application_name)

    async def initialize(self) -> None:
        await self._get_pool()

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        async with self._lock:
            if self._pool is None:
                _check_production_tls()
                logger.info(
                    "Connecting to PostgreSQL for %s using schema %s",
                    self._application_name,
                    self._schema,
                )
                attempts = db_startup_retries()
                base_delay = db_startup_retry_backoff_seconds()
                last_exc: Exception | None = None
                for attempt in range(1, attempts + 1):
                    try:
                        self._pool = await asyncpg.create_pool(
                            dsn=self._url,
                            min_size=self._min_size,
                            max_size=self._max_size,
                            timeout=self._connect_timeout,
                            command_timeout=self._command_timeout,
                            init=self._init_connection,
                        )
                        async with self._pool.acquire() as conn:
                            await _validate_database_role_security(conn, app_name=self._application_name)
                            await _ensure_platform_admin_rls_policies(conn)
                        break
                    except Exception as exc:
                        last_exc = exc
                        if self._pool is not None:
                            try:
                                await self._pool.close()
                            except Exception:
                                logger.debug("Failed to close partially initialized DB pool", exc_info=True)
                            finally:
                                self._pool = None
                        if attempt >= attempts:
                            raise
                        delay_seconds = round(base_delay * attempt, 2)
                        logger.warning(
                            "PostgreSQL connect retry service=%s schema=%s attempt=%s/%s delay_seconds=%s error=%s",
                            self._application_name,
                            self._schema,
                            attempt,
                            attempts,
                            delay_seconds,
                            exc,
                        )
                        await asyncio.sleep(delay_seconds)
                if self._pool is None and last_exc is not None:
                    raise last_exc
        return self._pool

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def fetch(self, query: str, *args):
        conn = _request_conn.get()
        if conn is not None:
            return await conn.fetch(query, *args)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args):
        conn = _request_conn.get()
        if conn is not None:
            return await conn.fetchrow(query, *args)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args):
        conn = _request_conn.get()
        if conn is not None:
            return await conn.fetchval(query, *args)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def execute(self, query: str, *args):
        conn = _request_conn.get()
        if conn is not None:
            return await conn.execute(query, *args)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def executemany(self, query: str, args_list: list):
        conn = _request_conn.get()
        if conn is not None:
            await conn.executemany(query, args_list)
            return
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.executemany(query, args_list)

    async def command(self, cmd: str = "SELECT 1") -> bool:
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.fetchval(cmd)
            return True
        except Exception:
            return False

    def transaction(self) -> "_TxCtx":
        return _TxCtx(self)

    async def bind_request_connection(
        self,
        conn: asyncpg.Connection,
        company_id: Optional[str] = None,
        auth_context: dict[str, Any] | None = None,
    ) -> tuple[contextvars.Token, contextvars.Token]:
        trusted_context = auth_context or {}
        role = str(trusted_context.get("role") or "").strip().lower()
        trusted_company = str(trusted_context.get("company_id") or "").strip()
        company = (company_id or trusted_company or "").strip() or None
        tenant = company or None
        platform_admin_mode = "on" if role == "super_admin" else ""
        conn_token = _request_conn.set(conn)
        company_token = _request_company_id.set(company)
        if company:
            await conn.execute("SELECT set_config('app.current_company', $1, false)", company)
        else:
            await conn.execute("SELECT set_config('app.current_company', '', false)")
        if tenant:
            await conn.execute("SELECT set_config('app.current_tenant', $1, false)", tenant)
        else:
            await conn.execute("SELECT set_config('app.current_tenant', '', false)")
        await conn.execute(
            "SELECT set_config('app.platform_admin_mode', $1, false)",
            platform_admin_mode,
        )
        await conn.execute("SELECT set_config('app.public_auth_mode', '', false)")
        await conn.execute("SELECT set_config('app.auth_email', '', false)")
        await conn.execute("SELECT set_config('app.auth_session_token', '', false)")
        await conn.execute("SELECT set_config('app.auth_token', '', false)")
        return conn_token, company_token

    async def unbind_request_connection(
        self,
        conn: asyncpg.Connection,
        conn_token: contextvars.Token,
        company_token: contextvars.Token,
    ) -> None:
        try:
            await conn.execute("RESET app.current_company")
        except Exception:
            pass
        for setting in (
            "app.current_tenant",
            "app.platform_admin_mode",
            "app.public_auth_mode",
            "app.auth_email",
            "app.auth_session_token",
            "app.auth_token",
        ):
            try:
                await conn.execute(f"RESET {setting}")
            except Exception:
                pass
        _request_conn.reset(conn_token)
        _request_company_id.reset(company_token)


class _TxCtx:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._conn = None
        self._tx = None
        self._pool = None

    async def __aenter__(self) -> asyncpg.Connection:
        self._pool = await self._db._get_pool()
        self._conn = await self._pool.acquire()
        self._tx = self._conn.transaction()
        await self._tx.start()
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type:
                await self._tx.rollback()
            else:
                await self._tx.commit()
        finally:
            if self._pool is not None and self._conn is not None:
                await self._pool.release(self._conn)


def create_database(
    *,
    schema: str | None = None,
    application_name: str | None = None,
    url: str | None = None,
) -> Database:
    return Database(url=url, schema=schema, application_name=application_name)
