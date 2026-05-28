from __future__ import annotations

import json
import logging
import uuid
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from shared.auth.dependencies import extract_bearer_token
from shared.auth.jwt import decode_token, is_valid_company_id
from shared.background_queue import collect_background_queue_snapshot
from shared.config import (
    PUBLIC_GATEWAY_PREFIXES,
    gateway_allowed_headers,
    gateway_allowed_methods,
    gateway_allowed_origins,
    identity_default_api_key,
    identity_default_tenant_id,
    gateway_rate_limit_requests,
    gateway_rate_limit_window_seconds,
    identity_public_tenant_id,
    identity_tenant_api_keys,
    is_production,
    is_origin_allowed,
    rate_limit_redis_url,
    require_secret,
    service_urls,
)
from shared.service_client import build_internal_headers
from shared.tracing import current_trace_context, current_trace_headers, seed_trace_context
from shared.utils.rate_limit import RateLimiter, build_rate_limiter

from shared.json_logging import configure_structured_logging

configure_structured_logging(service_name="api-gateway")
logger = logging.getLogger(__name__)

_HTTP_CLIENT = httpx.AsyncClient(
    timeout=httpx.Timeout(60.0, connect=3.0, read=60.0, write=10.0, pool=5.0),
    limits=httpx.Limits(
        max_keepalive_connections=20,
        max_connections=100,
        keepalive_expiry=30.0,
    ),
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@asynccontextmanager
async def _lifespan(application: FastAPI):
    if is_production():
        require_secret("JWT_SECRET", min_length=32)
        require_secret("INTERNAL_SERVICE_SECRET", min_length=24)
    yield
    await _HTTP_CLIENT.aclose()
    await general_rate_limiter.close()
    await login_rate_limiter.close()
    await webhook_rate_limiter.close()


app = FastAPI(title="Pulse Engine API Gateway", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=gateway_allowed_origins(),
    allow_credentials=True,
    allow_methods=gateway_allowed_methods(),
    allow_headers=gateway_allowed_headers(),
)

urls = service_urls().as_dict()
_rate_limit_redis_url = rate_limit_redis_url()
general_rate_limiter = build_rate_limiter(
    max_requests=gateway_rate_limit_requests(),
    window_seconds=gateway_rate_limit_window_seconds(),
    redis_url=_rate_limit_redis_url,
    key_prefix="pulse:gateway:general",
)
login_rate_limiter = build_rate_limiter(
    max_requests=10,
    window_seconds=60,
    redis_url=_rate_limit_redis_url,
    key_prefix="pulse:gateway:login",
)
webhook_rate_limiter = build_rate_limiter(
    max_requests=30,
    window_seconds=60,
    redis_url=_rate_limit_redis_url,
    key_prefix="pulse:gateway:webhook",
)

ROUTE_TABLE: list[tuple[str, str]] = [
    ("/api/admin/login", "super_admin"),
    ("/api/admin/users", "super_admin"),
    ("/api/admin/logs", "super_admin"),
    ("/api/admin/auth-logs", "super_admin"),
    ("/api/admin/login-sessions", "super_admin"),
    ("/api/auth", "auth"),
    ("/api/account", "auth"),
    ("/api/v1/auth", "identity"),
    ("/api/users", "user"),
    ("/api/platform/super-admin", "super_admin"),
    ("/api/platform", "user"),
    ("/api/settings", "user"),
    ("/api/security", "user"),
    ("/api/system", "user"),
    ("/api/billing", "user"),
    ("/api/customers", "customer"),
    ("/api/conversations", "customer"),
    ("/api/orders", "customer"),
    ("/api/tickets", "customer"),
    ("/api/purchases", "customer"),
    ("/api/knowledge-base", "customer"),
    ("/api/reference-data", "customer"),
    ("/api/search", "customer"),
    ("/api/dashboard", "customer"),
    ("/api/visitor", "customer"),
    ("/api/journey", "customer"),
    ("/api/webhook/meta", "customer"),
    ("/api/webhooks", "customer"),
    ("/api/infrastructure", "customer"),
    ("/api/whatsapp", "customer"),
    ("/api/communications", "customer"),
    ("/api/channels", "customer"),
    ("/api/identity", "identity"),
    ("/api/unification", "identity"),
    ("/api/consent", "identity"),
    ("/api/v1/identity", "identity"),
    ("/api/v1/fingerprint", "identity"),
    ("/api/admin/overview", "super_admin"),
    ("/api/admin/tenants", "super_admin"),
    ("/api/admin/billing", "super_admin"),
    ("/api/admin/usage", "super_admin"),
    ("/api/admin/review-queue", "identity"),
    ("/api/admin/resolve", "identity"),
    ("/api/admin/accuracy-report", "identity"),
    ("/api/leads", "lead"),
    ("/api/campaigns", "email_campaign"),
    ("/api/ai", "ai"),
    ("/api/orchestrator", "orchestrator"),
    ("/api/mcp", "ai"),
    ("/api/social", "ai"),
    ("/api/public", "product"),
    ("/api/products", "product"),
    ("/api/company-data", "product"),
    ("/api/onboarding-docs", "product"),
    ("/api/analytics", "analytics"),
    ("/api/notifications", "notification"),
    ("/api/notification-settings", "notification"),
]
HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "cookie",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
UPSTREAM_RESPONSE_STRIPPED_HEADERS = {"content-type", "date", "server"}
STRIPPED_EXTERNAL_HEADERS = {
    "x-company-id",
    "x-internal-service-secret",
    "x-tenant-id",
    "x-api-key",
    "x-user-id",
    "x-user-role",
}


def _current_trace_id() -> str:
    ctx = current_trace_context()
    return ctx.trace_id if ctx else ""


def _error_body(error: str) -> dict:
    return {"success": False, "error": error, "data": None, "trace_id": _current_trace_id()}


def _apply_security_headers(response: Response) -> Response:
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: blob: https:; frame-ancestors 'none'"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


def _apply_request_id(response: Response, request_id: str) -> Response:
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Correlation-ID"] = request_id
    return response


def _apply_trace_headers(response: Response) -> Response:
    for key, value in current_trace_headers().items():
        response.headers[key] = value
    return response


def _is_public_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix) for prefix in PUBLIC_GATEWAY_PREFIXES)


