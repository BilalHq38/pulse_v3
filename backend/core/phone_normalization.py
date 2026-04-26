"""E.164 phone digits (no +) for CRM, WhatsApp, and messaging. Uses Google libphonenumber.

For multi-tenant: prefer storing ISO 3166-1 alpha-2 in ``company_settings.default_phone_region`` when
set (per-tenant). Until wired, use env ``WHATSAPP_DEFAULT_COUNTRY`` as a single global default for
parsing local national numbers. Best practice: store E.164 in the database.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

import phonenumbers
from phonenumbers import PhoneNumberFormat

logger = logging.getLogger(__name__)


def get_default_phone_region() -> Optional[str]:
    v = (os.environ.get("WHATSAPP_DEFAULT_COUNTRY") or "").strip().upper()
    if len(v) == 2 and v.isalpha():
        return v
    return None


def _parse_to_valid_e164_digits(raw: str, fallback_region: Optional[str]) -> Optional[str]:
    """If *raw* is a valid phone (any supported parse path), return E.164 digits without +. Else None."""
    if not (raw or "").strip():
        return None
    raw = raw.strip()
    region = fallback_region

    try:
        if raw.startswith("+"):
            parsed = phonenumbers.parse(raw, None)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, PhoneNumberFormat.E164).lstrip("+")
    except Exception as e:
        logger.debug("E.164 parse failed for %s: %s", raw, e)

    digits = re.sub(r"\D", "", raw)
    if not raw.startswith("+") and len(digits) >= 8:
        try:
            parsed = phonenumbers.parse("+" + digits, None)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, PhoneNumberFormat.E164).lstrip("+")
        except Exception as e:
            logger.debug("E.164 parse failed for +%s: %s", digits, e)

    if region:
        try:
            parsed = phonenumbers.parse(raw, region)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, PhoneNumberFormat.E164).lstrip("+")
        except Exception as e:
            logger.debug("Could not parse phone number %s: %s", raw, e)

    return None


def strict_normalize_to_e164_digits(
    raw: str,
    fallback_region: Optional[str] = None,
) -> Optional[str]:
    """
    Return E.164 digits (no +) only if libphonenumber marks the number valid.
    Returns None for empty/whitespace, or if no valid parse (API validation).
    """
    if not (raw or "").strip():
        return None
    r = fallback_region if fallback_region is not None else get_default_phone_region()
    return _parse_to_valid_e164_digits(raw, r)


def normalize_to_e164_digits(raw: str, fallback_region: Optional[str] = None) -> str:
    """Return E.164 without +, or digit-only fallback if parsing fails (messaging / inbound, does not block)."""
    r = fallback_region if fallback_region is not None else get_default_phone_region()
    v = _parse_to_valid_e164_digits(raw, r) if (raw or "").strip() else None
    if v:
        return v
    if not (raw or "").strip():
        return ""
    logger.warning(
        "Could not parse %s as valid international number, using raw digits",
        (raw or "").strip(),
    )
    return re.sub(r"\D", "", raw.strip())


def _last_n_digits(digits: str, n: int) -> str:
    d = re.sub(r"\D", "", digits or "")
    if len(d) >= n:
        return d[-n:]
    return d or ""


def phone_lookup_candidates(raw: str, fallback_region: Optional[str] = None) -> list[str]:
    """Possible stored `customers.phone` values: E.164 (loose), raw, last-10 legacy (collision-prone)."""
    if not (raw or "").strip():
        return []
    raw = raw.strip()
    e164 = normalize_to_e164_digits(raw, fallback_region=fallback_region)
    out: list[str] = []
    for c in (e164, raw):
        if c and c not in out:
            out.append(c)
    last10 = _last_n_digits(e164, 10) or _last_n_digits(re.sub(r"\D", "", raw), 10)
    if last10 and last10 not in out:
        out.append(last10)
    return out
