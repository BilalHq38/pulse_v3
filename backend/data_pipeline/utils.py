from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any

_NON_DIGIT_RE = re.compile(r"\D+")
_WHITESPACE_RE = re.compile(r"\s+")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stable_json_dumps(payload: Any) -> str:
    return json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)


def stable_json_hash(payload: Any) -> str:
    import hashlib

    return hashlib.sha256(stable_json_dumps(payload).encode("utf-8")).hexdigest()


def normalize_text(value: Any, *, max_length: int = 10000) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = _WHITESPACE_RE.sub(" ", text)
    return text[:max_length]


def normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_phone(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return _NON_DIGIT_RE.sub("", raw)


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return utc_now()
    if text.isdigit():
        parsed = int(text)
        if parsed > 10_000_000_000:
            parsed = parsed // 1000
        return datetime.fromtimestamp(parsed, tz=timezone.utc)
    normalized = text.replace("Z", "+00:00")
    try:
        parsed_dt = datetime.fromisoformat(normalized)
    except ValueError:
        return utc_now()
    return parsed_dt if parsed_dt.tzinfo else parsed_dt.replace(tzinfo=timezone.utc)


def metric_date_for(value: Any) -> date:
    return parse_timestamp(value).date()


def dedupe_key(*parts: Any) -> str:
    return stable_json_hash([str(part or "").strip() for part in parts])