def _tenant_enrollment_exempt(path: str) -> bool:
    """Allow auth, settings used during onboarding, and other public routes without enrollment checks."""
    if path.startswith("/api/auth"):
        return True
    if path.startswith("/api/settings/company") or path.startswith("/api/settings/personal"):
        return True
    if _is_public_path(path):
        return True
    if path in {"/health", "/ready", "/metrics", "/api/healthz", "/api/health"}:
        return True
    return False


def _tenant_enrollment_block(claims: dict[str, Any]) -> str | None:
    """Returns API detail string if request should be blocked, else None. super_admin bypasses."""
    role = str(claims.get("role") or "").strip().lower()
    if role == "super_admin":
        return None
    try:
        ev = int(claims.get("ev", 1))
    except (TypeError, ValueError):
        ev = 1
    if ev == 0:
        return "EMAIL_VERIFICATION_REQUIRED"
    try:
        ei = int(claims.get("ei", 1))
    except (TypeError, ValueError):
        ei = 1
    if ei == 0:
        return "ENTERPRISE_INVITE_REQUIRED"
    if "ob" not in claims:
        return None
    try:
        ob = int(claims.get("ob", 0))
        pl = int(claims.get("pl", 0))
    except (TypeError, ValueError):
        return None
    if ob == 0:
        return "ONBOARDING_REQUIRED"
    if pl == 0:
        return "PLAN_SELECTION_REQUIRED"
    return None


def _resolve_service(path: str) -> tuple[str | None, str | None]:
    for prefix, service_key in ROUTE_TABLE:
        if path == prefix or path.startswith(f"{prefix}/"):
            return urls[service_key], service_key
    return None, None


def _unauthorized_response(status_code: int = 401) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=_error_body("Authentication failed"))


def _too_many_requests_response(retry_after: int) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content=_error_body("Too Many Requests"),
        headers={"Retry-After": str(retry_after)},
    )


def _validate_claims(path: str, token: str) -> dict[str, Any]:
    if _is_public_path(path):
        if not token:
            return {}
        claims = decode_token(token)
        if not claims or not claims.get("sub"):
            return {}
        company_id = str(claims.get("company_id") or "").strip()
        if claims.get("role") != "super_admin" and (not company_id or not is_valid_company_id(company_id)):
            return {}
        return claims
    if not token:
        raise ValueError("Unauthorized")
    claims = decode_token(token)
    if not claims or not claims.get("sub"):
        raise ValueError("Unauthorized")
    company_id = str(claims.get("company_id") or "").strip()
    if claims.get("role") != "super_admin" and (not company_id or not is_valid_company_id(company_id)):
        raise ValueError("Unauthorized")
    return claims


