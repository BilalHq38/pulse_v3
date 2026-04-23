from __future__ import annotations

import logging
import os
import pwd
import shutil
import socket
import subprocess
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware

from routers.ai import router as ai_router
from routers.analytics import router as analytics_router
from routers.auth import router as auth_router
from routers.billing import router as billing_router
from routers.conversations import router as conversations_router
from routers.customers import router as customers_router
from routers.leads import router as leads_router
from routers.meta import router as meta_router
from routers.misc import misc_router, notifications_router, security_router
from routers.products import router as products_router
from routers.settings import router as settings_router
from routers.tickets import router as tickets_router
from routers.users import router as users_router
from routers.webhooks import router as webhooks_router
from services.email_campaign_service.routes import router as campaigns_router
from services.identity_service.app.server import app as identity_service_app
from services.bootstrap import (
    bootstrap_ai_runtime,
    bootstrap_auth_security,
    bootstrap_data_pipeline,
    bootstrap_demo_accounts,
    bootstrap_roles,
    bootstrap_signup_primitives,
    bootstrap_super_admin,
)
from services.super_admin_service.routes import router as super_admin_router
from shared.app_factory import create_service_app

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=False)

_SCHEMA_BOOTSTRAPPED = False
_SCHEMA_BOOTSTRAP_LOCK = Lock()
_EMBEDDED_PG = None


def _is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        try:
            sock.connect((host, port))
        except OSError:
            return False
    return True


def _start_embedded_postgres_if_needed() -> None:
    global _EMBEDDED_PG

    if (os.environ.get("PG_EMBEDDED_ENABLED") or "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return

    database_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not database_url or _EMBEDDED_PG is not None:
        return

    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgresql", "postgres"}:
        return

    host = (parsed.hostname or "").strip().lower()
    port = int(parsed.port or 5432)
    username = parsed.username or "postgres"
    password = parsed.password or "postgres"
    database = (parsed.path or "/postgres").lstrip("/") or "postgres"

    if host not in {"127.0.0.1", "localhost"} or _is_port_open("127.0.0.1", port):
        return

    try:
        import pg0 as pg0_module

        if os.geteuid() == 0:
            nobody = pwd.getpwnam("nobody")
            pg_home = Path("/tmp/pulse-engine-pg0-home")
            data_dir = Path("/tmp/pulse-engine-pg0-data")
            pg_bin_dir = Path("/tmp/pulse-engine-pg0-bin")
            for path in (pg_home, data_dir, pg_bin_dir):
                path.mkdir(parents=True, exist_ok=True)
                os.chown(path, nobody.pw_uid, nobody.pw_gid)

            bundled_pg0 = Path(pg0_module._get_bundled_binary() or "")
            copied_pg0 = pg_bin_dir / "pg0"
            if bundled_pg0 and bundled_pg0.exists():
                shutil.copy2(bundled_pg0, copied_pg0)
                copied_pg0.chmod(0o755)
                os.chown(copied_pg0, nobody.pw_uid, nobody.pw_gid)
                pg0_module._find_pg0 = lambda: str(copied_pg0)

            original_run = subprocess.run

            def _run_pg0_as_nobody(*args, **kwargs):
                env = dict(os.environ)
                env.update(kwargs.pop("env", {}) or {})
                env["HOME"] = str(pg_home)

                def _drop_privileges() -> None:
                    os.setgid(nobody.pw_gid)
                    os.setuid(nobody.pw_uid)

                return original_run(*args, env=env, preexec_fn=_drop_privileges, **kwargs)

            pg0_module.subprocess.run = _run_pg0_as_nobody
            embedded_data_dir = str(data_dir)
        else:
            embedded_data_dir = str(PROJECT_ROOT / ".pg0-data")

        Pg0 = pg0_module.Pg0

        logger.info("Starting embedded PostgreSQL on %s:%s for local runtime", host, port)
        _EMBEDDED_PG = Pg0(
            name="pulse-engine",
            port=port,
            username=username,
            password=password,
            database=database,
            data_dir=embedded_data_dir,
            config={"shared_buffers": "128MB", "fsync": "off"},
        )
        _EMBEDDED_PG.start()
    except Exception as exc:  # pragma: no cover - best effort local bootstrap
        logger.exception("Embedded PostgreSQL bootstrap failed: %s", exc)
        raise


async def bootstrap_sql_schema(db) -> None:
    global _SCHEMA_BOOTSTRAPPED
    if _SCHEMA_BOOTSTRAPPED:
        return

    schema_path = PROJECT_ROOT / "backend" / "sql_schema.sql"
    with _SCHEMA_BOOTSTRAP_LOCK:
        if _SCHEMA_BOOTSTRAPPED:
            return
        sql = schema_path.read_text(encoding="utf-8")

    pool = await db._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(sql)

    _SCHEMA_BOOTSTRAPPED = True
    logger.info("Primary PostgreSQL schema loaded from %s", schema_path)


def _allowed_origins() -> list[str]:
    origins: list[str] = []
    raw_values = [
        os.environ.get("CORS_ORIGINS", ""),
        os.environ.get("ALLOWED_ORIGINS", ""),
        os.environ.get("FRONTEND_URL", ""),
        os.environ.get("APP_URL", ""),
    ]
    for raw in raw_values:
        for origin in raw.split(","):
            cleaned = origin.strip().rstrip("/")
            if cleaned and cleaned not in origins:
                origins.append(cleaned)
    for fallback in ("http://localhost:3000", "http://127.0.0.1:3000"):
        if fallback not in origins:
            origins.append(fallback)
    return origins


_start_embedded_postgres_if_needed()

app = create_service_app(
    service_name="pulse-engine",
    title="Pulse Engine API",
    routers=(
        auth_router,
        users_router,
        customers_router,
        conversations_router,
        tickets_router,
        leads_router,
        settings_router,
        products_router,
        billing_router,
        analytics_router,
        ai_router,
        campaigns_router,
        meta_router,
        webhooks_router,
        super_admin_router,
        security_router,
        notifications_router,
        misc_router,
    ),
    db_schema="public",
    startup_tasks=(
        bootstrap_sql_schema,
        bootstrap_roles,
        bootstrap_auth_security,
        bootstrap_signup_primitives,
        bootstrap_super_admin,
        bootstrap_demo_accounts,
        bootstrap_ai_runtime,
        bootstrap_data_pipeline,
    ),
)
app.state.allow_direct_jwt_auth = True
app.state.require_internal_service_secret = False
identity_service_app.state.service_name = "identity-service"
app.mount("/", identity_service_app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/healthz")
async def api_healthz() -> dict[str, str]:
    return {"status": "ok", "service": "pulse-engine"}


@app.on_event("shutdown")
async def stop_embedded_postgres() -> None:
    global _EMBEDDED_PG
    if _EMBEDDED_PG is None:
        return
    try:
        _EMBEDDED_PG.stop()
    except Exception:
        logger.warning("Embedded PostgreSQL shutdown reported an error", exc_info=True)
    finally:
        _EMBEDDED_PG = None