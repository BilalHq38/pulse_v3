"""core/utils.py — Pure helpers, PostgreSQL-compatible (no MongoDB)."""

import base64
import re
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse


def now_ts() -> datetime:
    """Return timezone-aware UTC datetime for PostgreSQL TIMESTAMPTZ columns."""
    return datetime.now(timezone.utc)


def now_iso() -> str:
    """ISO-8601 string — kept for legacy compatibility in some places."""
    return datetime.now(timezone.utc).isoformat()


def make_id() -> str:
    return str(uuid.uuid4())


def clean_doc(doc):
    if doc and isinstance(doc, dict):
        doc.pop("_id", None)
        doc.pop("password_hash", None)
    return doc


def clean_docs(docs):
    return [clean_doc(d) for d in docs]


def parse_dt(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except ValueError:
            return None
    return None


def seconds_until(value) -> int:
    parsed = parse_dt(value)
    if not parsed:
        return 0
    return max(0, int((parsed - datetime.now(timezone.utc)).total_seconds()))


def normalize_reference_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def validate_password(password: str) -> tuple:
    errors = []
    if len(password) < 8:
        errors.append("Password must be at least 8 characters long")
    if not re.search(r"[a-zA-Z]", password):
        errors.append("Password must contain at least one letter")
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?`~]", password):
        errors.append("Password must contain at least one special character")
    if not re.search(r"[0-9]", password):
        errors.append("Password must contain at least one number")
    return (len(errors) == 0, errors)


def format_response_minutes(avg_minutes: float) -> str:
    if not avg_minutes or avg_minutes <= 0:
        return "0m"
    if avg_minutes < 60:
        rounded = round(avg_minutes, 1)
        return f"{int(rounded)}m" if float(rounded).is_integer() else f"{rounded}m"
    hours = round(avg_minutes / 60, 1)
    return f"{int(hours)}h" if float(hours).is_integer() else f"{hours}h"


def sentiment_score_to_csat(avg_score: float, sample_size: int) -> float:
    if sample_size <= 0:
        return 0.0
    normalized = max(-1.0, min(1.0, avg_score))
    return round((((normalized + 1) / 2) * 4) + 1, 1)


EXCEL_ERROR_LITERALS = {"#value!", "#n/a", "#name?", "#null!", "#num!", "#ref!", "#div/0!"}
_BASE64_IMAGE_PAYLOAD_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


def is_valid_image_url(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered.startswith("data:image/"):
        try:
            header, payload = text.split(",", 1)
            if ";base64" not in header.lower():
                return False
            payload = payload.strip()
            if not payload or not _BASE64_IMAGE_PAYLOAD_RE.fullmatch(payload):
                return False
            raw = base64.b64decode(payload, validate=True)
            return 0 < len(raw) <= 8 * 1024 * 1024
        except Exception:
            return False
    parsed = urlparse(text)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and bool(parsed.path)


def normalize_product_images(images, limit=3) -> list:
    if not isinstance(images, list):
        return []
    result = []
    for raw in images:
        if raw is None:
            continue
        value = str(raw).strip()
        if not value or value.lower() in EXCEL_ERROR_LITERALS:
            continue
        if is_valid_image_url(value):
            result.append(value)
    return result[:limit]
