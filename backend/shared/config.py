from __future__ import annotations

import functools
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)

DEFAULT_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
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
    "/api/visitor/track",
    "/api/unification/public",
    "/api/billing/webhooks/stripe",
    "/api/webhook/meta/",
    "/api/webhooks/web-chat",
    "/api/webhooks/external/purchases",
    "/api/public/companies/",
    "/api/products/media/",
    "/api/company-data/products/media/",
    "/api/settings/company/logo/media/",
    "/api/conversations/attachments/media/",
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
    query: dict[str, str] = {}
    sslmode = (
        os.environ.get("POSTGRES_SSLMODE")
        or os.environ.get("PGSSLMODE")
        or os.environ.get("DATABASE_SSLMODE")
        or ""
    ).strip()
    connect_timeout = (
        os.environ.get("POSTGRES_CONNECT_TIMEOUT")
        or os.environ.get("PGCONNECT_TIMEOUT")
        or ""
    ).strip()
    if sslmode:
        query["sslmode"] = sslmode
    if connect_timeout:
        query["connect_timeout"] = connect_timeout
    query_string = f"?{urlencode(query)}" if query else ""
    return f"postgresql://{auth_segment}{host}:{port}/{database}{query_string}"


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


def db_connect_timeout_seconds(default: float = 10.0) -> float:
    try:
        return float(os.environ.get("POSTGRES_CONNECT_TIMEOUT", str(default)))
    except ValueError:
        return default


def db_command_timeout_seconds(default: float = 60.0) -> float:
    try:
        return float(os.environ.get("DB_COMMAND_TIMEOUT_SECONDS", str(default)))
    except ValueError:
        return default


def db_startup_retries(default: int = 10) -> int:
    try:
        return max(1, int(os.environ.get("DB_STARTUP_RETRIES", str(default))))
    except ValueError:
        return default


def db_startup_retry_backoff_seconds(default: float = 2.0) -> float:
    try:
        return max(0.1, float(os.environ.get("DB_STARTUP_RETRY_BACKOFF_SECONDS", str(default))))
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


def ai_service_timeout_seconds(default: float = 60.0) -> float:
    try:
        return float(os.environ.get("AI_SERVICE_TIMEOUT_SECONDS", str(default)))
    except ValueError:
        return default


def ai_service_retry_attempts(default: int = 3) -> int:
    try:
        return int(os.environ.get("AI_SERVICE_RETRY_ATTEMPTS", str(default)))
    except ValueError:
        return default


def orchestrator_request_timeout_seconds(default: float = 60.0) -> float:
    try:
        return float(os.environ.get("ORCHESTRATOR_REQUEST_TIMEOUT_SECONDS", str(default)))
    except ValueError:
        return default


def ai_fallback_timeout_seconds(default: float = 60.0) -> float:
    try:
        return float(os.environ.get("AI_FALLBACK_TIMEOUT_SECONDS", str(default)))
    except ValueError:
        return default


def ai_input_token_budget(default: int = 12000) -> int:
    try:
        raw = os.environ.get("AI_INPUT_TOKEN_BUDGET", os.environ.get("GEMINI_INPUT_TOKEN_BUDGET", str(default)))
        return max(1, int(raw))
    except ValueError:
        return default


