import ipaddress
import os
from urllib.parse import urlparse

from fastapi import Request


def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else ""


def normalize_base_url(value: str) -> str:
    return (value or "").strip().rstrip("/")


def normalize_public_media_url(value: str) -> str:
    """Return an externally reachable media URL, or an empty string when none can be built."""
    media_url = str(value or "").strip()
    if not media_url:
        return ""
    public_base = _first_configured_base_url("PUBLIC_BACKEND_URL", "BACKEND_PUBLIC_URL")
    try:
        parsed = urlparse(media_url)
    except Exception:
        parsed = None
    if parsed and parsed.scheme in ("http", "https") and parsed.netloc:
        hostname = (parsed.hostname or "").strip().lower()
        if hostname not in {"localhost", "127.0.0.1", "api-gateway", "gateway", "customer"}:
            return media_url
        if not public_base:
            return ""
        suffix = parsed.path or "/"
        if parsed.query:
            suffix = f"{suffix}?{parsed.query}"
        return f"{public_base}{suffix}"
    if not public_base:
        return ""
    return f"{public_base}/{media_url.lstrip('/')}"


def _is_dev_trusted_frontend_host(hostname: str) -> bool:
    """Allow common local / LAN origins for OAuth redirects without FRONTEND_URL set."""
    host = (hostname or "").strip().lower()
    if not host:
        return False
    if host.endswith(".local"):
        return True
    bare = host.strip("[]")
    try:
        ip = ipaddress.ip_address(bare)
        return bool(ip.is_private or ip.is_loopback or ip.is_link_local)
    except ValueError:
        return False


def _first_configured_base_url(*env_names: str) -> str:
    for env_name in env_names:
        raw = os.environ.get(env_name, "")
        for candidate in raw.split(","):
            normalized = normalize_base_url(candidate)
            if normalized:
                return normalized
    return ""


def _loopback_base_url(request: Request) -> str:
    forwarded_proto = (request.headers.get("x-forwarded-proto", "") or request.url.scheme).split(",")[0].strip()
    forwarded_host = (
        (request.headers.get("x-forwarded-host", "") or request.headers.get("host", "")).split(",")[0].strip()
    )
    if not forwarded_host:
        return ""
    try:
        parsed = urlparse(f"{forwarded_proto or 'http'}://{forwarded_host}")
    except Exception:
        return ""
    hostname = (parsed.hostname or "").lower()
    if hostname not in ("localhost", "127.0.0.1"):
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def resolve_backend_base_url(request: Request) -> str:
    loopback_base = _loopback_base_url(request)
    if loopback_base:
        return loopback_base
    configured = _first_configured_base_url("BACKEND_URL", "APP_URL")
    if configured:
        return configured
    forwarded_proto = (request.headers.get("x-forwarded-proto", "") or request.url.scheme).split(",")[0].strip()
    forwarded_host = (
        (request.headers.get("x-forwarded-host", "") or request.headers.get("host", "")).split(",")[0].strip()
    )
    if forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def resolve_frontend_base_url(request: Request) -> str:
    configured = _first_configured_base_url("FRONTEND_URL", "APP_URL")
    if configured:
        return configured
    origin = normalize_base_url(request.headers.get("origin", ""))
    if origin:
        return origin
    return resolve_backend_base_url(request)


def sanitize_frontend_origin(frontend_origin: str) -> str:
    candidate = normalize_base_url(frontend_origin)
    if not candidate:
        return ""
    try:
        parsed = urlparse(candidate)
    except Exception:
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    hostname = (parsed.hostname or "").lower()
    if hostname in ("localhost", "127.0.0.1", "::1"):
        return f"{parsed.scheme}://{parsed.netloc}"
    if hostname.endswith(".preview.emergentagent.com"):
        return f"{parsed.scheme}://{parsed.netloc}"
    if _is_dev_trusted_frontend_host(hostname):
        return f"{parsed.scheme}://{parsed.netloc}"
    configured_origins = [
        normalize_base_url(origin)
        for env_name in ("FRONTEND_URL", "APP_URL")
        for origin in os.environ.get(env_name, "").split(",")
    ]
    if any(origin and origin.lower() == candidate.lower() for origin in configured_origins):
        return candidate
    return ""


def build_google_redirect_uri(request: Request) -> str:
    return f"{resolve_backend_base_url(request)}/api/auth/google/callback"


def build_facebook_redirect_uri(request: Request) -> str:
    return f"{resolve_backend_base_url(request)}/api/auth/facebook/callback"


def build_oauth_redirect_url(
    request: Request,
    session_token: str,
    next_path: str = "",
    error: str = "",
    frontend_origin: str = "",
) -> str:
    from urllib.parse import urlencode

    base = sanitize_frontend_origin(frontend_origin) or resolve_frontend_base_url(request)
    params = {}
    if error:
        params["error"] = error
    elif session_token:
        params["session_id"] = session_token
        if next_path.startswith("/"):
            params["next"] = next_path
    fragment = urlencode(params)
    return f"{base}/auth/callback#{fragment}" if fragment else f"{base}/auth/callback"
