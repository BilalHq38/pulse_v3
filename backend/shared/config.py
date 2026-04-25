from __future__ import annotations

import functools
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)

DEFAULT_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://qa-finalize.preview.emergentagent.com",
]
LOCAL_FRONTEND_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

_WEAK_SECRET_MARKERS = {
    "change-me",
    "change-me-too",
    "fallback-secret-change-me",
    "identity-service-jwt-secret",
    "demo-identity-key",
    "demo-key",
    "identityadmin!2026",
    "welcome@123",
    "pulse_engine_verify",
}

DEFAULT_PUBLIC_TENANT = "public_unification"

PUBLIC_GATEWAY_PREFIXES = (
    "/health",
    "/api/health",
    "/api/healthz",
    "/api/admin/login",
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/refresh",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
    "/api/auth/verify-email",
    "/api/auth/resend-verification",
    "/api/auth/google",
    "/api/auth/google/callback",
    "/api/auth/facebook",
    "/api/auth/facebook/callback",
    "/api/auth/session",
    "/api/auth/invitations/accept",
    "/api/auth/register/status",
    "/api/auth/signup-billing-info",
    "/api/unification/public",
    "/api/billing/webhooks/stripe",
    "/api/webhook/meta/",
    "/api/webhooks/web-chat",
    "/api/webhooks/external/purchases",
)


@dataclass(frozen=True)
class ServiceUrls:
    auth: str
    user: str
    customer: str
    lead: str
    email_campaign: str
    ai: str
    orchestrator: str
    product: str
    analytics: str
    data_pipeline: str
    notification: str
    identity: str
    super_admin: str

    def as_dict(self) -> dict[str, str]:
        return {
            "auth": self.auth,
            "user": self.user,
            "customer": self.customer,
            "lead": self.lead,
            "email_campaign": self.email_campaign,
            "ai": self.ai,
            "orchestrator": self.orchestrator,
            "product": self.product,
            "analytics": self.analytics,
            "data_pipeline": self.data_pipeline,
            "notification": self.notification,
            "identity": self.identity,
            "super_admin": self.super_admin,
        }


def service_urls() -> ServiceUrls:
    return ServiceUrls(
        auth=os.environ.get("AUTH_SERVICE_URL", "http://auth:8001").rstrip("/"),
        user=os.environ.get("USER_SERVICE_URL", "http://user:8002").rstrip("/"),
        customer=os.environ.get("CUSTOMER_SERVICE_URL", "http://customer:8003").rstrip("/"),
        lead=os.environ.get("LEAD_SERVICE_URL", "http://lead:8004").rstrip("/"),
        email_campaign=os.environ.get("EMAIL_CAMPAIGN_SERVICE_URL", "http://email-campaign:8013").rstrip("/"),
        ai=os.environ.get("AI_SERVICE_URL", "http://ai:8005").rstrip("/"),
        orchestrator=os.environ.get("ORCHESTRATOR_SERVICE_URL", "http://agent-orchestrator:8009").rstrip("/"),
        analytics=os.environ.get("ANALYTICS_SERVICE_URL", "http://analytics:8006").rstrip("/"),
        product=os.environ.get("PRODUCT_SERVICE_URL", "http://product:8007").rstrip("/"),
        data_pipeline=os.environ.get("DATA_PIPELINE_SERVICE_URL", "http://data-pipeline:8012").rstrip("/"),
        notification=os.environ.get("NOTIFICATION_SERVICE_URL", "http://notification:8008").rstrip("/"),
        identity=os.environ.get("IDENTITY_SERVICE_URL", "http://identity:8010").rstrip("/"),
        super_admin=os.environ.get("SUPER_ADMIN_SERVICE_URL", "http://super-admin:8011").rstrip("/"),
    )


def identity_default_tenant_id(default: str = "demo_tenant") -> str:
    configured = os.environ.get("DEFAULT_TENANT_ID", default)
    return (configured or "").strip()


def identity_default_api_key(default: str = "demo-identity-key") -> str:
    configured = os.environ.get("DEFAULT_TENANT_API_KEY", default)
    return (configured or "").strip()


def identity_public_tenant_id(default: str = DEFAULT_PUBLIC_TENANT) -> str:
    configured = os.environ.get("IDENTITY_PUBLIC_TENANT", default)
    return (configured or default).strip() or default


def identity_tenant_api_keys() -> dict[str, str]:
    raw = (os.environ.get("TENANT_API_KEYS", "") or "").strip()
    parsed: dict[str, str] = {}
    if raw:
        for pair in raw.split(","):
            entry = pair.strip()
            if not entry or ":" not in entry:
                continue
            tenant, api_key = entry.split(":", 1)
            tenant_id = tenant.strip()
            key_value = api_key.strip()
            if tenant_id and key_value:
                parsed[tenant_id] = key_value
    default_tenant_id = identity_default_tenant_id()
    default_api_key = identity_default_api_key()
    if default_tenant_id and default_api_key:
        parsed.setdefault(default_tenant_id, default_api_key)
    return parsed