def ai_enable_rule_based_recovery() -> bool:
    return os.environ.get("AI_ENABLE_RULE_BASED_RECOVERY", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def ai_temperature(default: float = 0.7) -> float:
    try:
        return float(os.environ.get("AI_TEMPERATURE", str(default)) or default)
    except ValueError:
        return default


def ai_max_tokens(default: int = 8192) -> int:
    try:
        return int(os.environ.get("AI_MAX_TOKENS", str(default)) or default)
    except ValueError:
        return default


def ai_provider_name(default: str = "gemini") -> str:
    return (os.environ.get("AI_PROVIDER", default) or default).strip().lower()


def ai_model_name(default: str = "") -> str:
    return (os.environ.get("AI_MODEL_NAME", default) or default).strip()


def ai_api_call_timeout_seconds(default: float = 15.0) -> float:
    try:
        return max(1.0, min(15.0, float(os.environ.get("AI_API_CALL_TIMEOUT_SECONDS", str(default)) or default)))
    except ValueError:
        return default


def openai_api_key() -> str:
    return (os.environ.get("OPENAI_API_KEY") or "").strip()


def anthropic_api_key() -> str:
    return (os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def gemini_api_key() -> str:
    return (os.environ.get("GEMINI_API_KEY") or "").strip()


def openai_model_name(default: str = "gpt-4o-mini") -> str:
    return (os.environ.get("OPENAI_MODEL", default) or default).strip()


def anthropic_model_name(default: str = "claude-3-5-sonnet-20241022") -> str:
    return (os.environ.get("ANTHROPIC_MODEL", default) or default).strip()


def gemini_flash_model_name(default: str = "gemini-2.5-flash-lite") -> str:
    return (os.environ.get("GEMINI_FLASH_MODEL", default) or default).strip()


def gemini_pro_model_name(default: str = "gemini-2.5-pro") -> str:
    return (os.environ.get("GEMINI_PRO_MODEL", default) or default).strip()


def gemini_fallback_models(
    default: str = "gemini-2.5-flash-lite,gemini-2.5-flash,gemini-2.0-flash,gemini-2.0-flash-lite",
) -> list[str]:
    return [
        model.strip()
        for model in (os.environ.get("GEMINI_FALLBACK_MODELS", default) or default).split(",")
        if model.strip()
    ]


def normalize_gemini_embedding_model_name(model_name: str | None = None, default: str = "gemini-embedding-exp-03-07") -> str:
    """Return the Gemini embedding model name expected by the SDK.

    The REST API documents resource names as ``models/{model}``, while the
    Python SDK accepts the model code. Keeping the user-facing env flexible
    prevents ``models/models/...`` style failures without exposing secrets or
    silently inventing a different configured model.
    """
    raw = (model_name if model_name is not None else os.environ.get("GEMINI_EMBEDDING_MODEL", default))
    value = (raw or default).strip()
    while value.startswith("models/"):
        value = value.split("/", 1)[1].strip()
    return value or default


def gemini_embedding_model_name(default: str = "gemini-embedding-exp-03-07") -> str:
    return normalize_gemini_embedding_model_name(default=default)


def openai_embedding_model_name(default: str = "text-embedding-3-small") -> str:
    return (os.environ.get("OPENAI_EMBEDDING_MODEL", default) or default).strip()


def ai_embedding_unsupported_cooldown_seconds(default: int = 900) -> int:
    try:
        return max(0, int(os.environ.get("AI_EMBEDDING_UNSUPPORTED_COOLDOWN_SECONDS", str(default)) or default))
    except ValueError:
        return default


def ai_response_recent_ai_message_limit(default: int = 3) -> int:
    try:
        return max(1, int(os.environ.get("AI_RESPONSE_RECENT_MESSAGE_LIMIT", str(default)) or default))
    except ValueError:
        return default


def ai_response_temperature_base(default: float = 0.72) -> float:
    try:
        return float(os.environ.get("AI_RESPONSE_TEMPERATURE_BASE", str(default)) or default)
    except ValueError:
        return default


def ai_response_temperature_min(default: float = 0.55) -> float:
    try:
        return float(os.environ.get("AI_RESPONSE_TEMPERATURE_MIN", str(default)) or default)
    except ValueError:
        return default


def ai_response_temperature_max(default: float = 1.05) -> float:
    try:
        return float(os.environ.get("AI_RESPONSE_TEMPERATURE_MAX", str(default)) or default)
    except ValueError:
        return default


def ai_response_temperature_jitter_steps(default: int = 6) -> int:
    try:
        return max(0, int(os.environ.get("AI_RESPONSE_TEMPERATURE_JITTER_STEPS", str(default)) or default))
    except ValueError:
        return default


def ai_response_temperature_product_delta(default: float = 0.08) -> float:
    try:
        return float(os.environ.get("AI_RESPONSE_TEMPERATURE_PRODUCT_DELTA", str(default)) or default)
    except ValueError:
        return default


def ai_response_temperature_negative_delta(default: float = -0.10) -> float:
    try:
        return float(os.environ.get("AI_RESPONSE_TEMPERATURE_NEGATIVE_DELTA", str(default)) or default)
    except ValueError:
        return default


def ai_response_temperature_repetition_delta(default: float = 0.06) -> float:
    try:
        return float(os.environ.get("AI_RESPONSE_TEMPERATURE_REPETITION_DELTA", str(default)) or default)
    except ValueError:
        return default


def ai_response_retry_temperature_delta(default: float = 0.14) -> float:
    try:
        return float(os.environ.get("AI_RESPONSE_RETRY_TEMPERATURE_DELTA", str(default)) or default)
    except ValueError:
        return default


def ai_response_retry_temperature_max(default: float = 1.1) -> float:
    try:
        return float(os.environ.get("AI_RESPONSE_RETRY_TEMPERATURE_MAX", str(default)) or default)
    except ValueError:
        return default


def outbound_retry_base_delay_seconds(default: float = 1.0) -> float:
    try:
        return max(0.1, float(os.environ.get("OUTBOUND_RETRY_BASE_DELAY_SECONDS", str(default))))
    except ValueError:
        return default


def ai_response_cooldown_seconds(default: int = 300) -> int:
    try:
        return max(0, int(os.environ.get("AI_RESPONSE_COOLDOWN_SECONDS", str(default))))
    except ValueError:
        return default


def dedup_cache_ttl_seconds(default: int = 300) -> int:
    try:
        return max(30, int(os.environ.get("DEDUP_CACHE_TTL_SECONDS", str(default))))
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


def ai_enable_provider_fallback() -> bool:
    return os.environ.get("AI_ENABLE_PROVIDER_FALLBACK", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def ai_max_provider_attempts(default: int = 3) -> int:
    try:
        return max(1, int(os.environ.get("AI_MAX_PROVIDER_ATTEMPTS", str(default)) or default))
    except ValueError:
        return default


def ai_max_llm_calls_per_message(default: int = 1) -> int:
    try:
        return max(1, int(os.environ.get("AI_MAX_LLM_CALLS_PER_MESSAGE", str(default)) or default))
    except ValueError:
        return default


def ai_max_embedding_calls_per_message(default: int = 1) -> int:
    try:
        return max(0, int(os.environ.get("AI_MAX_EMBEDDING_CALLS_PER_MESSAGE", str(default)) or default))
    except ValueError:
        return default


def ai_analytics_llm_enabled() -> bool:
    return os.environ.get("AI_ANALYTICS_LLM_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def ai_analytics_summary_min_messages(default: int = 12) -> int:
    try:
        return max(1, int(os.environ.get("AI_ANALYTICS_SUMMARY_MIN_MESSAGES", str(default)) or default))
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


def background_queue_max_stream_length(default: int = 10000) -> int:
    try:
        return max(1000, int(os.environ.get("BACKGROUND_QUEUE_MAX_STREAM_LENGTH", str(default))))
    except ValueError:
        return default


def background_queue_consumer_prefix(default: str = "worker") -> str:
    return (os.environ.get("BACKGROUND_QUEUE_CONSUMER_PREFIX", default) or default).strip()


def internal_service_secret() -> str:
    return os.environ.get("INTERNAL_SERVICE_SECRET", "").strip()


def backend_public_url() -> str:
    """Public-facing base URL of the API gateway.

    Used to build absolute media URLs that external services (WhatsApp, Meta)
    can download.  In production set BACKEND_PUBLIC_URL to the real domain.
    Defaults to http://localhost:8000 for local development.
    """
    return os.environ.get("BACKEND_PUBLIC_URL", "http://localhost:8000").rstrip("/")


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
    if is_production():
        if not configured_values:
            raise RuntimeError("Gateway CORS requires explicit production origins")
        source_origins = configured_values
    else:
        source_origins = [*LOCAL_FRONTEND_ORIGINS, *DEFAULT_ORIGINS, *configured_values]
    for origin in source_origins:
        candidate = origin.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        origins.append(candidate)
    if is_production() and "*" in origins:
        raise RuntimeError("Gateway CORS requires explicit production origins")
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


def webhook_replay_ttl_seconds(default: int = 300) -> int:
    try:
        return max(60, int(os.environ.get("WEBHOOK_REPLAY_TTL_SECONDS", str(default)) or default))
    except ValueError:
        return default


def webhook_signature_max_skew_seconds(default: int = 300) -> int:
    try:
        return max(30, int(os.environ.get("WEBHOOK_SIGNATURE_MAX_SKEW_SECONDS", str(default)) or default))
    except ValueError:
        return default


def meta_webhook_rate_limit_per_minute(default: int = 120) -> int:
    try:
        return max(10, int(os.environ.get("META_WEBHOOK_RATE_LIMIT_PER_MINUTE", str(default)) or default))
    except ValueError:
        return default


def meta_webhook_event_replay_ttl_seconds(default: int = 86400) -> int:
    try:
        return max(300, int(os.environ.get("META_WEBHOOK_EVENT_REPLAY_TTL_SECONDS", str(default)) or default))
    except ValueError:
        return default


def unprocessed_event_max_retries(default: int = 8) -> int:
    try:
        return max(1, int(os.environ.get("UNPROCESSED_EVENT_MAX_RETRIES", str(default)) or default))
    except ValueError:
        return default


def unprocessed_event_retry_base_seconds(default: int = 60) -> int:
    try:
        return max(15, int(os.environ.get("UNPROCESSED_EVENT_RETRY_BASE_SECONDS", str(default)) or default))
    except ValueError:
        return default


def webhook_identity_resolve_timeout_seconds(default: float = 10.0) -> float:
    try:
        return max(1.0, float(os.environ.get("IDENTITY_RESOLVE_TIMEOUT_SECONDS", str(default)) or default))
    except ValueError:
        return default


def webhook_message_history_fetch_limit(default: int = 20) -> int:
    try:
        return max(1, int(os.environ.get("WEBHOOK_MESSAGE_HISTORY_FETCH_LIMIT", str(default)) or default))
    except ValueError:
        return default


def whatsapp_mode(default: str = "bridge") -> str:
    return (os.environ.get("WHATSAPP_MODE", default) or default).strip().lower()


def whatsapp_bridge_url(default: str = "http://localhost:3001") -> str:
    return (os.environ.get("WHATSAPP_BRIDGE_URL", default) or default).rstrip("/")


def whatsapp_bridge_secret() -> str:
    return (
        os.environ.get("WHATSAPP_BRIDGE_SECRET")
        or os.environ.get("BRIDGE_SECRET")
        or ""
    ).strip()


def messaging_http_timeout_seconds(default: float = 20.0) -> float:
    try:
        return max(1.0, float(os.environ.get("MESSAGING_HTTP_TIMEOUT_SECONDS", str(default)) or default))
    except ValueError:
        return default


def messaging_http_connect_timeout_seconds(default: float = 5.0) -> float:
    try:
        return max(0.1, float(os.environ.get("MESSAGING_HTTP_CONNECT_TIMEOUT_SECONDS", str(default)) or default))
    except ValueError:
        return default


def messaging_http_pool_timeout_seconds(default: float = 5.0) -> float:
    try:
        return max(0.1, float(os.environ.get("MESSAGING_HTTP_POOL_TIMEOUT_SECONDS", str(default)) or default))
    except ValueError:
        return default


def messaging_http_max_keepalive_connections(default: int = 10) -> int:
    try:
        return max(1, int(os.environ.get("MESSAGING_HTTP_MAX_KEEPALIVE_CONNECTIONS", str(default)) or default))
    except ValueError:
        return default


def messaging_http_max_connections(default: int = 20) -> int:
    try:
        return max(1, int(os.environ.get("MESSAGING_HTTP_MAX_CONNECTIONS", str(default)) or default))
    except ValueError:
        return default


def messaging_http_keepalive_expiry_seconds(default: float = 60.0) -> float:
    try:
        return max(1.0, float(os.environ.get("MESSAGING_HTTP_KEEPALIVE_EXPIRY_SECONDS", str(default)) or default))
    except ValueError:
        return default


def whatsapp_bridge_session_timeout_seconds(default: float = 5.0) -> float:
    try:
        return max(0.1, float(os.environ.get("WHATSAPP_BRIDGE_SESSION_TIMEOUT_SECONDS", str(default)) or default))
    except ValueError:
        return default


def whatsapp_bridge_send_timeout_seconds(default: float = 20.0) -> float:
    try:
        return max(1.0, float(os.environ.get("WHATSAPP_BRIDGE_SEND_TIMEOUT_SECONDS", str(default)) or default))
    except ValueError:
        return default


def whatsapp_bridge_health_timeout_seconds(default: float = 5.0) -> float:
    try:
        return max(0.1, float(os.environ.get("WHATSAPP_BRIDGE_HEALTH_TIMEOUT_SECONDS", str(default)) or default))
    except ValueError:
        return default


def meta_message_send_timeout_seconds(default: float = 15.0) -> float:
    try:
        return max(1.0, float(os.environ.get("META_MESSAGE_SEND_TIMEOUT_SECONDS", str(default)) or default))
    except ValueError:
        return default


def meta_graph_api_version(default: str = "v21.0") -> str:
    version = (os.environ.get("META_GRAPH_API_VERSION") or os.environ.get("META_API_VERSION") or default or "v21.0").strip()
    return version if version.lower().startswith("v") else f"v{version}"


# ── Media storage ──────────────────────────────────────────────────────────────

def storage_backend(default: str = "local") -> str:
    """Return 'local' or 's3' depending on the STORAGE_BACKEND env var."""
    return (os.environ.get("STORAGE_BACKEND", default) or default).strip().lower()


def aws_s3_bucket() -> str:
    return (os.environ.get("AWS_S3_BUCKET", "") or "").strip()


def aws_s3_region(default: str = "us-east-1") -> str:
    return (os.environ.get("AWS_S3_REGION", default) or default).strip()


def aws_s3_cdn_base_url() -> str:
    """Optional CloudFront or CDN base URL for serving S3 objects."""
    return (os.environ.get("AWS_S3_CDN_BASE_URL", "") or "").rstrip("/")


def aws_access_key_id() -> str:
    """Explicit AWS access key ID (leave empty to use IAM role in ECS/EC2)."""
    return (os.environ.get("AWS_ACCESS_KEY_ID", "") or "").strip()


def aws_secret_access_key() -> str:
    """Explicit AWS secret access key (leave empty to use IAM role in ECS/EC2)."""
    return (os.environ.get("AWS_SECRET_ACCESS_KEY", "") or "").strip()
