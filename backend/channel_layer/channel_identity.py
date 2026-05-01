"""Channel identity normalization and outbound recipient resolution."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from email.utils import parseaddr
from typing import Any

import phonenumbers
from phonenumbers import PhoneNumberFormat

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DIGITS_RE = re.compile(r"\D+")


@dataclass(frozen=True)
class NormalizedIdentity:
    raw_value: str
    canonical_value: str
    provider_id: str
    is_valid: bool
    reason: str
    channel: str
    confidence: float = 0.0


def _region(value: str | None) -> str | None:
    cleaned = str(value or "").strip().upper()
    return cleaned if len(cleaned) == 2 and cleaned.isalpha() else None


async def company_default_phone_region(db: Any, company_id: str) -> str:
    scoped_company_id = str(company_id or "").strip()
    if not db or not scoped_company_id:
        return ""
    try:
        row = await db.fetchrow(
            "SELECT default_phone_region FROM company_settings WHERE company_id=$1 LIMIT 1",
            scoped_company_id,
        )
    except Exception as exc:
        logger.debug("Failed to load company default phone region company_id=%s: %s", scoped_company_id, exc)
        return ""
    if not row:
        return ""
    try:
        return _region(dict(row).get("default_phone_region")) or ""
    except Exception:
        return ""


def _canonical_e164(raw_value: str, default_region: str | None = None) -> tuple[str, str, float]:
    raw = str(raw_value or "").strip()
    if not raw:
        return "", "empty", 0.0

    # whatsapp-web.js commonly uses 923001234567@c.us; Meta Cloud uses bare digits.
    compact = raw.replace("@c.us", "").replace("@s.whatsapp.net", "").strip()
    if compact.lower().startswith("wamid.") or "@" in compact and not compact.endswith(("@c.us", "@s.whatsapp.net")):
        return "", "provider_or_non_phone_identifier", 0.0

    digits = _DIGITS_RE.sub("", compact)
    if not digits:
        return "", "no_digits", 0.0
    if len(digits) < 8:
        return "", "too_short", 0.0
    if len(digits) > 15:
        return "", "too_long_for_e164", 0.0

    candidates: list[tuple[str, str | None, float]] = []
    if compact.startswith("+"):
        candidates.append((compact, None, 1.0))
    else:
        candidates.append(("+" + digits, None, 0.95))
        region = _region(default_region)
        if region:
            candidates.append((compact, region, 0.9))

    last_error = "invalid_phone"
    for candidate, region, confidence in candidates:
        try:
            parsed = phonenumbers.parse(candidate, region)
        except Exception as exc:
            last_error = f"parse_failed:{exc.__class__.__name__}"
            continue
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, PhoneNumberFormat.E164), "", confidence
        last_error = "not_valid_number"
    return "", last_error, 0.0


def normalize_whatsapp_phone(raw_value: str, default_region: str | None = None) -> NormalizedIdentity:
    raw = str(raw_value or "").strip()
    canonical, reason, confidence = _canonical_e164(raw, default_region)
    return NormalizedIdentity(
        raw_value=raw,
        canonical_value=canonical,
        provider_id=raw if raw and not canonical else "",
        is_valid=bool(canonical),
        reason=reason,
        channel="whatsapp",
        confidence=confidence,
    )


def is_valid_whatsapp_recipient(value: str, default_region: str | None = None) -> bool:
    return normalize_whatsapp_phone(value, default_region=default_region).is_valid


def normalize_email(raw_value: str) -> NormalizedIdentity:
    raw = str(raw_value or "").strip()
    _, addr = parseaddr(raw)
    canonical = addr.strip().lower()
    valid = bool(canonical and _EMAIL_RE.match(canonical))
    return NormalizedIdentity(
        raw_value=raw,
        canonical_value=canonical if valid else "",
        provider_id="",
        is_valid=valid,
        reason="" if valid else "invalid_email",
        channel="email",
        confidence=1.0 if valid else 0.0,
    )


def normalize_social_channel_id(raw_value: str, provider: str) -> NormalizedIdentity:
    raw = str(raw_value or "").strip()
    channel = str(provider or "").strip().lower()
    if channel not in {"facebook", "instagram", "web_chat"}:
        return NormalizedIdentity(raw, "", "", False, "unsupported_social_provider", channel, 0.0)
    if not raw:
        return NormalizedIdentity(raw, "", "", False, "empty", channel, 0.0)
    if "@" in raw:
        return NormalizedIdentity(raw, "", raw, False, "email_not_social_id", channel, 0.0)
    return NormalizedIdentity(raw, raw, raw, True, "", channel, 0.95)


def resolve_outbound_recipient(
    channel: str,
    conversation: dict[str, Any],
    customer: dict[str, Any],
    *,
    default_region: str | None = None,
    sender_contact: str = "",
) -> NormalizedIdentity:
    normalized = str(channel or "").strip().lower()
    conversation_channel_id = str(
        conversation.get("channel_id") or conversation.get("session_id") or ""
    ).strip()
    if normalized == "whatsapp":
        for raw in (customer.get("phone"), conversation_channel_id, sender_contact):
            identity = normalize_whatsapp_phone(str(raw or ""), default_region=default_region)
            if identity.is_valid:
                return identity
        return NormalizedIdentity(
            raw_value=str(conversation_channel_id or customer.get("phone") or sender_contact or "").strip(),
            canonical_value="",
            provider_id="",
            is_valid=False,
            reason="missing_valid_whatsapp_phone",
            channel="whatsapp",
            confidence=0.0,
        )
    if normalized == "email":
        for raw in (customer.get("email"), conversation_channel_id, sender_contact):
            identity = normalize_email(str(raw or ""))
            if identity.is_valid:
                return identity
        return NormalizedIdentity("", "", "", False, "missing_valid_email", "email", 0.0)
    if normalized in {"facebook", "instagram"}:
        for raw in (conversation_channel_id, sender_contact, customer.get("channel_profile_id")):
            identity = normalize_social_channel_id(str(raw or ""), normalized)
            if identity.is_valid:
                return identity
        return NormalizedIdentity("", "", "", False, "missing_channel_id", normalized, 0.0)
    if normalized == "web_chat":
        for raw in (conversation_channel_id, sender_contact, customer.get("channel_profile_id"), customer.get("id")):
            identity = normalize_social_channel_id(str(raw or ""), normalized)
            if identity.is_valid:
                return identity
        return NormalizedIdentity("", "", "", False, "missing_channel_id", normalized, 0.0)
    return NormalizedIdentity("", "", "", False, "unsupported_channel", normalized, 0.0)