def is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_weak_secret(value: str | None) -> bool:
    candidate = (value or "").strip()
    if not candidate:
        return True
    lowered = candidate.lower()
    if lowered in _WEAK_SECRET_MARKERS:
        return True
    return any(marker in lowered for marker in _WEAK_SECRET_MARKERS if len(marker) >= 6)


def require_secret(name: str, *, min_length: int = 24) -> str:
    value = (os.environ.get(name) or "").strip()
    if len(value) < min_length:
        raise RuntimeError(f"Missing or too-short required secret: {name} (min {min_length} chars)")
    if is_weak_secret(value):
        raise RuntimeError(f"Insecure secret value detected for {name}")
    return value


def service_name(default: str = "service") -> str:
    return (os.environ.get("SERVICE_NAME", default) or default).strip()


def service_port(default: int = 8000) -> int:
    try:
        return int(os.environ.get("PORT", str(default)))
    except ValueError:
        return default


def database_url() -> str:
    explicit = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("POSTGRES_DSN")
    if explicit:
        return explicit
    host = os.environ.get("POSTGRES_HOST", os.environ.get("PGHOST", "localhost"))
    port = os.environ.get("POSTGRES_PORT", os.environ.get("PGPORT", "5432"))
    user = (os.environ.get("POSTGRES_USER") or os.environ.get("PGUSER") or "").strip()
    password = (os.environ.get("POSTGRES_PASSWORD") or os.environ.get("PGPASSWORD") or "").strip()
    database = os.environ.get("POSTGRES_DB", os.environ.get("PGDATABASE", "pulse_engine"))
    auth_segment = ""
    if user and password:
        auth_segment = f"{user}:{password}@"
    elif user:
        auth_segment = f"{user}@"
    return f"postgresql://{auth_segment}{host}:{port}/{database}"


def db_schema(default: str = "public") -> str:
    return (os.environ.get("DB_SCHEMA", default) or default).strip()


def db_pool_min_size(default: int = 5) -> int:
    try:
        return int(os.environ.get("DB_POOL_MIN_SIZE", str(default)))
    except ValueError:
        return default


def db_pool_max_size(default: int = 20) -> int:
    try:
        return int(os.environ.get("DB_POOL_MAX_SIZE", str(default)))
    except ValueError:
        return default


def gateway_rate_limit_requests(default: int = 120) -> int:
    try:
        return int(os.environ.get("GATEWAY_RATE_LIMIT_REQUESTS", str(default)))
    except ValueError:
        return default


def gateway_rate_limit_window_seconds(default: int = 60) -> int:
    try:
        return int(os.environ.get("GATEWAY_RATE_LIMIT_WINDOW_SECONDS", str(default)))
    except ValueError:
        return default