def _select_rate_limiter(path: str) -> RateLimiter:
    if path in {
        "/api/admin/login",
        "/api/auth/login",
        "/api/auth/register",
        "/api/auth/refresh",
        "/api/auth/forgot-password",
        "/api/auth/reset-password",
    }:
        return login_rate_limiter
    if path.startswith("/api/webhooks/") or path.startswith("/api/webhook/meta/"):
        return webhook_rate_limiter
    return general_rate_limiter


def _rate_limit_subject(claims: dict[str, Any]) -> str:
    role = str(claims.get("role") or "").strip().lower()
    company_id = str(claims.get("company_id") or "").strip()
    if role == "super_admin":
        return "super_admin"
    if company_id:
        return company_id
    return "public"


def _is_public_unification_path(path: str) -> bool:
    return path == "/api/unification/public" or path.startswith("/api/unification/public/")


def _resolve_identity_tenant_id(request: Request, claims: dict[str, Any], full_path: str = "") -> str:
    if _is_public_unification_path(full_path):
        return identity_public_tenant_id()

    tenant_keys = identity_tenant_api_keys()
    default_tenant = identity_default_tenant_id("demo_tenant") or "demo_tenant"
    claim_tenant = str(claims.get("company_id") or "").strip()
    requested_tenant = str(request.headers.get("X-Tenant-ID") or "").strip()
    role = str(claims.get("role") or "").strip().lower()

    if role == "super_admin":
        tenant_id = requested_tenant or claim_tenant or default_tenant
        if not requested_tenant and not claim_tenant:
            logger.warning(
                "identity tenant fallback applied for super_admin tenant_id=%s",
                tenant_id,
            )
    else:
        if requested_tenant and claim_tenant and requested_tenant != claim_tenant:
            logger.info("identity tenant header ignored in favor of logged-in company_id=%s", claim_tenant)
        if claim_tenant:
            tenant_id = claim_tenant
        elif requested_tenant:
            tenant_id = requested_tenant
        else:
            tenant_id = default_tenant

    if tenant_id not in tenant_keys:
        logger.info("identity tenant mapping missing; forwarding tenant_id=%s without backend auth check", tenant_id)
    return tenant_id


def _build_preflight_response(request: Request) -> Response:
    origin = request.headers.get("origin", "").strip()
    requested_method = request.headers.get("access-control-request-method", "").strip().upper()
    requested_headers = request.headers.get("access-control-request-headers", "").strip()
    if origin and not is_origin_allowed(origin):
        return _apply_security_headers(JSONResponse(status_code=403, content={"error": "Forbidden"}))
    if not origin:
        return _apply_security_headers(Response(status_code=200))
    return _apply_security_headers(
        Response(
            status_code=200,
            headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Methods": requested_method or "*",
                "Access-Control-Allow-Headers": requested_headers or "*",
                "Access-Control-Max-Age": "600",
                "Vary": "Origin",
            },
        )
    )


def _sanitize_forward_headers(request: Request, full_path: str) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    allow_cookie = full_path.startswith("/api/auth") or full_path.startswith("/api/account")
    for key, value in request.headers.items():
        lowered = key.lower()
        if lowered == "cookie" and allow_cookie:
            sanitized[key] = value
            continue
        if lowered in HOP_BY_HOP_HEADERS or lowered in STRIPPED_EXTERNAL_HEADERS or lowered.startswith("x-forwarded-"):
            continue
        sanitized["Authorization" if lowered == "authorization" else key] = value
    return sanitized


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code in {401, 403}:
        err = "Authentication failed"
        if isinstance(exc.detail, str) and exc.detail in (
            "EMAIL_VERIFICATION_REQUIRED",
            "ENTERPRISE_INVITE_REQUIRED",
        ):
            err = exc.detail
        elif isinstance(exc.detail, str) and (
            exc.detail.startswith("Your account is ")
            or exc.detail.startswith("Session has been revoked")
        ):
            err = exc.detail
        return _apply_security_headers(JSONResponse(status_code=exc.status_code, content=_error_body(err)))
    if exc.status_code == 429:
        return _apply_security_headers(JSONResponse(status_code=429, content=_error_body("Too Many Requests")))
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return _apply_security_headers(JSONResponse(status_code=exc.status_code, content=_error_body(detail)))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("gateway unhandled_error path=%s", request.url.path)
    return _apply_security_headers(JSONResponse(status_code=500, content=_error_body("Internal Server Error")))


