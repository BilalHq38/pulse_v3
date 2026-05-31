from __future__ import annotations

from services.conversation_engine import prompt_builder
from services.conversation_engine.schemas import ContextChunk


FALLBACK = "I don't have that information right now. Would you like me to connect you with our team?"


def _chunk(source_type, source_id, content):
    return ContextChunk(source_type=source_type, source_id=source_id, title=source_id, content=content, relevance_score=0.9)


def test_product_queries_scope_prompt_to_product_catalog_only() -> None:
    prompt = prompt_builder.build_prompt(
        user_message="What products do you have?",
        chunks=[
            _chunk("company_data", "co", "Company name: Pulse Secret"),
            _chunk("product", "p1", "Product: Black Shirt | Price: 25 USD | Product page: https://shop.test/black-shirt"),
            _chunk("faq", "f1", "FAQ: Hidden return policy"),
            _chunk("knowledge_base", "kb1", "KB internal installation notes"),
        ],
        history_turns=[],
    )

    assert "<product_catalog>" in prompt
    assert "Black Shirt" in prompt
    assert "Pulse Secret" not in prompt
    assert "Hidden return policy" not in prompt
    assert "internal installation notes" not in prompt


def test_company_questions_scope_prompt_to_company_info_only() -> None:
    prompt = prompt_builder.build_prompt(
        user_message="Tell me about your company",
        chunks=[
            _chunk("company_data", "co", "Company name: Pulse"),
            _chunk("product", "p1", "Product: Black Shirt | Price: 25 USD"),
            _chunk("faq", "f1", "FAQ: Returns are 30 days"),
        ],
        history_turns=[],
    )

    assert "<company_info>" in prompt
    assert "Company name: Pulse" in prompt
    assert "Black Shirt" not in prompt
    assert "Returns are 30 days" not in prompt


def test_faq_questions_scope_prompt_to_faq_and_knowledge_base() -> None:
    prompt = prompt_builder.build_prompt(
        user_message="What is your return policy?",
        chunks=[
            _chunk("company_data", "co", "Company name: Pulse"),
            _chunk("product", "p1", "Product: Black Shirt | Price: 25 USD"),
            _chunk("faq", "f1", "Q: Returns?\nA: 30 days"),
            _chunk("knowledge_base", "kb1", "Return exceptions are listed here."),
        ],
        history_turns=[],
    )

    assert "<faqs>" in prompt
    assert "<knowledge_base>" in prompt
    assert "30 days" in prompt
    assert "Return exceptions" in prompt
    assert "Company name: Pulse" not in prompt
    assert "Black Shirt" not in prompt


def test_purchase_intent_prompt_contains_grounded_link_and_cta_instruction() -> None:
    prompt = prompt_builder.build_prompt(
        user_message="I want to buy this product",
        chunks=[
            _chunk(
                "product",
                "p1",
                "Product: Black Shirt | Price: 25 USD | Product page: https://shop.test/black-shirt",
            )
        ],
        history_turns=[],
    )

    assert "https://shop.test/black-shirt" in prompt
    assert "click the link to complete the order" in prompt
    assert "Product page URL" in prompt


def test_prompt_uses_exact_escalation_phrase_and_no_ungrounded_product_data() -> None:
    prompt = prompt_builder.build_prompt(
        user_message="I want to buy this product",
        chunks=[
            _chunk(
                "product",
                "p1",
                "Product: Black Shirt | Price: 25 USD | Product page: https://shop.test/black-shirt",
            )
        ],
        history_turns=[],
    )

    assert FALLBACK in prompt
    assert "Black Shirt" in prompt
    assert "25 USD" in prompt
    assert "https://shop.test/black-shirt" in prompt
    assert "White Shoes" not in prompt
    assert "999 USD" not in prompt
    assert "https://shop.test/fake" not in prompt
