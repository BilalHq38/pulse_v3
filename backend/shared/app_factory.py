from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from shared.json_logging import configure_structured_logging

from shared.background_queue import (
    collect_background_queue_snapshot,
    start_background_queue_worker,
    stop_background_queue_worker,
)
from shared.cache import close_cache_clients
from core.socket import close_socket_resources, set_socket_db
from shared.metrics import increment_counter, observe_histogram, snapshot_metrics
from shared.auth.dependencies import (
    build_trusted_context_from_request,
    extract_bearer_token,
    extract_company_id_from_request,
    is_trusted_service_request,
)
from shared.database import Database, create_database
from shared.config import is_production, require_secret
from shared.tracing import current_trace_headers, seed_trace_context

logger = logging.getLogger(__name__)


def _apply_security_headers(response: Response) -> Response:
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def _apply_request_id(response: Response, request_id: str) -> Response:
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Correlation-ID"] = request_id
    return response


def _apply_trace_headers(response: Response) -> Response:
    for key, value in current_trace_headers().items():
        response.headers[key] = value
    return response


def _register_security_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        from shared.tracing import current_trace_context

        increment_counter(
            "http.exceptions.total",
            labels={
                "service": request.app.state.service_name,
                "status": exc.status_code,
            },
        )
        trace = current_trace_context()
        trace_id = trace.trace_id if trace else ""
        if exc.status_code in {401, 403}:
            err = "Authentication failed"
            if isinstance(exc.detail, str) and exc.detail in (
                "EMAIL_VERIFICATION_REQUIRED",
                "ENTERPRISE_INVITE_REQUIRED",
            ):
                err = exc.detail
            return _apply_security_headers(
                JSONResponse(
                    status_code=exc.status_code,
                    content={
                        "success": False,
                        "error": err,
                        "detail": err,
                        "data": None,
                        "trace_id": trace_id,
                    },
                )
            )
        if exc.status_code == 429:
            return _apply_security_headers(
                JSONResponse(
                    status_code=429,
                    content={"success": False, "error": "Too Many Requests", "data": None, "trace_id": trace_id},
                )
            )
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return _apply_security_headers(
            JSONResponse(
                status_code=exc.status_code,
                content={"success": False, "error": detail, "data": None, "trace_id": trace_id},
            )
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        from shared.tracing import current_trace_context

        increment_counter(
            "http.exceptions.unhandled",
            labels={"service": request.app.state.service_name},
        )
        logger.exception("service=%s unhandled_error path=%s", request.app.state.service_name, request.url.path)
        trace = current_trace_context()
        trace_id = trace.trace_id if trace else ""
        return _apply_security_headers(
            JSONResponse(
                status_code=500,
                content={"success": False, "error": "Internal Server Error", "data": None, "trace_id": trace_id},
            )
        )


def create_service_app(
    *,
    service_name: str,
    title: str,
    routers: Iterable[APIRouter] = (),
    db_schema: str,
    startup_tasks: Iterable[Callable[[Database], Awaitable[None]]] = (),
) -> FastAPI:
    configure_structured_logging(service_name=service_name)
    os.environ["SERVICE_NAME"] = service_name
    app = FastAPI(title=title)
    db = create_database(schema=db_schema, application_name=service_name)
    app.state.db = db
    app.state.service_name = service_name
    app.state.allow_direct_jwt_auth = False
    app.state.require_internal_service_secret = True

    _register_security_handlers(app)

    @app.middleware("http")
    async def service_request_middleware(request: Request, call_next):
        started = time.perf_counter()
        has_auth = bool(extract_bearer_token(request))
        request_id = (
            request.headers.get("x-request-id") or request.headers.get("x-correlation-id") or str(uuid.uuid4())
        ).strip() or str(uuid.uuid4())
        trace_context = seed_trace_context(request.headers, request_id=request_id)
        request.state.request_id = request_id
        request.state.trace_context = trace_context
        response: Response
        try:
            if request.method.upper() == "OPTIONS":
                response = Response(status_code=200)
            elif not is_trusted_service_request(request):
                response = JSONResponse(
                    status_code=401,
                    content={"error": "Authentication failed"},
                )
            else:
                trusted = build_trusted_context_from_request(request)
                if trusted is not None:
                    request.state.auth_context = trusted

                if request.url.path in {
                    "/health",
                    "/api/healthz",
                    "/metrics",
                    "/api/metrics",
                } or request.url.path.startswith("/health/"):
                    response = await call_next(request)
                else:
                    pool = await db._get_pool()
                    async with pool.acquire() as conn:
                        company_id = extract_company_id_from_request(request)
                        auth_context = getattr(request.state, "auth_context", None)
                        conn_token, company_token = await db.bind_request_connection(
                            conn,
                            company_id,
                            auth_context=auth_context,
                        )
                        try:
                            # ── Global billing enforcement ──────────────────
                            # Runs cache-first (Redis → DB) on every request
                            # that carries a company context. Exempt paths
                            # (auth, billing webhooks, health) are skipped
                            # inside enforce_billing itself.
                            try:
                                from shared.billing_guard import enforce_access_gates, enforce_billing

                                await enforce_access_gates(request)
                                await enforce_billing(request)
                            except Exception as billing_exc:
                                raise billing_exc
                            # ───────────────────────────────────────────────
                            response = await call_next(request)
                        finally:
                            await db.unbind_request_connection(conn, conn_token, company_token)
        except HTTPException:
            raise
        response = _apply_security_headers(response)
        response = _apply_request_id(response, request_id)
        response = _apply_trace_headers(response)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        status_class = f"{response.status_code // 100}xx"
        metric_labels = {
            "service": service_name,
            "method": request.method.upper(),
            "path": request.url.path,
            "status": response.status_code,
            "status_class": status_class,
        }
        increment_counter("http.requests.total", labels=metric_labels)
        observe_histogram("http.request.duration_ms", duration_ms, labels=metric_labels)
        logger.info(
            "service=%s method=%s path=%s has_auth=%s request_id=%s trace_id=%s span_id=%s status=%s duration_ms=%s",
            service_name,
            request.method,
            request.url.path,
            has_auth,
            request_id,
            trace_context.trace_id,
            trace_context.span_id,
            response.status_code,
            duration_ms,
        )
        return response

    @app.on_event("startup")
    async def startup() -> None:
        if is_production():
            require_secret("JWT_SECRET", min_length=32)
            require_secret("INTERNAL_SERVICE_SECRET", min_length=24)
        logger.info("Starting %s", service_name)
        await db.initialize()
        set_socket_db(db)
        for task in startup_tasks:
            await task(db)
        try:
            app.state.background_queue_handle = await start_background_queue_worker(db, service_label=service_name)
        except Exception as exc:
            logger.warning("background queue startup skipped for %s: %s", service_name, exc)
            app.state.background_queue_handle = None

    @app.on_event("shutdown")
    async def shutdown() -> None:
        await stop_background_queue_worker(getattr(app.state, "background_queue_handle", None))
        set_socket_db(None)
        await close_socket_resources()
        await close_cache_clients()
        await db.close()

    @app.get("/health")
    async def health() -> dict:
        return {
            "service": service_name,
            "database": "ok" if await db.command() else "error",
        }

    @app.get("/ready")
    async def readiness() -> dict:
        """Deep readiness check — verifies DB and Redis connectivity."""
        checks: dict[str, str] = {}
        checks["database"] = "ok" if await db.command() else "error"
        try:
            from shared.cache import get_cache_client

            cache = get_cache_client(namespace="readiness")
            if cache.using_redis:
                await cache.set_json("__ready_probe__", {"ts": 1}, ttl_seconds=5)
                checks["redis"] = "ok"
            else:
                checks["redis"] = "fallback_memory"
        except Exception:
            checks["redis"] = "error"
        is_ready = checks["database"] == "ok"
        return {
            "service": service_name,
            "ready": is_ready,
            "checks": checks,
        }

    @app.get("/metrics")
    async def metrics() -> dict:
        return {
            "service": service_name,
            "metrics": snapshot_metrics(),
            "background_queue": await collect_background_queue_snapshot(),
        }

    for router in routers:
        app.include_router(router, prefix="/api")

    return app