@app.middleware("http")
async def gateway_middleware(request: Request, call_next):
    started = time.perf_counter()
    path = request.url.path
    has_auth = bool(extract_bearer_token(request))
    request_id = (
        request.headers.get("x-request-id") or request.headers.get("x-correlation-id") or str(uuid.uuid4())
    ).strip() or str(uuid.uuid4())
    trace_context = seed_trace_context(request.headers, request_id=request_id)
    request.state.request_id = request_id
    request.state.trace_context = trace_context
    _, target_service = _resolve_service(path)
    service_name = target_service or "gateway"

    if request.method.upper() == "OPTIONS":
        response = _build_preflight_response(request)
        logger.info(
            "gateway method=%s path=%s service=%s has_auth=%s request_id=%s trace_id=%s span_id=%s status=%s duration_ms=%s",  # noqa: E501
            request.method,
            path,
            service_name,
            has_auth,
            request_id,
            trace_context.trace_id,
            trace_context.span_id,
            response.status_code,
            round((time.perf_counter() - started) * 1000, 2),
        )
        return _apply_trace_headers(_apply_request_id(response, request_id))

    token = extract_bearer_token(request)
    try:
        request.state.claims = _validate_claims(path, token)
    except ValueError:
        return _apply_trace_headers(_apply_request_id(_apply_security_headers(_unauthorized_response()), request_id))

    claims = getattr(request.state, "claims", {}) or {}
    client_ip = request.client.host if request.client else "unknown"
    rate_limit_subject = _rate_limit_subject(claims)
    rate_limit_key = f"{rate_limit_subject}:{client_ip}:{path}"
    allowed, retry_after = await _select_rate_limiter(path).allow(rate_limit_key)
    if not allowed:
        return _apply_trace_headers(
            _apply_request_id(_apply_security_headers(_too_many_requests_response(retry_after)), request_id)
        )

    if has_auth and claims.get("sub") and not _tenant_enrollment_exempt(path):
        _enroll = _tenant_enrollment_block(claims)
        if _enroll:
            return _apply_trace_headers(
                _apply_request_id(
                    _apply_security_headers(
                        JSONResponse(status_code=403, content={"detail": _enroll, "error": _enroll, "success": False})
                    ),
                    request_id,
                )
            )

    # ── Gateway-level billing pre-flight (cache-only, no DB) ────────────────
    # Reads billing:{tenant_id} from Redis (populated by downstream services).
    # On cache miss the check is skipped — downstream service enforces via DB.
    # Bypass: super_admin, auth routes, billing management routes, health.
    _GATEWAY_BILLING_EXEMPT = (
        "/api/auth",
        "/api/account",
        "/api/billing/webhooks",
        "/api/billing/checkout-session",
        "/api/billing/customer-portal",
        "/api/billing/plans",
        "/api/billing/subscription",
        "/api/admin",
        "/api/platform/super-admin",
        "/health",
        "/ready",
        "/metrics",
    )
    _billing_exempt = any(path == p or path.startswith(p) for p in _GATEWAY_BILLING_EXEMPT)
    _gateway_role = str(claims.get("role") or "").strip().lower()
    _gateway_company = str(claims.get("company_id") or "").strip()

    if not _billing_exempt and _gateway_company and _gateway_role != "super_admin":
        try:
            from shared.billing_cache import get_cached_billing, is_token_blacklisted
            import time as _time

            # Token blacklist check
            _jti = str(claims.get("jti") or "").strip()
            if _jti and await is_token_blacklisted(_jti):
                return _apply_trace_headers(
                    _apply_request_id(
                        _apply_security_headers(
                            JSONResponse(
                                status_code=401, content=_error_body("Session has been revoked. Please log in again.")
                            )
                        ),
                        request_id,
                    )
                )

            # Skip subscription cache enforcement when Stripe is off or DEMO_MODE / STRIPE_OPTIONAL.
            _enforce_subscription_cache = True
            _offline_trial_enf = False
            try:
                from services.billing_helpers import (
                    relaxed_billing_env as _relaxed_billing,
                    stripe_configured as _stripe_cfg,
                    stripe_enabled_for_app as _stripe_app,
                )

                _enforce_subscription_cache = bool(_stripe_cfg()) and not bool(_relaxed_billing())
                _offline_trial_enf = not bool(_relaxed_billing()) and not bool(_stripe_app())
            except Exception:
                _enforce_subscription_cache = False
                _offline_trial_enf = False

            # Billing status check (cache-only — skip if no cached record)
            _billing = await get_cached_billing(_gateway_company)
            if _billing is not None and _offline_trial_enf:
                _plan_ot = str(_billing.get("plan_code") or "free").lower()
                _end_ts = _billing.get("current_period_end_ts")
                if (
                    _plan_ot != "free"
                    and _end_ts is not None
                    and _time.time() > float(_end_ts)
                ):
                    return _apply_trace_headers(
                        _apply_request_id(
                            _apply_security_headers(
                                JSONResponse(
                                    status_code=402,
                                    content=_error_body("Trial period ended. Please contact your administrator."),
                                )
                            ),
                            request_id,
                        )
                    )

            if _enforce_subscription_cache and _billing is not None:
                if _billing.get("is_active") is False:
                    return _apply_trace_headers(
                        _apply_request_id(
                            _apply_security_headers(
                                JSONResponse(status_code=403, content=_error_body("This workspace has been disabled."))
                            ),
                            request_id,
                        )
                    )
                _sub_status = str(_billing.get("subscription_status") or "active").lower()
                _pay_status = str(_billing.get("payment_status") or "inactive").lower()
                _plan = str(_billing.get("plan_code") or "free").lower()
                _grace = _billing.get("grace_until")
                _blocked = {"canceled", "unpaid", "past_due", "incomplete"}
                if _plan != "free" and _sub_status in _blocked:
                    if not _grace or _time.time() >= float(_grace):
                        return _apply_trace_headers(
                            _apply_request_id(
                                _apply_security_headers(
                                    JSONResponse(
                                        status_code=402, content=_error_body("Subscription inactive. Please renew.")
                                    )
                                ),
                                request_id,
                            )
                        )
                if _plan != "free" and _sub_status in {"active", "trialing"} and _pay_status == "failed":
                    return _apply_trace_headers(
                        _apply_request_id(
                            _apply_security_headers(
                                JSONResponse(
                                    status_code=402,
                                    content=_error_body("Payment failed. Please update billing details."),
                                )
                            ),
                            request_id,
                        )
                    )
        except Exception as _gbe:
            logger.warning("gateway billing pre-flight error (non-fatal): %s", _gbe)
    # ─────────────────────────────────────────────────────────────────────────

    response = await call_next(request)
    response = _apply_security_headers(response)
    response = _apply_request_id(response, request_id)
    response = _apply_trace_headers(response)
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    logger.info(
        "gateway method=%s path=%s service=%s has_auth=%s request_id=%s trace_id=%s span_id=%s status=%s duration_ms=%s",  # noqa: E501
        request.method,
        path,
        service_name,
        has_auth,
        request_id,
        trace_context.trace_id,
        trace_context.span_id,
        response.status_code,
        duration_ms,
    )
    return response


