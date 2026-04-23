import hashlib
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
from dotenv import load_dotenv
from fastapi import Header, HTTPException, Request, status
from pydantic import BaseModel

from shared.config import identity_public_tenant_id, is_production, is_weak_secret

PROJECT_ROOT = Path(__file__).resolve().parents[5]
load_dotenv(PROJECT_ROOT / ".env", override=False)

JWT_SECRET = (os.environ.get("JWT_SECRET") or "").strip()
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_EXP_MINUTES = int(os.environ.get("JWT_EXP_MINUTES", "480"))
ADMIN_EMAIL = (os.environ.get("IDENTITY_ADMIN_EMAIL") or "").strip().lower()
ADMIN_PASSWORD = (os.environ.get("IDENTITY_ADMIN_PASSWORD") or "").strip()
DEFAULT_TENANT_ID = (os.environ.get("DEFAULT_TENANT_ID") or "").strip() or "demo_tenant"
DEFAULT_PUBLIC_TENANT = identity_public_tenant_id()
logger = logging.getLogger(__name__)


def _parse_map(raw_value: str | None, default_key: str, default_value: str) -> dict[str, str]:
    if not raw_value:
        if default_key and default_value:
            return {default_key: default_value}
        return {}
    output: dict[str, str] = {}
    for pair in raw_value.split(","):
        if not pair.strip() or ":" not in pair:
            continue
        key, value = pair.split(":", 1)
        output[key.strip()] = value.strip()
    if output:
        return output
    if default_key and default_value:
        return {default_key: default_value}
    return {}


TENANT_API_KEYS = _parse_map(
    os.environ.get("TENANT_API_KEYS"), DEFAULT_TENANT_ID, (os.environ.get("DEFAULT_TENANT_API_KEY") or "").strip()
)
TENANT_SALTS = _parse_map(
    os.environ.get("TENANT_SALTS"), DEFAULT_TENANT_ID, (os.environ.get("DEFAULT_TENANT_SALT") or "").strip()
)


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _self_service_mode_enabled() -> bool:
    return _is_truthy(os.environ.get("IDENTITY_SELF_SERVICE_MODE", "false"))


def _default_tenant_api_key() -> str:
    return (os.environ.get("DEFAULT_TENANT_API_KEY") or "").strip()


def _default_tenant_salt() -> str:
    return (os.environ.get("DEFAULT_TENANT_SALT") or "").strip()


def _public_unification_tenant_salt() -> str:
    explicit = (os.environ.get("PUBLIC_UNIFICATION_TENANT_SALT") or "").strip()
    if explicit:
        return explicit
    mapped = TENANT_SALTS.get(DEFAULT_PUBLIC_TENANT)
    if mapped:
        return mapped
    default_salt = _default_tenant_salt()
    if default_salt:
        return default_salt
    seed = (
        (os.environ.get("PUBLIC_UNIFICATION_TENANT_SALT_SEED") or "").strip()
        or (os.environ.get("INTERNAL_SERVICE_SECRET") or "").strip()
        or (os.environ.get("JWT_SECRET") or "").strip()
        or "pulse-public-unification-fallback"
    )
    return hashlib.sha256(f"{DEFAULT_PUBLIC_TENANT}:{seed}".encode("utf-8")).hexdigest()


def _is_public_unification_route(path: str) -> bool:
    safe_path = str(path or "").strip()
    return safe_path == "/api/unification/public" or safe_path.startswith("/api/unification/public/")


def _tenant_api_key_for(tenant_id: str) -> str | None:
    direct = TENANT_API_KEYS.get(tenant_id)
    if direct:
        return direct
    if _self_service_mode_enabled():
        return TENANT_API_KEYS.get(DEFAULT_TENANT_ID) or _default_tenant_api_key()
    return None


def _tenant_salt_for(tenant_id: str) -> str | None:
    if tenant_id == DEFAULT_PUBLIC_TENANT:
        salt = _public_unification_tenant_salt()
        if salt:
            return salt
    direct = TENANT_SALTS.get(tenant_id)
    if direct:
        return direct
    if _self_service_mode_enabled():
        return TENANT_SALTS.get(DEFAULT_TENANT_ID) or _default_tenant_salt()
    return None


