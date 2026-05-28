"""Wave 1 — public product pages and buy endpoint tests.

These tests cover the pure-Python helpers (bulk parser, slug generation,
request validation, in-memory rate limiter). End-to-end DB tests live in
test_wave1_public_buy_endpoint.py and require a Postgres instance.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from routers import products
from routers.public_products import PublicBuyRequest
from shared.utils.rate_limit import InMemoryRateLimiter


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_bulk_upload_with_links_column_parses_url():
    rows = [
        ["name", "price", "links", "image_url"],
        ["Starter", "29", "https://example.com/buy/starter", "https://example.com/s.jpg"],
    ]
    items, errors = products._parse_bulk_product_rows(rows)
    assert errors == []
    assert items[0]["payload"]["links"] == "https://example.com/buy/starter"


def test_bulk_upload_with_links_column_rejects_invalid_url():
    rows = [
        ["name", "price", "links"],
        ["Bad Link", "10", "not-a-url"],
    ]
    items, errors = products._parse_bulk_product_rows(rows)
    assert items == []
    assert errors[0]["row"] == 2
    assert "links" in errors[0]["error"].lower()


def test_bulk_upload_with_stock_quantity_column():
    rows = [
        ["name", "price", "stock_quantity"],
        ["Limited", "99", "5"],
        ["NoTracking", "10", ""],
        ["BadStock", "10", "-3"],
    ]
    items, errors = products._parse_bulk_product_rows(rows)
    assert len(items) == 2
    assert items[0]["payload"]["stock_quantity"] == 5
    assert "stock_quantity" not in items[1]["payload"]
    assert errors and errors[0]["row"] == 4


def test_bulk_upload_template_omits_links_column_is_still_accepted():
    rows = [
        ["name", "price"],
        ["Plain", "5"],
    ]
    items, errors = products._parse_bulk_product_rows(rows)
    assert errors == []
    assert "links" not in items[0]["payload"]


def test_auto_slug_generation_is_deterministic_and_url_safe():
    pid = "abc123def456"
    assert products._make_product_slug("Starter Plan", pid).startswith("starter-plan-")
    assert products._make_product_slug("Starter Plan", pid) == products._make_product_slug("Starter Plan", pid)
    assert products._make_product_slug("Starter Plan", pid).endswith(pid[:6])
    weird = products._make_product_slug("CRAZY!! Product Name ☆", pid)
    assert all(c.isalnum() or c in "-_" for c in weird)
    empty = products._make_product_slug("", pid)
    assert empty == f"product-{pid[:6]}"


def test_company_slug_generation_is_deterministic_and_short():
    cid = "00000000-0000-0000-0000-000000000123"
    slug = products._make_company_slug("Acme Industries", cid)
    assert slug.startswith("acme-industries-")
    suffix = slug.rsplit("-", 1)[-1]
    assert len(suffix) == 6
    assert all(c.isalnum() for c in suffix)


def test_normalize_link_returns_manual_when_url_supplied():
    assert products._normalize_link("https://example.com/x") == ("https://example.com/x", "manual")
    assert products._normalize_link("") == ("", "auto")
    assert products._normalize_link(None) == ("", "auto")


def test_normalize_link_rejects_non_http_scheme():
    with pytest.raises(Exception):
        products._normalize_link("javascript:alert(1)")
    with pytest.raises(Exception):
        products._normalize_link("ftp://example.com")


def test_public_buy_request_requires_phone_or_email():
    with pytest.raises(Exception):
        PublicBuyRequest.model_validate({"customer_name": "A", "quantity": 1})
    PublicBuyRequest.model_validate(
        {"customer_name": "A", "customer_phone": "+15550100", "quantity": 1}
    )
    PublicBuyRequest.model_validate(
        {"customer_name": "A", "customer_email": "a@example.com", "quantity": 1, "client_request_id": "abcdef1234567890"}
    )


def test_public_buy_request_anonymous_requires_client_request_id():
    with pytest.raises(Exception):
        PublicBuyRequest.model_validate(
            {"customer_name": "A", "customer_email": "a@example.com", "quantity": 1}
        )
    with pytest.raises(Exception):
        PublicBuyRequest.model_validate(
            {"customer_name": "A", "customer_email": "a@example.com", "quantity": 1, "client_request_id": "short"}
        )


def test_public_buy_request_enforces_quantity_bounds():
    with pytest.raises(Exception):
        PublicBuyRequest.model_validate(
            {"customer_name": "A", "customer_phone": "+15550100", "quantity": 0}
        )
    with pytest.raises(Exception):
        PublicBuyRequest.model_validate(
            {"customer_name": "A", "customer_phone": "+15550100", "quantity": 1000}
        )


def test_in_memory_rate_limiter_blocks_after_limit():
    async def _run():
        limiter = InMemoryRateLimiter(max_requests=5, window_seconds=60)
        results = []
        for _ in range(7):
            allowed, retry = await limiter.allow("1.2.3.4")
            results.append((allowed, retry))
        return results

    results = asyncio.run(_run())
    assert sum(1 for ok, _ in results if ok) == 5
    blocked = [r for r in results if not r[0]]
    assert len(blocked) == 2
    assert all(retry >= 1 for _, retry in blocked)


def test_migration_015_adds_required_columns_and_indexes():
    migration = (REPO_ROOT / "backend" / "sql_migrations" / "015_product_pages.sql").read_text()
    schema = (REPO_ROOT / "backend" / "sql_schema.sql").read_text()
    for needed in (
        "ADD COLUMN IF NOT EXISTS links",
        "ADD COLUMN IF NOT EXISTS slug",
        "ADD COLUMN IF NOT EXISTS public_page_enabled",
        "ADD COLUMN IF NOT EXISTS stock_quantity",
        "ADD COLUMN IF NOT EXISTS idempotency_key",
        "uq_company_products_company_slug",
        "uq_orders_public_dedupe",
        "uq_orders_idempotency_key",
    ):
        assert needed in migration, f"migration missing: {needed}"
    for needed in (
        "uq_companies_slug",
        "uq_company_products_company_slug",
        "uq_orders_public_dedupe",
        "uq_orders_idempotency_key",
        "'pending'",
    ):
        assert needed in schema, f"schema missing: {needed}"
