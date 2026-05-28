from __future__ import annotations

import hmac
import re
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from shared.auth.jwt import decode_token, is_valid_company_id
from shared.config import PUBLIC_GATEWAY_PREFIXES
from shared.config import internal_service_secret

security = HTTPBearer(auto_error=False)
ROLE_RE = re.compile(r"^[a-z_][a-z0-9_:-]{1,63}$", re.IGNORECASE)
INTERNAL_HEADER_NAMES = {
    "x-user-id",
    "x-company-id",
    "x-user-role",
    "x-internal-service-secret",
}
META_WEBHOOK_PUBLIC_PREFIXES = (
    "/api/webhook/meta/",
    "/webhook/meta/",
)


def unauthorized_exception() -> HTTPException:
    return HTTPException(status_code=401, detail="Unauthorized")


def forbidden_exception() -> HTTPException:
    return HTTPException(status_code=403, detail="Unauthorized")


def is_options_request(request: Request) -> bool:
    return request.method.upper() == "OPTIONS"


def extract_bearer_token(request: Request) -> str:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return ""
    parts = auth.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[-1].strip()


def has_authorization_header(request: Request) -> bool:
    return bool(extract_bearer_token(request))


def _direct_jwt_allowed(request: Request) -> bool:
    return bool(getattr(request.app.state, "allow_direct_jwt_auth", False))


def _public_service_path(path: str) -> bool:
    if path in {"/health", "/api/healthz"} or path.startswith("/health/"):
        return True
    if any(path == prefix or path.startswith(prefix) for prefix in PUBLIC_GATEWAY_PREFIXES):
        return True
    return any(path.startswith(prefix) for prefix in META_WEBHOOK_PUBLIC_PREFIXES)


def _internal_secret_valid(request: Request) -> bool:
    configured = internal_service_secret()
    if not configured:
        return False
    supplied = request.headers.get("X-Internal-Service-Secret", "").strip()
    if not supplied:
        return False
    return hmac.compare_digest(configured, supplied)


def _normalize_trusted_headers(request: Request) -> dict[str, Any] | None:
    user_id = str(request.headers.get("X-User-Id", "")).strip()
    company_id = str(request.headers.get("X-Company-Id", "")).strip()
    role = str(request.headers.get("X-User-Role", "")).strip()
    if not user_id and not company_id and not role:
        return None
    # Gateway infers company_id from the request body for unauthenticated public
    # webhook requests (e.g. web-chat). In that case user_id and role are empty —
    # return a minimal company-only context rather than raising.
    if not user_id and not role and company_id:
        return {"company_id": company_id, "cid": company_id}
    if not user_id or not role or not ROLE_RE.fullmatch(role):
        raise unauthorized_exception()
    if role.lower() != "super_admin" and not is_valid_company_id(company_id):
        raise unauthorized_exception()
    return {
        "sub": user_id,
        "company_id": company_id,
        "cid": company_id,
        "role": role,
    }


def _validate_jwt_request_context(request: Request) -> dict[str, Any] | None:
    if not _direct_jwt_allowed(request):
        return None
    token = extract_bearer_token(request)
    if not token:
        return None
    payload = decode_token(token)
    if not payload or not payload.get("sub") or not payload.get("role"):
        return None
    company_id = str(payload.get("company_id") or "").strip()
    if payload.get("role") != "super_admin" and (not company_id or not is_valid_company_id(company_id)):
        return None
    request.state.auth_context = payload
    return payload


def extract_company_id_from_request(request: Request) -> str | None:
    if is_options_request(request):
        return None
    cached = getattr(request.state, "auth_context", None)
    if cached is not None:
        return str(cached.get("company_id") or "").strip() or None
    if _internal_secret_valid(request):
        trusted = _normalize_trusted_headers(request)
        if trusted:
            request.state.auth_context = trusted
            return trusted.get("company_id") or None
        return None
    payload = _validate_jwt_request_context(request)
    if payload:
        company_id = str(payload.get("company_id") or "").strip()
        if payload.get("role") == "super_admin":
            return None
        if not company_id or not is_valid_company_id(company_id):
            raise unauthorized_exception()
        return company_id
    return None


def is_trusted_service_request(request: Request) -> bool:
    if is_options_request(request):
        return True
    if _public_service_path(request.url.path):
        return True
    if _internal_secret_valid(request):
        return True
    return _validate_jwt_request_context(request) is not None


def build_trusted_context_from_request(request: Request) -> dict[str, Any] | None:
    if is_options_request(request):
        return {}
    cached = getattr(request.state, "auth_context", None)
    if cached is not None:
        return cached
    if _internal_secret_valid(request):
        trusted = _normalize_trusted_headers(request)
        if trusted:
            request.state.auth_context = trusted
            return trusted
    return _validate_jwt_request_context(request)


async def resolve_request_user(request: Request, credentials: HTTPAuthorizationCredentials | None = None) -> dict:
    if is_options_request(request):
        return {}
    trusted = build_trusted_context_from_request(request)
    if trusted:
        return trusted
    if _direct_jwt_allowed(request):
        token = credentials.credentials if credentials else extract_bearer_token(request)
        payload = decode_token(token)
        if not payload or not payload.get("sub") or not payload.get("role"):
            raise unauthorized_exception()
        company_id = str(payload.get("company_id") or "").strip()
        if payload.get("role") != "super_admin" and (not company_id or not is_valid_company_id(company_id)):
            raise unauthorized_exception()
        request.state.auth_context = payload
        return payload
    raise unauthorized_exception()


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    return await resolve_request_user(request, credentials)


def get_company_id(current_user: dict = Depends(get_current_user)) -> str:
    company_id = str(current_user.get("company_id") or "").strip()
    if not company_id:
        raise unauthorized_exception()
    return company_id


def require_role(allowed_roles: list[str]):
    async def checker(current_user: dict = Depends(get_current_user)) -> dict:
        if current_user.get("role") not in allowed_roles:
            raise forbidden_exception()
        return current_user

    return checker
