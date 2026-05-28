"""
channel_layer/validators.py — Webhook signature validation utilities.

Consolidates all signature verification logic into reusable validators
that adapters can delegate to.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_module
import logging
import os
from datetime import datetime, timezone

from fastapi import HTTPException, Request

from shared.cache import get_cache_client

logger = logging.getLogger(__name__)

_WEBHOOK_REPLAY_TTL_SECONDS = max(60, int(os.environ.get("WEBHOOK_REPLAY_TTL_SECONDS", "300") or 300))
_WEBHOOK_MAX_SKEW_SECONDS = max(30, int(os.environ.get("WEBHOOK_SIGNATURE_MAX_SKEW_SECONDS", "300") or 300))


def verify_hmac_sha256(secret: str, message: bytes, provided_signature: str) -> bool:
    """Verify an HMAC-SHA256 signature."""
    if not secret or not message or not provided_signature:
        return False
    expected = hmac_module.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac_module.compare_digest(expected, provided_signature.lower().strip())


def extract_hex_signature(raw_value: str) -> str:
    """Extract hex signature from header value like 'sha256=abc123'."""
    token = (raw_value or "").strip()
    if not token:
        return ""
    if "=" in token:
        _, token = token.split("=", 1)
    return token.strip().lower()


def assert_timestamp_fresh(timestamp_raw: str | int) -> int:
    """
    Validate that a webhook timestamp is within the accepted skew window.

    Raises HTTPException(401) if the timestamp is too old or too far in future.
    """
    try:
        ts = int(str(timestamp_raw or "").strip())
    except (ValueError, TypeError) as exc:
        raise HTTPException(401, "Missing or invalid webhook timestamp") from exc

    # Normalize milliseconds to seconds
    if ts > 10_000_000_000:
        ts = ts // 1000

    current_ts = int(datetime.now(timezone.utc).timestamp())
    if abs(current_ts - ts) > _WEBHOOK_MAX_SKEW_SECONDS:
        raise HTTPException(401, "Webhook timestamp is outside accepted window")

    return ts


async def check_replay(channel: str, fingerprint: str) -> None:
    """
    Check if a webhook event is a replay (duplicate delivery).

    Raises HTTPException(409) if the event was already processed.
    """
    cache = get_cache_client(namespace="webhook_replay")
    key = f"{channel}:{fingerprint}"
    if await cache.get_json(key):
        raise HTTPException(409, "Replay webhook request blocked")
    await cache.set_json(key, {"seen": True}, ttl_seconds=_WEBHOOK_REPLAY_TTL_SECONDS)


def build_replay_fingerprint(
    channel: str,
    *,
    signature: str = "",
    timestamp: str | int = "",
    raw_body: bytes = b"",
    tenant_id: str = "",
) -> str:
    """Build a deterministic fingerprint for replay detection."""
    source = f"{channel}:{tenant_id}:{signature}:{timestamp}".encode("utf-8") + raw_body
    return hashlib.sha256(source).hexdigest()


def client_ip_from_request(request: Request) -> str:
    """Extract the client IP from a request, respecting X-Forwarded-For."""
    forwarded = (request.headers.get("X-Forwarded-For") or "").strip()
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return (request.client.host if request.client else "").strip() or "unknown"
