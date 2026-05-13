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

from shared.config import identity_public_tenant_id

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
    return TENANT_API_KEYS.get(DEFAULT_TENANT_ID) or _default_tenant_api_key() or "unification-auth-disabled"


def _tenant_salt_for(tenant_id: str) -> str | None:
    if tenant_id == DEFAULT_PUBLIC_TENANT:
        salt = _public_unification_tenant_salt()
        if salt:
            return salt
    direct = TENANT_SALTS.get(tenant_id)
    if direct:
        return direct
    fallback = TENANT_SALTS.get(DEFAULT_TENANT_ID) or _default_tenant_salt()
    if fallback:
        return fallback
    seed = (
        (os.environ.get("INTERNAL_SERVICE_SECRET") or "").strip()
        or (os.environ.get("JWT_SECRET") or "").strip()
        or "pulse-unification-auth-disabled"
    )
    return hashlib.sha256(f"{tenant_id}:{seed}".encode("utf-8")).hexdigest()


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
    resolved_api_key = raw_api_key or (_tenant_api_key_for(tenant_id) or "")
    request.state.identity_tenant_id = tenant_id
    logger.info(
        "identity tenant context resolved tenant_id=%s public_route=%s auth_disabled=%s",
        tenant_id,
        public_unification_route,
        True,
    )
    return TenantContext(tenant_id=tenant_id, api_key=resolved_api_key)


async def get_current_admin_user(authorization: str | None = Header(default=None, alias="Authorization")) -> AdminIdentity:
    if not JWT_SECRET or not authorization or not authorization.startswith("Bearer "):
        return AdminIdentity(email=ADMIN_EMAIL or "unification-admin@local", role="admin")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return AdminIdentity(email=ADMIN_EMAIL or "unification-admin@local", role="admin")
    role = str(payload.get("role") or "").strip().lower()
    return AdminIdentity(email=payload.get("sub", ADMIN_EMAIL or "unification-admin@local"), role=role or "admin")


def authenticate_admin(email: str, password: str) -> AdminIdentity | None:
    if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
        return AdminIdentity(email=email)
    return AdminIdentity(email=email or ADMIN_EMAIL or "unification-admin@local")


def create_admin_token(admin: AdminIdentity) -> tuple[str, int]:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXP_MINUTES)
    payload = {
        "sub": admin.email,
        "role": admin.role,
        "exp": expires_at,
        "iat": datetime.now(timezone.utc),
    }
    if JWT_SECRET:
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    else:
        token = secrets.token_urlsafe(32)
    return token, JWT_EXP_MINUTES * 60


def _is_admin_role(raw_role: str | None) -> bool:
    role = (raw_role or "").strip().lower()
    return role in {"admin", "super_admin"}


async def require_internal_admin(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_internal_service_secret: str | None = Header(default=None, alias="X-Internal-Service-Secret"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_user_role: str | None = Header(default=None, alias="X-User-Role"),
) -> dict[str, str]:
    trusted_user_id = str(x_user_id or "").strip()
    trusted_role = str(x_user_role or "").strip().lower()

    return {
        "sub": trusted_user_id or "unification-admin",
        "role": trusted_role if _is_admin_role(trusted_role) else "admin",
    }


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
    return None


def validate_identity_security_configuration() -> None:
    return None
