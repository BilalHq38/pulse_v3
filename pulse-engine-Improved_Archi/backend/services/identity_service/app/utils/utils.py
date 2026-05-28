import json
import math
import re
import uuid
from difflib import SequenceMatcher
from datetime import datetime, timezone
from typing import Any

try:
    import jellyfish
except Exception:  # pragma: no cover - optional dependency fallback
    jellyfish = None
try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - optional dependency fallback
    fuzz = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", value.strip().lower())
    return normalized or None


def normalize_phone(value: str | None) -> str | None:
    """Normalize a phone number to a canonical digits-only form.

    Strategy: extract all digits, then take the **last 10 significant digits**.
    This makes ``+923001234567``, ``923001234567``, ``03001234567``, and
    ``3001234567`` all normalize to the same 10-digit string ``3001234567``,
    enabling cross-format matching within a tenant without requiring a default
    country code.

    Edge case: numbers shorter than 10 digits are kept as-is (e.g. some
    short-codes or test numbers).
    """
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if not digits:
        return None
    # Take last 10 significant digits for subscriber-number matching
    return digits[-10:] if len(digits) >= 10 else digits


def phone_variants(value: str | None) -> list[str]:
    """Return all plausible phone string variants for a DB lookup.

    Returns the raw value, the normalized (last-10-digits) form, and common
    prefix variants so that legacy records stored in different formats still
    match.
    """
    if not value:
        return []
    raw = value.strip()
    normalized = normalize_phone(raw)
    seen: set[str] = set()
    variants: list[str] = []
    for candidate in (raw, normalized):
        if candidate and candidate not in seen:
            variants.append(candidate)
            seen.add(candidate)
    if normalized:
        # Local trunk form: 0 + normalized
        trunk = "0" + normalized
        if trunk not in seen:
            variants.append(trunk)
            seen.add(trunk)
        # E.164-ish with leading digits (12-digit form common in South Asia)
        if len(normalized) == 10:
            intl = "92" + normalized  # Pakistan default; generic enough for common CRM usage
            if intl not in seen:
                variants.append(intl)
                seen.add(intl)
    return variants


def normalize_email(value: str | None) -> str | None:
    """Normalize an email address.

    * Lowercase and strip whitespace.
    * Strip plus-addressing (``user+tag@domain.com`` → ``user@domain.com``).
    * For Gmail/Googlemail domains, remove dots from the local part
      (``john.doe@gmail.com`` → ``johndoe@gmail.com``).
    """
    if not value:
        return None
    email = value.strip().lower()
    if "@" not in email:
        return email or None
    local, _, domain = email.partition("@")
    if not local or not domain:
        return email or None
    # Strip plus-addressing
    local = local.split("+")[0]
    # Gmail dot normalization
    if domain in ("gmail.com", "googlemail.com"):
        local = local.replace(".", "")
    return f"{local}@{domain}" if local else None


def serialize_any(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): serialize_any(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize_any(item) for item in value]
    return value


def json_dumps(value: Any) -> str:
    return json.dumps(serialize_any(value), sort_keys=True, separators=(",", ":"))


def build_fingerprint_hash(signals: dict[str, Any]) -> str | None:
    if not signals:
        return None
    normalized = {str(k): str(v).strip().lower() for k, v in sorted(signals.items()) if v not in (None, "", [])}
    if not normalized:
        return None
    payload = json_dumps(normalized)
    import hashlib

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def partial_signal_match_ratio(left: dict[str, Any], right: dict[str, Any]) -> tuple[float, list[str]]:
    if not left or not right:
        return 0.0, []
    matched_keys: list[str] = []
    comparable_keys = sorted(set(left.keys()) & set(right.keys()))
    if not comparable_keys:
        return 0.0, []
    for key in comparable_keys:
        if str(left.get(key)).strip().lower() == str(right.get(key)).strip().lower():
            matched_keys.append(key)
    return len(matched_keys) / len(comparable_keys), matched_keys


def fuzzy_name_similarity(left: str | None, right: str | None) -> float:
    left_n = normalize_text(left)
    right_n = normalize_text(right)
    if not left_n or not right_n:
        return 0.0
    if jellyfish is not None:
        jw = jellyfish.jaro_winkler_similarity(left_n, right_n)
    else:
        jw = SequenceMatcher(None, left_n, right_n).ratio()
    if fuzz is not None:
        lev = fuzz.ratio(left_n, right_n) / 100.0
    else:
        lev = SequenceMatcher(None, left_n, right_n).ratio()
    return (jw + lev) / 2.0


def username_similarity(left: str | None, right: str | None) -> float:
    left_n = normalize_text(left)
    right_n = normalize_text(right)
    if not left_n or not right_n:
        return 0.0
    if fuzz is not None:
        return fuzz.ratio(left_n, right_n) / 100.0
    return SequenceMatcher(None, left_n, right_n).ratio()


def profile_picture_distance(left: str | None, right: str | None) -> int | None:
    if not left or not right:
        return None
    try:
        left_int = int(left, 16) if any(c.isalpha() for c in left.lower()) else int(left)
        right_int = int(right, 16) if any(c.isalpha() for c in right.lower()) else int(right)
    except ValueError:
        return None
    return (left_int ^ right_int).bit_count()


def ip_subnet(ip_address: str | None) -> str | None:
    if not ip_address or "." not in ip_address:
        return None
    chunks = ip_address.split(".")
    if len(chunks) != 4:
        return None
    return ".".join(chunks[:3])


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_mag = math.sqrt(sum(a * a for a in left))
    right_mag = math.sqrt(sum(b * b for b in right))
    if not left_mag or not right_mag:
        return 0.0
    return dot / (left_mag * right_mag)


def typing_speed_within_range(current: float | None, baseline: float | None) -> bool:
    if current is None or baseline is None or baseline == 0:
        return False
    return abs(current - baseline) / baseline <= 0.15


def build_embedding_text(payload: dict[str, Any]) -> str:
    fragments: list[str] = []
    for key in ["full_name", "username", "language", "locale", "cookie_id"]:
        value = payload.get(key)
        if value:
            fragments.append(str(value))
    messages = payload.get("message_patterns") or []
    if messages:
        fragments.append(" ".join(messages[:10]))
    session_timing = payload.get("session_timing") or []
    if session_timing:
        fragments.append("session:" + ",".join(str(round(item, 3)) for item in session_timing[:12]))
    if payload.get("typing_speed") is not None:
        fragments.append(f"typing:{payload['typing_speed']}")
    return " | ".join(fragments).strip()


def vector_to_pg_literal(vector: list[float] | None) -> str | None:
    if not vector:
        return None
    return "[" + ",".join(f"{float(item):.8f}" for item in vector) + "]"


def score_to_confidence(score: float, deterministic: bool = False) -> float:
    if deterministic:
        return 0.999999
    return round(min(0.9999, max(0.05, score / 1000.0)), 6)


def utc_now_iso() -> str:
    return utcnow().isoformat()


def generate_request_id() -> str:
    return str(uuid.uuid4())
