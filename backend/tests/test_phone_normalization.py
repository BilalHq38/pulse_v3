"""Phone normalization: E.164, lookup candidates, and API strict validation."""
import os

import pytest

# Ensure known region for national-format tests
os.environ["WHATSAPP_DEFAULT_COUNTRY"] = "PK"


def test_strict_rejects_garbage() -> None:
    from core.phone_normalization import strict_normalize_to_e164_digits

    assert strict_normalize_to_e164_digits("abc123") is None
    assert strict_normalize_to_e164_digits("") is None
    assert strict_normalize_to_e164_digits("   ") is None


def test_local_pk_to_e164() -> None:
    from core.phone_normalization import strict_normalize_to_e164_digits

    assert strict_normalize_to_e164_digits("03001234567") == "923001234567"


def test_international_plus() -> None:
    from core.phone_normalization import strict_normalize_to_e164_digits

    assert strict_normalize_to_e164_digits("+44 7911 123456") == "447911123456"


def test_phone_lookup_unifies_national_and_e164_last10() -> None:
    """Local PK national vs same number as E.164 should share a candidate (last-10 overlap)."""
    from core.phone_normalization import phone_lookup_candidates

    a = phone_lookup_candidates("03001234567")
    b = phone_lookup_candidates("923001234567")
    assert a and b
    inter = set(a) & set(b)
    last10 = "3001234567"
    assert last10 in inter, (a, b)
