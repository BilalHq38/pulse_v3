"""Wave 4 (cont.) — upsell context retriever + product_relationships."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from services.conversation_engine import Orchestrator, TurnRequest
from services.conversation_engine.llm_gateway import GenerationResult
from services.conversation_engine.retrieval.products import OrderRelatedProductRetriever


@dataclass
class _FakeDb:
    orders: dict[str, dict] = field(default_factory=dict)
    relationships: list[dict] = field(default_factory=list)
    company_settings: dict[str, dict] = field(default_factory=dict)
    executed: list[tuple] = field(default_factory=list)

    async def fetchrow(self, sql, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("SELECT product_id FROM orders"):
            (company_id, order_id) = args
            order = self.orders.get(order_id)
            if not order or order.get("company_id") != company_id:
                return None
            return {"product_id": order.get("product_id")}
        return None

    async def fetch(self, sql, *args):
        sql_norm = " ".join(sql.split())
        if "FROM product_relationships pr" in sql_norm and "JOIN company_products cp" in sql_norm:
            (company_id, product_id, limit) = args
            rows = [
                r for r in self.relationships
                if r["company_id"] == company_id and r["product_id"] == product_id
            ]
            rows.sort(key=lambda r: r.get("weight", 0.5), reverse=True)
            # Apply the SQL alias `pr.related_id AS id` so the retriever sees
            # the row shape it expects.
            return [{**r, "id": r["related_id"]} for r in rows[: int(limit)]]
        return []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


def test_related_retriever_returns_empty_for_unknown_order():
    db = _FakeDb()
    retriever = OrderRelatedProductRetriever(order_id="unknown")
    chunks = asyncio.run(retriever.fetch(db, company_id="c1", query=""))
    assert chunks == []


def test_related_retriever_returns_empty_for_order_with_no_relationships():
    db = _FakeDb(orders={
        "o1": {"company_id": "c1", "product_id": "p1"},
    })
    retriever = OrderRelatedProductRetriever(order_id="o1")
    chunks = asyncio.run(retriever.fetch(db, company_id="c1", query=""))
    assert chunks == []


def test_related_retriever_returns_related_products_with_weights():
    db = _FakeDb(
        orders={"o1": {"company_id": "c1", "product_id": "p1"}},
        relationships=[
            {
                "company_id": "c1", "product_id": "p1", "related_id": "p2",
                "relation_kind": "complementary", "weight": 0.8,
                "name": "Phone Case", "price": "19.99", "category": "accessory",
                "slug": "phone-case", "links": "", "stock_quantity": 10,
                "product_title": "Phone Case", "product_type": "accessory",
                "description": "Protective phone case", "features": [],
                "price_currency": "USD",
            },
            {
                "company_id": "c1", "product_id": "p1", "related_id": "p3",
                "relation_kind": "upgrade", "weight": 0.4,
                "name": "Pro Plan", "price": "199", "category": "plan",
                "slug": "pro-plan", "links": "", "stock_quantity": None,
                "product_title": "Pro Plan", "product_type": "subscription",
                "description": "Pro plan", "features": [],
                "price_currency": "USD",
            },
        ],
    )
    retriever = OrderRelatedProductRetriever(order_id="o1")
    chunks = asyncio.run(retriever.fetch(db, company_id="c1", query=""))
    assert len(chunks) == 2
    # Higher-weight relationship comes first.
    assert chunks[0].source_id == "p2"
    assert chunks[0].relevance_score == pytest.approx(0.8)
    assert chunks[0].metadata["relation_kind"] == "complementary"
    assert chunks[1].source_id == "p3"
    assert chunks[1].metadata["relation_kind"] == "upgrade"


def test_orchestrator_swaps_in_related_retriever_for_proactive_upsell():
    """Verify the orchestrator uses OrderRelatedProductRetriever for
    proactive upsell turns anchored to an order_id."""
    orch = Orchestrator()
    upsell_req = TurnRequest(
        session_id="s1", company_id="c1", user_message="suggest a complementary product",
        mode="proactive", workflow_kind="upsell", order_id="o1",
    )
    retriever = orch._retriever_for("product", upsell_req)
    assert isinstance(retriever, OrderRelatedProductRetriever)

    # Reactive turns keep the default product retriever.
    reactive_req = TurnRequest(
        session_id="s1", company_id="c1", user_message="what's the price?",
        mode="reactive",
    )
    assert not isinstance(orch._retriever_for("product", reactive_req), OrderRelatedProductRetriever)

    # Proactive but no order_id — fall back to the default retriever.
    proactive_no_order = TurnRequest(
        session_id="s1", company_id="c1", user_message="suggest",
        mode="proactive", workflow_kind="upsell", order_id="",
    )
    assert not isinstance(
        orch._retriever_for("product", proactive_no_order), OrderRelatedProductRetriever
    )


def test_orchestrator_end_to_end_proactive_upsell_uses_related_products(monkeypatch):
    """Integration-flavoured: orchestrator runs an upsell turn end-to-end
    with the related-products retriever, fake gateway, and verifies the
    related product makes it into the prompt."""
    captured_prompts: list[str] = []

    class _FakeGateway:
        async def generate(self, prompt, *, engine=None):
            captured_prompts.append(prompt)
            return GenerationResult(text="Consider adding a Phone Case for $19.99.", attempts=1)

    db = _FakeDb(
        orders={"o1": {"company_id": "c1", "product_id": "p1"}},
        relationships=[{
            "company_id": "c1", "product_id": "p1", "related_id": "p2",
            "relation_kind": "complementary", "weight": 0.9,
            "name": "Phone Case", "price": "19.99", "category": "accessory",
            "slug": "phone-case", "links": "", "stock_quantity": 10,
            "product_title": "Phone Case", "product_type": "accessory",
            "description": "Protective phone case", "features": [],
            "price_currency": "USD",
        }],
    )

    class _NoCompanyRetriever:
        source_type = "company_data"
        async def fetch(self, *args, **kwargs):
            return []

    class _NoTemplatesRetriever:
        source_type = "faq"
        async def fetch(self, *args, **kwargs):
            return []

    class _NoKbRetriever:
        source_type = "knowledge_base"
        async def fetch(self, *args, **kwargs):
            return []

    # Override the persistence side too — the real memory module hits the DB.
    from services.conversation_engine import memory as memory_module
    async def _no_history(*_a, **_kw):
        return []
    async def _no_summary(*_a, **_kw):
        return ""
    async def _next_idx(*_a, **_kw):
        return 1
    async def _persist(*_a, **_kw):
        return "t_test"
    monkeypatch.setattr(memory_module, "fetch_recent_turns", _no_history)
    monkeypatch.setattr(memory_module, "fetch_rolling_summary", _no_summary)
    monkeypatch.setattr(memory_module, "next_turn_index", _next_idx)
    monkeypatch.setattr(memory_module, "persist_turn", _persist)

    orch = Orchestrator(
        retrievers={
            "company_data": _NoCompanyRetriever(),
            "product": None,  # will be swapped in by _retriever_for
            "faq": _NoTemplatesRetriever(),
            "knowledge_base": _NoKbRetriever(),
        },
        gateway=_FakeGateway(),
    )

    result = asyncio.run(orch.run_proactive_turn(
        db,
        company_id="c1", session_id="s1", customer_id="cu1", order_id="o1",
        workflow_kind="upsell",
    ))

    # The engine returned the gateway's text and the prompt included the
    # related product's name.
    assert "Phone Case" in result.answer
    assert any("Phone Case" in p for p in captured_prompts)
    assert "product" in result.sources_used