def allow_local_ai_fallback() -> bool:
    return os.environ.get("ALLOW_LOCAL_AI_FALLBACK", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def ai_service_timeout_seconds(default: float = 45.0) -> float:
    try:
        return float(os.environ.get("AI_SERVICE_TIMEOUT_SECONDS", str(default)))
    except ValueError:
        return default


def ai_service_retry_attempts(default: int = 3) -> int:
    try:
        return int(os.environ.get("AI_SERVICE_RETRY_ATTEMPTS", str(default)))
    except ValueError:
        return default


def ai_service_retry_backoff_seconds(default: float = 0.5) -> float:
    try:
        return float(os.environ.get("AI_SERVICE_RETRY_BACKOFF_SECONDS", str(default)))
    except ValueError:
        return default


def ai_service_circuit_breaker_failures(default: int = 5) -> int:
    try:
        return int(os.environ.get("AI_SERVICE_CIRCUIT_BREAKER_FAILURES", str(default)))
    except ValueError:
        return default


def ai_service_circuit_breaker_recovery_seconds(default: float = 30.0) -> float:
    try:
        return float(os.environ.get("AI_SERVICE_CIRCUIT_BREAKER_RECOVERY_SECONDS", str(default)))
    except ValueError:
        return default


def background_queue_enabled() -> bool:
    explicit = os.environ.get("BACKGROUND_QUEUE_ENABLED", "").strip().lower()
    if explicit in {"1", "true", "yes", "on"}:
        return True
    if explicit in {"0", "false", "no", "off"}:
        return False
    return bool(background_queue_url())


def cache_redis_url() -> str:
    return (
        os.environ.get("CACHE_REDIS_URL")
        or os.environ.get("REDIS_URL")
        or os.environ.get("BACKGROUND_QUEUE_URL")
        or os.environ.get("QUEUE_REDIS_URL")
        or ""
    ).strip()


def rate_limit_redis_url() -> str:
    return (
        os.environ.get("RATE_LIMIT_REDIS_URL")
        or os.environ.get("REDIS_URL")
        or os.environ.get("CACHE_REDIS_URL")
        or os.environ.get("BACKGROUND_QUEUE_URL")
        or os.environ.get("QUEUE_REDIS_URL")
        or ""
    ).strip()


def background_queue_url() -> str:
    return (
        os.environ.get("BACKGROUND_QUEUE_URL") or os.environ.get("REDIS_URL") or os.environ.get("QUEUE_REDIS_URL") or ""
    ).strip()


def background_queue_stream_prefix(default: str = "pulse_engine:bg_jobs") -> str:
    return (os.environ.get("BACKGROUND_QUEUE_STREAM_PREFIX", default) or default).strip()


def background_queue_max_retries(default: int = 5) -> int:
    try:
        return int(os.environ.get("BACKGROUND_QUEUE_MAX_RETRIES", str(default)))
    except ValueError:
        return default


def background_queue_visibility_timeout_seconds(default: int = 300) -> int:
    try:
        return int(os.environ.get("BACKGROUND_QUEUE_VISIBILITY_TIMEOUT", str(default)))
    except ValueError:
        return default


def background_queue_job_timeout_seconds(default: int = 600) -> int:
    try:
        return int(os.environ.get("BACKGROUND_QUEUE_JOB_TIMEOUT_SECONDS", str(default)))
    except ValueError:
        return default


def background_queue_job_ttl_seconds(default: int = 86400) -> int:
    try:
        return int(os.environ.get("BACKGROUND_QUEUE_JOB_TTL_SECONDS", str(default)))
    except ValueError:
        return default


def background_queue_consumer_prefix(default: str = "worker") -> str:
    return (os.environ.get("BACKGROUND_QUEUE_CONSUMER_PREFIX", default) or default).strip()


def internal_service_secret() -> str:
    return os.environ.get("INTERNAL_SERVICE_SECRET", "").strip()


def frontend_url() -> str:
    configured = os.environ.get("FRONTEND_URL", "").strip() or os.environ.get("APP_URL", "").strip()
    if configured:
        return next(
            (origin.strip() for origin in configured.split(",") if origin.strip()),
            DEFAULT_ORIGINS[0],
        )
    return DEFAULT_ORIGINS[0]


@functools.lru_cache(maxsize=1)
def gateway_allowed_origins() -> list[str]:
    origins: list[str] = []
    seen: set[str] = set()
    configured_values = [
        origin.strip()
        for raw in (
            os.environ.get("FRONTEND_URL", ""),
            os.environ.get("APP_URL", ""),
            os.environ.get("GATEWAY_ALLOWED_ORIGINS", ""),
        )
        for origin in raw.split(",")
        if origin.strip()
    ]
    for origin in [*LOCAL_FRONTEND_ORIGINS, *DEFAULT_ORIGINS, *configured_values]:
        candidate = origin.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        origins.append(candidate)
    return origins or list(LOCAL_FRONTEND_ORIGINS)


def gateway_allowed_methods() -> list[str]:
    configured = os.environ.get("GATEWAY_ALLOWED_METHODS", "").strip()
    if configured:
        methods = [method.strip().upper() for method in configured.split(",") if method.strip()]
        return methods or ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    return ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


def gateway_allowed_headers() -> list[str]:
    configured = os.environ.get("GATEWAY_ALLOWED_HEADERS", "").strip()
    if configured:
        headers = [header.strip() for header in configured.split(",") if header.strip()]
        if headers:
            return headers
    return [
        "Authorization",
        "Content-Type",
        "Accept",
        "Origin",
        "X-Requested-With",
        "X-Request-ID",
        "X-Correlation-ID",
        "X-Trace-ID",
        "X-Span-ID",
        "X-Parent-Span-ID",
        "traceparent",
        "tracestate",
        "baggage",
        "X-Company-Id",
        "X-User-Id",
        "X-User-Role",
        "X-Internal-Service-Secret",
        "X-Bridge-Secret",
        "X-Tenant-ID",
        "X-API-Key",
    ]


def is_origin_allowed(origin: str) -> bool:
    return origin in gateway_allowed_origins()


def is_production() -> bool:
    return os.environ.get("ENVIRONMENT", os.environ.get("APP_ENV", "development")).strip().lower() in {
        "prod",
        "production",
    }


def socket_rate_limit_requests(default: int = 20) -> int:
    try:
        return int(os.environ.get("SOCKET_RATE_LIMIT_REQUESTS", str(default)))
    except ValueError:
        return default


def socket_rate_limit_window_seconds(default: int = 60) -> int:
    try:
        return int(os.environ.get("SOCKET_RATE_LIMIT_WINDOW_SECONDS", str(default)))
    except ValueError:
        return default
