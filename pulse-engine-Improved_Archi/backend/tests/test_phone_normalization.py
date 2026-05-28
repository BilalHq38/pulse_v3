"""Phone normalization: E.164, lookup candidates, and API strict validation."""
import os

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


def test_international_plus_accepts_common_upload_formatting() -> None:
    from core.phone_normalization import strict_normalize_to_e164_digits

    assert strict_normalize_to_e164_digits("  +92 (300) 123-4567  ") == "923001234567"


def test_spreadsheet_numeric_phone_text_is_cleaned_before_validation() -> None:
    from core.phone_normalization import strict_normalize_to_e164_digits

    assert strict_normalize_to_e164_digits("923001234567.0") == "923001234567"
    assert strict_normalize_to_e164_digits("9.23001234567E+11") == "923001234567"


def test_local_numbers_use_upload_country_code_mapping() -> None:
    from core.phone_normalization import phone_region_from_country_hint, strict_normalize_to_e164_digits
    from shared.tabular_uploads import phone_region_from_upload_row

    assert phone_region_from_upload_row({"country_code": "+92"}) == "PK"
    assert strict_normalize_to_e164_digits("300 1234567", fallback_region=phone_region_from_country_hint("+92")) == (
        "923001234567"
    )


def test_us_and_uk_local_upload_numbers_use_region_hints() -> None:
    from core.phone_normalization import phone_region_from_country_hint, strict_normalize_to_e164_digits

    us_region = phone_region_from_country_hint("+1")
    uk_region = phone_region_from_country_hint("GB")

    assert strict_normalize_to_e164_digits("(650) 253-0000", fallback_region=us_region) == "16502530000"
    assert strict_normalize_to_e164_digits("07911 123456", fallback_region=uk_region) == "447911123456"


def test_phone_lookup_unifies_national_and_e164_last10() -> None:
    """Local PK national vs same number as E.164 should share a candidate (last-10 overlap)."""
    from core.phone_normalization import phone_lookup_candidates

    a = phone_lookup_candidates("03001234567")
    b = phone_lookup_candidates("923001234567")
    assert a and b
    inter = set(a) & set(b)
    last10 = "3001234567"
    assert last10 in inter, (a, b)