@dataclass
class TenantContext:
    tenant_id: str
    api_key: str


class AdminIdentity(BaseModel):
    email: str
    role: str = "admin"


async def get_tenant_context(
    request: Request,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> TenantContext:
    raw_tenant_id = (x_tenant_id or "").strip()
    raw_api_key = (x_api_key or "").strip()
    self_service_mode = _self_service_mode_enabled()
    path = str(getattr(request.url, "path", "") or "")
    public_unification_route = _is_public_unification_route(path)
    fallback_tenant = DEFAULT_TENANT_ID or "demo_tenant"

    if public_unification_route:
        tenant_id = DEFAULT_PUBLIC_TENANT
        resolved_salt = _tenant_salt_for(tenant_id)
        if not resolved_salt:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Public unification tenant salt is not configured",
            )
        return TenantContext(
            tenant_id=tenant_id,
            api_key=raw_api_key or (_tenant_api_key_for(tenant_id) or ""),
        )

    tenant_id = raw_tenant_id or fallback_tenant
    if not raw_tenant_id:
        logger.info(
            "identity tenant header missing, using fallback tenant_id=%s",
            tenant_id,
        )

    expected_api_key = _tenant_api_key_for(tenant_id)
    if not expected_api_key:
        logger.warning(
            "identity tenant configuration unavailable requested_tenant=%s fallback_tenant=%s",
            tenant_id,
            fallback_tenant,
        )
        tenant_id = fallback_tenant
        expected_api_key = _tenant_api_key_for(tenant_id)
    if not expected_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tenant configuration unavailable",
        )

    resolved_api_key = raw_api_key or (expected_api_key if self_service_mode else "")
    if resolved_api_key != expected_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid tenant credentials")

    resolved_salt = _tenant_salt_for(tenant_id)
    if not resolved_salt:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Tenant salt not configured")

    if self_service_mode and (not raw_tenant_id or not raw_api_key):
        logger.info(
            "identity self-service mode resolved credentials tenant_id=%s headers_supplied=%s",
            tenant_id,
            bool(raw_tenant_id and raw_api_key),
        )

    request.state.identity_tenant_id = tenant_id
    logger.info(
        "identity tenant context resolved tenant_id=%s public_route=%s self_service=%s",
        tenant_id,
        public_unification_route,
        self_service_mode,
    )
    return TenantContext(tenant_id=tenant_id, api_key=resolved_api_key)


async def get_current_admin_user(authorization: str = Header(..., alias="Authorization")) -> AdminIdentity:
    if not JWT_SECRET:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="JWT secret is not configured")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin token") from exc
    role = str(payload.get("role") or "").strip().lower()
    if role not in {"admin", "super_admin"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return AdminIdentity(email=payload.get("sub", ADMIN_EMAIL))


def authenticate_admin(email: str, password: str) -> AdminIdentity | None:
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        logger.error("Identity admin credentials are not configured")
        return None
    if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
        return AdminIdentity(email=email)
    return None


def create_admin_token(admin: AdminIdentity) -> tuple[str, int]:
    if not JWT_SECRET:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="JWT secret is not configured")
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXP_MINUTES)
    payload = {
        "sub": admin.email,
        "role": admin.role,
        "exp": expires_at,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM), JWT_EXP_MINUTES * 60


def _is_admin_role(raw_role: str | None) -> bool:
    role = (raw_role or "").strip().lower()
    return role in {"admin", "super_admin"}