@app.get("/health")
async def health() -> dict[str, Any]:
    statuses: dict[str, Any] = {}
    for service_name, base_url in urls.items():
        service_status = "error"
        last_status_code: int | None = None
        for health_path in ("/health", "/api/health"):
            try:
                response = await _HTTP_CLIENT.get(f"{base_url}{health_path}", timeout=5.0)
                last_status_code = response.status_code
                if response.status_code == 200:
                    service_status = "ok"
                    break
            except Exception:
                continue
        statuses[service_name] = {"status": service_status}
        if last_status_code is not None:
            statuses[service_name]["status_code"] = last_status_code
    return {"service": "api-gateway", "services": statuses}


@app.get("/api/healthz")
async def healthz() -> dict[str, Any]:
    return {"status": "ok", "service": "api-gateway"}


@app.get("/api/system/queue-status")
async def queue_status(request: Request) -> dict[str, Any]:
    claims = getattr(request.state, "claims", {}) or {}
    if claims.get("role") not in {"admin", "super_admin"}:
        raise HTTPException(status_code=403, detail="Forbidden")
    return await collect_background_queue_snapshot()


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy(path: str, request: Request):
    full_path = f"/{path}"
    target, service_name = _resolve_service(full_path)
    if not target:
        return _apply_security_headers(JSONResponse(status_code=404, content=_error_body("Not Found")))

    claims = getattr(request.state, "claims", {}) or {}
    forwarded_headers = _sanitize_forward_headers(request, full_path)
    is_meta_webhook_path = full_path.startswith("/api/webhook/meta/")
    body = await request.body()

    # Webhook requests are often unauthenticated; infer tenant from payload when possible.
    inferred_company_id = ""
    if not str(claims.get("company_id") or "").strip() and full_path.startswith("/api/webhooks/"):
        try:
            payload = json.loads(body or b"{}")
            if isinstance(payload, dict):
                inferred_company_id = str(
                    payload.get("company_id") or payload.get("tenant_id") or payload.get("companyId") or ""
                ).strip()
        except Exception:
            inferred_company_id = ""

    if not is_meta_webhook_path:
        forwarded_headers.update(
            build_internal_headers(
                authorization=request.headers.get("authorization") or request.headers.get("Authorization", ""),
                company_id=str(claims.get("company_id") or inferred_company_id or ""),
                user_id=str(claims.get("sub") or ""),
                user_role=str(claims.get("role") or ""),
            )
        )
    if service_name == "identity":
        tenant_keys = identity_tenant_api_keys()
        tenant_id = _resolve_identity_tenant_id(request, claims, full_path)
        forwarded_headers["X-Tenant-ID"] = tenant_id
        tenant_api_key = tenant_keys.get(tenant_id) or identity_default_api_key("demo-identity-key")
        if tenant_api_key:
            forwarded_headers["X-API-Key"] = tenant_api_key
        elif not _is_public_unification_path(full_path):
            logger.warning("identity api key is missing for tenant_id=%s path=%s", tenant_id, full_path)
    request_id = str(getattr(request.state, "request_id", "") or "").strip()
    if request_id:
        forwarded_headers["X-Request-ID"] = request_id
        forwarded_headers["X-Correlation-ID"] = request_id
    forwarded_headers["X-Forwarded-Host"] = request.headers.get("host", "")
    forwarded_headers["X-Forwarded-Proto"] = request.url.scheme
    forwarded_headers["X-Forwarded-For"] = request.client.host if request.client else ""

    try:
        if full_path.endswith("/stream") or "text/event-stream" in request.headers.get("accept", "").lower():
            upstream_request = _HTTP_CLIENT.build_request(
                method=request.method,
                url=f"{target}{full_path}",
                params=list(request.query_params.multi_items()),
                content=body,
                headers=forwarded_headers,
            )
            upstream = await _HTTP_CLIENT.send(upstream_request, stream=True)
        else:
            upstream = await _HTTP_CLIENT.request(
                method=request.method,
                url=f"{target}{full_path}",
                params=list(request.query_params.multi_items()),
                content=body,
                headers=forwarded_headers,
                timeout=60.0,
            )
    except httpx.HTTPError as exc:
        logger.error(
            "gateway upstream error service=%s path=%s target=%s error=%s detail=%s",
            service_name,
            full_path,
            target,
            exc.__class__.__name__,
            str(exc) or repr(exc),
        )
        return _apply_security_headers(JSONResponse(status_code=502, content=_error_body("Bad Gateway")))

    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() not in UPSTREAM_RESPONSE_STRIPPED_HEADERS
    }
    if full_path.endswith("/stream") or "text/event-stream" in upstream.headers.get("content-type", "").lower():
        async def _stream_upstream():
            try:
                async for chunk in upstream.aiter_bytes():
                    if chunk:
                        yield chunk
            except Exception as exc:
                logger.error(
                    "gateway upstream stream error service=%s path=%s target=%s error=%s detail=%s",
                    service_name,
                    full_path,
                    target,
                    exc.__class__.__name__,
                    str(exc) or repr(exc),
                )
                raise
            finally:
                await upstream.aclose()

        return _apply_security_headers(
            StreamingResponse(
                _stream_upstream(),
                status_code=upstream.status_code,
                headers=response_headers,
                media_type=upstream.headers.get("content-type") or "text/event-stream",
            )
        )
    return _apply_security_headers(
        Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=upstream.headers.get("content-type"),
        )
    )
