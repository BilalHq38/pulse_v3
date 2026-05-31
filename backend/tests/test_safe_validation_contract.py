from __future__ import annotations

import asyncio
import logging

from services.conversation_engine.llm_gateway import GenerationResult
from services.conversation_engine.orchestrator import Orchestrator
from services.conversation_engine.schemas import ContextChunk, ProductLink, TurnRequest
from services.conversation_engine import validator


def _product_chunk() -> ContextChunk:
    return ContextChunk(
        source_type="product",
        source_id="p1",
        title="Black Shirt",
        content="Product: Black Shirt | Price: 25 USD | Product page: https://shop.test/black-shirt",
        metadata={"name": "Black Shirt", "price": "25", "public_url": "https://shop.test/black-shirt"},
        relevance_score=0.9,
    )


def test_safe_validation_strips_fabricated_product_price_and_url() -> None:
    safe = validator.safe_validate_response(
        answer="Black Shirt costs 25 USD. White Shoes costs 999 USD: https://shop.test/fake",
        product_links=[],
        chunks=[_product_chunk()],
        user_message="Tell me about products",
    )

    assert safe.report.ok, safe.report.offences
    assert "Black Shirt" in safe.answer
    assert "25 USD" in safe.answer
    assert "White Shoes" not in safe.answer
    assert "999 USD" not in safe.answer
    assert "https://shop.test/fake" not in safe.answer
    assert any(item.startswith("product_name:") for item in safe.stripped_content)
    assert any(item.startswith("price:") for item in safe.stripped_content)
    assert any(item.startswith("url:") for item in safe.stripped_content)


def test_safe_validation_strips_internal_context_tags_and_source_labels() -> None:
    safe = validator.safe_validate_response(
        answer="<product_catalog>Black Shirt</product_catalog> costs 25 USD. (Source: Product Database)",
        product_links=[],
        chunks=[_product_chunk()],
    )

    assert safe.report.ok, safe.report.offences
    assert "<product_catalog>" not in safe.answer
    assert "Source:" not in safe.answer
    assert any(item.startswith("context_tag:") for item in safe.stripped_content)
    assert any(item.startswith("source_label:") for item in safe.stripped_content)


def test_safe_validation_requires_purchase_link_for_purchase_intent() -> None:
    missing = validator.safe_validate_response(
        answer="You can buy Black Shirt for 25 USD.",
        product_links=[],
        chunks=[_product_chunk()],
        user_message="I want to buy this product",
    )

    assert not missing.report.ok
    assert "missing_purchase_link" in missing.report.offences

    present = validator.safe_validate_response(
        answer="You can buy Black Shirt for 25 USD: https://shop.test/black-shirt",
        product_links=[ProductLink(product_id="p1", url="https://shop.test/black-shirt")],
        chunks=[_product_chunk()],
        user_message="I want to buy this product",
    )

    assert present.report.ok, present.report.offences


def test_safe_validation_removes_duplicate_response_blocks() -> None:
    safe = validator.safe_validate_response(
        answer="Black Shirt costs 25 USD.\n\nBlack Shirt costs 25 USD.",
        product_links=[],
        chunks=[_product_chunk()],
    )

    assert safe.report.ok, safe.report.offences
    assert safe.answer.count("Black Shirt costs 25 USD.") == 1
    assert "duplicate_paragraph" in safe.stripped_content


def test_orchestrator_logs_safe_validation_and_persists_one_response(caplog) -> None:
    class FakeRetriever:
        source_type = "product"

        async def fetch(self, db, *, company_id, query, top_k):
            return [_product_chunk()]

    class EmptyRetriever:
        def __init__(self, source_type):
            self.source_type = source_type

        async def fetch(self, db, *, company_id, query, top_k):
            return []

    class FakeGateway:
        async def generate(self, prompt, *, engine=None):
            return GenerationResult(
                text="Black Shirt costs 25 USD. White Shoes costs 999 USD: https://shop.test/fake",
                attempts=1,
            )

    class FakeDb:
        def __init__(self):
            self.executed = []

        async def fetch(self, *_args):
            return []

        async def fetchrow(self, *_args):
            return None

        async def fetchval(self, *_args):
            return 0

        async def execute(self, sql, *args):
            self.executed.append((sql, args))

    db = FakeDb()
    orch = Orchestrator(
        retrievers={
            "company_data": EmptyRetriever("company_data"),
            "product": FakeRetriever(),
            "faq": EmptyRetriever("faq"),
            "knowledge_base": EmptyRetriever("knowledge_base"),
        },
        gateway=FakeGateway(),
    )
    caplog.set_level(logging.INFO, logger="services.conversation_engine.orchestrator")

    result = asyncio.run(
        orch.run_turn(
            db,
            TurnRequest(session_id="s-1", company_id="co-1", user_message="What is the price of Black Shirt?"),
        )
    )

    assert result.answer.count("Black Shirt") == 1
    assert "White Shoes" not in result.answer
    assert "999 USD" not in result.answer
    assert "https://shop.test/fake" not in result.answer
    inserts = [item for item in db.executed if "INSERT INTO ai_conversation_turns" in item[0]]
    assert len(inserts) == 1
    assert "safe_validation checks_passed=true" in caplog.text
    assert "stripped_content=" in caplog.text