async def require_internal_admin(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_internal_service_secret: str | None = Header(default=None, alias="X-Internal-Service-Secret"),
) -> dict[str, str]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    if not JWT_SECRET:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="JWT secret is not configured")

    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin token") from exc

    if not _is_admin_role(payload.get("role")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")

    expected = (os.environ.get("INTERNAL_SERVICE_SECRET") or "").strip()
    provided = (x_internal_service_secret or "").strip()
    if expected and provided == expected:
        return {
            "sub": str(payload.get("sub") or "").strip(),
            "role": str(payload.get("role") or "").strip().lower(),
        }
    if not expected and not is_production():
        return {
            "sub": str(payload.get("sub") or "").strip(),
            "role": str(payload.get("role") or "").strip().lower(),
        }
    if _self_service_mode_enabled() and not is_production():
        logger.info("identity direct admin bridge allowed without internal secret in self-service mode")
        return {
            "sub": str(payload.get("sub") or "").strip(),
            "role": str(payload.get("role") or "").strip().lower(),
        }

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid internal service secret",
    )


def get_tenant_salt(tenant_id: str) -> str:
    salt = _tenant_salt_for(tenant_id)
    if not salt:
        raise KeyError(f"Tenant salt missing for '{tenant_id}'")
    return salt


def normalize_hash_input(value: str) -> str:
    return value.strip().lower()


def hash_with_tenant_salt(tenant_id: str, value: str | None) -> str | None:
    if not value:
        return None
    normalized = normalize_hash_input(value)
    if not normalized:
        return None
    salt = get_tenant_salt(tenant_id)
    return hashlib.sha256(f"{tenant_id}:{salt}:{normalized}".encode("utf-8")).hexdigest()


def issue_consent_token() -> str:
    return secrets.token_urlsafe(32)


def anonymize_ip(ip_address: str | None) -> str | None:
    if not ip_address:
        return None
    chunks = ip_address.split(".")
    if len(chunks) == 4:
        masked = ".".join(chunks[:3] + ["0"])
        return hashlib.sha256(masked.encode("utf-8")).hexdigest()
    return hashlib.sha256(ip_address.encode("utf-8")).hexdigest()


def get_admin_credentials() -> dict[str, Any]:
    default_api_key = _tenant_api_key_for(DEFAULT_TENANT_ID) or _default_tenant_api_key()
    return {
        "email": ADMIN_EMAIL,
        "tenant_id": DEFAULT_TENANT_ID,
        "api_key": default_api_key,
    }


async def verify_internal_secret(
    x_internal_service_secret: str | None = Header(default=None),
) -> None:
    expected = (os.environ.get("INTERNAL_SERVICE_SECRET") or "").strip()
    if not expected:
        if is_production():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Internal service secret is not configured",
            )
        return
    provided = (x_internal_service_secret or "").strip()
    if provided != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal service secret",
        )


def validate_identity_security_configuration() -> None:
    if not is_production():
        return

    problems: list[str] = []
    if not JWT_SECRET:
        problems.append("JWT_SECRET is required")
    elif len(JWT_SECRET) < 32 or is_weak_secret(JWT_SECRET):
        problems.append("JWT_SECRET must be strong and at least 32 characters")

    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        problems.append("IDENTITY_ADMIN_EMAIL and IDENTITY_ADMIN_PASSWORD are required")
    elif is_weak_secret(ADMIN_PASSWORD) or len(ADMIN_PASSWORD) < 12:
        problems.append("IDENTITY_ADMIN_PASSWORD must be strong and at least 12 characters")

    if not (os.environ.get("INTERNAL_SERVICE_SECRET") or "").strip():
        problems.append("INTERNAL_SERVICE_SECRET is required")

    if not TENANT_API_KEYS:
        problems.append("TENANT_API_KEYS (or DEFAULT_TENANT_ID + DEFAULT_TENANT_API_KEY) is required")
    if not TENANT_SALTS:
        problems.append("TENANT_SALTS (or DEFAULT_TENANT_ID + DEFAULT_TENANT_SALT) is required")
    if not _tenant_salt_for(DEFAULT_PUBLIC_TENANT):
        problems.append(
            "Public unification tenant salt is required (PUBLIC_UNIFICATION_TENANT_SALT or TENANT_SALTS entry)"
        )

    if problems:
        raise RuntimeError("; ".join(problems))
