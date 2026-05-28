"""Signed, opaque product-link reference tokens.

A ref token encodes (customer_id, session_id, company_id, expiry) in a
URL-safe string and authenticates it with HMAC-SHA256.  The customer sees
only an opaque base64 blob — no internal IDs are exposed.

Usage
-----
    from shared.product_ref_token import create_ref_token, decode_ref_token

    # When building a product URL (conversation engine):
    token = create_ref_token(customer_id="...", session_id="...", company_id="...")
    url = f"{base_url}?ref={token}"

    # When processing a buy request (public_products router):
    payload = decode_ref_token(token, expected_company_id=company_id)
    if payload:
        customer_id = payload["customer_id"]
        session_id  = payload["session_id"]
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time


# Re-use the existing JWT secret so no new env-var is needed.
_SECRET: bytes = (
    os.environ.get("JWT_SECRET", "")
    or os.environ.get("INTERNAL_SERVICE_SECRET", "")
    or "fallback-product-ref-secret-change-me"
).encode()

# Tokens are valid for 7 days — long enough for a customer to return to
# a product page they bookmarked, short enough to limit replay exposure.
_TTL_SECONDS = 7 * 24 * 3600


def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64_decode(s: str) -> bytes:
    # Restore padding before decoding.
    remainder = len(s) % 4
    if remainder:
        s += "=" * (4 - remainder)
    return base64.urlsafe_b64decode(s)


def _sign(payload_b64: str) -> str:
    return hmac.new(_SECRET, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()[:24]


def create_ref_token(*, customer_id: str, session_id: str, company_id: str) -> str:
    """Return a URL-safe opaque token that encodes the given IDs."""
    payload = json.dumps(
        {
            "c": customer_id,
            "s": session_id,
            "k": company_id,
            "e": int(time.time()) + _TTL_SECONDS,
        },
        separators=(",", ":"),
    )
    payload_b64 = _b64_encode(payload.encode("utf-8"))
    sig = _sign(payload_b64)
    return f"{payload_b64}.{sig}"


def decode_ref_token(token: str, *, expected_company_id: str = "") -> dict | None:
    """Verify and decode a ref token.

    Returns a dict with ``customer_id``, ``session_id``, ``company_id``
    on success, or ``None`` if the token is invalid, expired, or belongs
    to a different company.
    """
    if not token:
        return None
    try:
        parts = token.split(".", 1)
        if len(parts) != 2:
            return None
        payload_b64, sig = parts
        expected_sig = _sign(payload_b64)
        if not hmac.compare_digest(sig, expected_sig):
            return None
        payload = json.loads(_b64_decode(payload_b64).decode("utf-8"))
        if int(time.time()) > int(payload.get("e", 0)):
            return None
        company_id = str(payload.get("k") or "")
        if expected_company_id and company_id and company_id != expected_company_id:
            return None
        return {
            "customer_id": str(payload.get("c") or ""),
            "session_id": str(payload.get("s") or ""),
            "company_id": company_id,
        }
    except Exception:
        return None
