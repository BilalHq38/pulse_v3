"""E.164 phone digits (no +) for CRM, WhatsApp, and messaging. Uses Google libphonenumber.

For multi-tenant: prefer storing ISO 3166-1 alpha-2 in ``company_settings.default_phone_region`` when
set (per-tenant). Until wired, use env ``WHATSAPP_DEFAULT_COUNTRY`` as a single global default for
parsing local national numbers. Best practice: store E.164 in the database.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Optional

import phonenumbers
from phonenumbers import PhoneNumberFormat

logger = logging.getLogger(__name__)

_ZERO_DECIMAL_PHONE_RE = re.compile(r"^(\+?\d+)\.0+$")
_SCIENTIFIC_PHONE_RE = re.compile(r"^\+?\d+(?:\.\d+)?[eE][+-]?\d+$")
_COUNTRY_NAME_REGION_ALIASES = {
    "america": "US",
    "england": "GB",
    "great britain": "GB",
    "pakistan": "PK",
    "uk": "GB",
    "united kingdom": "GB",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
}


def get_default_phone_region() -> Optional[str]:
    v = (os.environ.get("WHATSAPP_DEFAULT_COUNTRY") or "").strip().upper()
    if len(v) == 2 and v.isalpha():
        return v
    return None


def _clean_phone_text(raw: str) -> str:
    text = unicodedata.normalize("NFKC", str(raw or ""))
    text = (
        text.replace("\u00a0", " ")
        .replace("\u2007", " ")
        .replace("\u202f", " ")
        .replace("\u200b", "")
        .replace("\ufeff", "")
    )
    text = text.strip().strip("'\"")
    compact = re.sub(r"\s+", "", text)

    zero_decimal = _ZERO_DECIMAL_PHONE_RE.match(compact)
    if zero_decimal:
        return zero_decimal.group(1)

    if _SCIENTIFIC_PHONE_RE.match(compact):
        try:
            value = Decimal(compact)
        except InvalidOperation:
            return text
        integral = value.to_integral_value()
        if value == integral:
            return format(integral, "f")

    return text


def phone_region_from_country_hint(*hints: str) -> Optional[str]:
    """Return an ISO alpha-2 region from upload hints such as PK, +92, or United States."""
    for hint in hints:
        cleaned = _clean_phone_text(str(hint or ""))
        if not cleaned:
            continue

        alpha = re.sub(r"[^A-Za-z]", "", cleaned).upper()
        if len(alpha) == 2 and phonenumbers.country_code_for_region(alpha):
            return alpha

        alias_key = re.sub(r"[^a-z]+", " ", cleaned.lower()).strip()
        alias_region = _COUNTRY_NAME_REGION_ALIASES.get(alias_key)
        if alias_region:
            return alias_region

        digits = re.sub(r"\D", "", cleaned)
        if not digits or len(digits) > 3:
            continue
        region = phonenumbers.region_code_for_country_code(int(digits))
        if region and len(region) == 2 and region.isalpha():
            return region

    return None


def _parse_to_valid_e164_digits(raw: str, fallback_region: Optional[str]) -> Optional[str]:
    """If *raw* is a valid phone (any supported parse path), return E.164 digits without +. Else None."""
    if not (raw or "").strip():
        return None
    raw = _clean_phone_text(raw)
    region = fallback_region

    try:
        if raw.startswith("+"):
            parsed = phonenumbers.parse(raw, None)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, PhoneNumberFormat.E164).lstrip("+")
    except Exception as e:
        logger.debug("E.164 parse failed for %s: %s", raw, e)

    digits = re.sub(r"\D", "", raw)
    if raw.startswith("00") and len(digits) >= 10:
        try:
            parsed = phonenumbers.parse("+" + digits[2:], None)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, PhoneNumberFormat.E164).lstrip("+")
        except Exception as e:
            logger.debug("E.164 parse failed for +%s: %s", digits[2:], e)

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
    cleaned = _clean_phone_text(raw)
    logger.warning(
        "Could not parse %s as valid international number, using raw digits",
        cleaned,
    )
    return re.sub(r"\D", "", cleaned)


def _last_n_digits(digits: str, n: int) -> str:
    d = re.sub(r"\D", "", digits or "")
    if len(d) >= n:
        return d[-n:]
    return d or ""


def phone_lookup_candidates(raw: str, fallback_region: Optional[str] = None) -> list[str]:
    """Possible stored `customers.phone` values: E.164 (loose), raw, last-10 legacy (collision-prone)."""
    if not (raw or "").strip():
        return []
    raw = _clean_phone_text(raw)
    e164 = normalize_to_e164_digits(raw, fallback_region=fallback_region)
    out: list[str] = []
    for c in (e164, raw):
        if c and c not in out:
            out.append(c)
    last10 = _last_n_digits(e164, 10) or _last_n_digits(re.sub(r"\D", "", raw), 10)
    if last10 and last10 not in out:
        out.append(last10)
    return out
