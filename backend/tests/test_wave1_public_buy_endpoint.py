"""Wave 1 — public buy endpoint integration tests.

Mounts the public_products router on a bare FastAPI app and stubs
platform_admin_context with a FakeConn that records SQL and returns canned
rows. This lets us exercise the rate limiter, stock-check transaction,
duplicate guard, and availability cache without a real Postgres.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import public_products
from shared import rate_limit as rate_limit_module
from shared.utils.rate_limit import InMemoryRateLimiter


class FakeUniqueViolation(asyncpg.UniqueViolationError):
    pass


class FakeConn:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.executed: list[tuple[str, tuple]] = []

    async def fetchrow(self, query: str, *args):
        self.executed.append((query, args))
        q = " ".join(query.split())
        if "FROM companies WHERE slug" in q:
            company = self.state.get("company")
            if company and company["slug"] == args[0]:
                return {"id": company["id"]}
            return None
        if "FROM company_products p" in q and "p.slug = $2" in q:
            product = self.state.get("product")
            if product and product["company_id"] == args[0] and product["slug"] == args[1]:
                return {
                    "id": product["id"],
                    "company_id": product["company_id"],
                    "name": product["name"],
                    "product_title": "",
                    "description": product.get("description", ""),
                    "price": product.get("price", ""),
                    "price_currency": "USD",
                    "category": "general",
                    "product_type": "standard",
                    "status": product.get("status", "active"),
                    "links": "",
                    "link_source": "auto",
                    "slug": product["slug"],
                    "public_page_enabled": product.get("public_page_enabled", True),
                    "stock_quantity": product.get("stock_quantity"),
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "images": "[]",
                    "features": "[]",
                }
            return None
        if "stock_quantity, status FROM company_products" in q:
            product = self.state.get("product")
            if product and product["slug"] == args[1] and product.get("public_page_enabled", True):
                return {"stock_quantity": product.get("stock_quantity"), "status": product.get("status", "active")}
            return None
        if "FROM company_products" in q and "FOR UPDATE" in q:
            product = self.state.get("product")
            if product and product["company_id"] == args[0] and product["slug"] == args[1]:
                return {
                    "id": product["id"],
                    "name": product["name"],
                    "stock_quantity": product.get("stock_quantity"),
                    "status": product.get("status", "active"),
                    "public_page_enabled": product.get("public_page_enabled", True),
                    "price": product.get("price"),
                    "price_currency": "USD",
                }
            return None
        if "FROM companies WHERE id" in q:
            company = self.state.get("company")
            if company and company["id"] == args[0]:
                return {"id": company["id"], "name": company["name"], "slug": company["slug"]}
            return None
        if "FROM company_settings WHERE company_id" in q:
            return None
        if "FROM orders" in q and "ORDER BY created_at DESC" in q:
            return self.state.get("dedupe_lookup")
        return None

    async def execute(self, query: str, *args):
        self.executed.append((query, args))
        q = " ".join(query.split())
        if "INSERT INTO orders" in q:
            if self.state.get("simulate_unique_violation"):
                raise FakeUniqueViolation("uq_orders_idempotency_key")
            self.state.setdefault("orders", []).append({"id": args[0], "company_id": args[1], "args": args})
        elif "UPDATE company_products SET stock_quantity" in q:
            product = self.state.get("product")
            if product:
                product["stock_quantity"] = (product.get("stock_quantity") or 0) - args[0]
                self.state.setdefault("stock_updates", []).append(args[0])
        return "OK"

    def transaction(self):
        @contextlib.asynccontextmanager
        async def _tx():
            yield
        return _tx()


class FakeDb:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state


def _app_with_state(state: dict[str, Any]) -> FastAPI:
    app = FastAPI()
    app.state.db = FakeDb(state)

    @contextlib.asynccontextmanager
    async def _ctx(_db):
        conn = FakeConn(state)
        state.setdefault("conns", []).append(conn)
        yield conn

    public_products.platform_admin_context = _ctx
    app.include_router(public_products.router, prefix="/api")
    return app


def _seed_state(stock_quantity: int | None = 5) -> dict[str, Any]:
    return {
        "company": {"id": "co-1", "slug": "acme", "name": "Acme Inc"},
        "product": {
            "id": "prod-1",
            "company_id": "co-1",
            "slug": "starter-abc123",
            "name": "Starter",
            "description": "Starter description",
            "price": "29",
            "status": "active",
            "public_page_enabled": True,
            "stock_quantity": stock_quantity,
        },
    }


@pytest.fixture(autouse=True)
def _reset_limiters():
    rate_limit_module._LIMITERS.clear()
    public_products.get_cache_client(namespace=public_products.AVAILABILITY_CACHE_NAMESPACE)
    yield
    rate_limit_module._LIMITERS.clear()


def test_get_public_product_returns_payload_for_active_listing():
    state = _seed_state()
    client = TestClient(_app_with_state(state))
    resp = client.get("/api/public/companies/acme/products/starter-abc123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["product"]["name"] == "Starter"
    assert body["company"]["slug"] == "acme"


def test_get_public_product_returns_404_when_company_unknown():
    state = _seed_state()
    client = TestClient(_app_with_state(state))
    resp = client.get("/api/public/companies/unknown/products/whatever")
    assert resp.status_code == 404


def test_availability_endpoint_returns_stock_and_caches():
    state = _seed_state(stock_quantity=7)
    client = TestClient(_app_with_state(state))
    r1 = client.get("/api/public/companies/acme/products/starter-abc123/availability")
    assert r1.status_code == 200
    assert r1.json()["stock_quantity"] == 7
    # Mutating the underlying state must not bleed through within the cache TTL.
    state["product"]["stock_quantity"] = 0
    r2 = client.get("/api/public/companies/acme/products/starter-abc123/availability")
    assert r2.json()["stock_quantity"] == 7  # still cached
    # Manually purge the cache and confirm fresh value.
    asyncio.run(
        public_products.get_cache_client(namespace=public_products.AVAILABILITY_CACHE_NAMESPACE).delete("acme:starter-abc123")
    )
    r3 = client.get("/api/public/companies/acme/products/starter-abc123/availability")
    assert r3.json()["stock_quantity"] == 0


def test_public_buy_creates_pending_order_and_decrements_stock():
    state = _seed_state(stock_quantity=3)
    client = TestClient(_app_with_state(state))
    body = {"customer_name": "Jane", "customer_phone": "+15550199", "quantity": 2}
    resp = client.post("/api/public/companies/acme/products/starter-abc123/buy", json=body)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["status"] == "pending"
    assert payload["order_ref"].startswith("ORD-")
    assert payload["order_reference"] == payload["order_ref"]
    assert payload["product_name"] == "Starter"
    assert payload["quantity"] == 2
    assert payload["deduplicated"] is False
    assert state["product"]["stock_quantity"] == 1
    assert len(state["orders"]) == 1


def test_public_buy_returns_409_when_insufficient_stock():
    state = _seed_state(stock_quantity=1)
    client = TestClient(_app_with_state(state))
    body = {"customer_name": "Jane", "customer_phone": "+15550199", "quantity": 5}
    resp = client.post("/api/public/companies/acme/products/starter-abc123/buy", json=body)
    assert resp.status_code == 409
    assert resp.json()["detail"] == "insufficient_stock"
    assert "orders" not in state or state.get("orders") == []


def test_public_buy_accepts_null_stock_meaning_unlimited():
    state = _seed_state(stock_quantity=None)
    client = TestClient(_app_with_state(state))
    body = {"customer_name": "Jane", "customer_phone": "+15550199", "quantity": 99}
    resp = client.post("/api/public/companies/acme/products/starter-abc123/buy", json=body)
    assert resp.status_code == 200
    assert state["product"]["stock_quantity"] is None


def test_public_buy_requires_phone_or_email():
    state = _seed_state()
    client = TestClient(_app_with_state(state))
    resp = client.post(
        "/api/public/companies/acme/products/starter-abc123/buy",
        json={"customer_name": "Jane", "quantity": 1},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "contact_required"


def test_public_buy_anonymous_requires_client_request_id():
    state = _seed_state()
    client = TestClient(_app_with_state(state))
    resp = client.post(
        "/api/public/companies/acme/products/starter-abc123/buy",
        json={"customer_name": "Jane", "customer_email": "j@example.com", "quantity": 1},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "client_request_id_required_for_anonymous_buy"


def test_public_buy_anonymous_with_client_request_id_succeeds():
    state = _seed_state()
    client = TestClient(_app_with_state(state))
    resp = client.post(
        "/api/public/companies/acme/products/starter-abc123/buy",
        json={
            "customer_name": "Jane",
            "customer_email": "j@example.com",
            "quantity": 1,
            "client_request_id": "abcdef1234567890beef",
        },
    )
    assert resp.status_code == 200


def test_public_buy_dedupes_on_unique_violation_and_returns_existing():
    state = _seed_state()
    state["simulate_unique_violation"] = True
    state["dedupe_lookup"] = {"id": "existing-order-id", "status": "pending"}
    client = TestClient(_app_with_state(state))
    resp = client.post(
        "/api/public/companies/acme/products/starter-abc123/buy",
        json={"customer_name": "Jane", "customer_phone": "+15550100", "quantity": 1},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["order_id"] == "existing-order-id"
    assert body["order_ref"] == "ORD-EXISTI"
    assert body["order_reference"] == body["order_ref"]
    assert body["deduplicated"] is True
    assert state["product"]["stock_quantity"] == 5
    assert "orders" not in state


def test_public_buy_disabled_product_returns_404():
    state = _seed_state()
    state["product"]["public_page_enabled"] = False
    client = TestClient(_app_with_state(state))
    # Buy must reject even though we can resolve the product row.
    resp = client.post(
        "/api/public/companies/acme/products/starter-abc123/buy",
        json={"customer_name": "Jane", "customer_phone": "+15550100", "quantity": 1},
    )
    assert resp.status_code == 404


def test_rate_limiter_returns_429_with_retry_after_after_5_in_60s():
    state = _seed_state()
    client = TestClient(_app_with_state(state))
    body = {"customer_name": "Jane", "customer_phone": "+15550100", "quantity": 1}
    statuses = []
    for i in range(6):
        # Vary the request id so the per-row dedupe index does not get triggered.
        body["client_request_id"] = f"req-{i:020d}"
        resp = client.post("/api/public/companies/acme/products/starter-abc123/buy", json=body)
        statuses.append(resp.status_code)
    assert statuses[:5].count(200) == 5
    assert statuses[5] == 429


def test_concurrent_buys_against_single_stock_only_one_wins():
    # The router transaction uses FOR UPDATE; here we simulate that by serializing
    # via the FakeConn (single-threaded asyncio), then assert exactly one buy
    # succeeds when stock=1 against 10 concurrent attempts.
    state = _seed_state(stock_quantity=1)
    client = TestClient(_app_with_state(state))

    async def _attempt(idx: int) -> int:
        body = {
            "customer_name": f"Buyer-{idx}",
            "customer_phone": "+15550100",
            "quantity": 1,
            "client_request_id": f"concurrent-{idx:020d}",
        }
        # TestClient is sync; offload to thread to actually concurrentize the calls.
        return await asyncio.to_thread(
            client.post,
            "/api/public/companies/acme/products/starter-abc123/buy",
            json=body,
        )

    async def _runner():
        return await asyncio.gather(*[_attempt(i) for i in range(10)])

    # Bump rate limit out of the way for this concurrency test.
    rate_limit_module._LIMITERS.clear()
    rate_limit_module._LIMITERS[("public_buy", 5, 60)] = InMemoryRateLimiter(max_requests=100, window_seconds=60)

    responses = asyncio.run(_runner())
    statuses = [r.status_code for r in responses]
    successes = statuses.count(200)
    oversells = statuses.count(409)
    assert successes == 1, statuses
    assert oversells >= 1
    assert state["product"]["stock_quantity"] == 0
