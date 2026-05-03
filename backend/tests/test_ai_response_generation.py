import pytest

from services.ai_service import response_generator
from services.ai_service.facade import _safe_ai_reply_default


@pytest.fixture(autouse=True)
def fast_ai_response_memory(monkeypatch):
    async def empty_dict(*_args, **_kwargs):
        return {}

    async def empty_list(*_args, **_kwargs):
        return []

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(response_generator, "get_last_ai_response_context", empty_dict)
    monkeypatch.setattr(response_generator, "get_conversation_state_memory", empty_dict)
    monkeypatch.setattr(response_generator, "get_last_shown_product_ids", empty_list)
    monkeypatch.setattr(response_generator, "_persist_response_memory", noop)


@pytest.mark.asyncio
async def test_generate_ai_response_provider_failure_returns_degraded_payload(monkeypatch):
    async def fake_engine(**_kwargs):
        return {"provider": "gemini", "model_name": "gemini-2.5-flash", "id": "llm-1"}

    async def fail_text(*_args, **_kwargs):
        raise RuntimeError("429 RESOURCE_EXHAUSTED quota exceeded")

    monkeypatch.setattr(response_generator, "_allow_rule_based_recovery", lambda: False)
    monkeypatch.setattr(response_generator, "_resolve_engine_cached", fake_engine)
    monkeypatch.setattr(response_generator, "_generate_response_text", fail_text)

    result = await response_generator.generate_ai_response(
        [{"sender_type": "customer", "content": "What services are you providing?"}],
        customer_info={"id": "cust-1", "name": "Ayesha"},
        company_id="company-1",
        knowledge_context="We provide CRM setup, WhatsApp automation, and customer support workflows.",
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "company_question", "confidence": 0.9, "urgency": "low"},
    )

    assert result["degraded"] is True
    assert result["api_error"] is True
    assert result["error_type"] == "quota_exhausted"
    assert result["provider"] == "fallback"
    assert result["provider_error"]["provider"] == "gemini"
    assert "CRM setup" in result["response"]
    assert "budget" not in result["response"].lower()


@pytest.mark.asyncio
async def test_service_question_uses_service_context_without_budget_prompt(monkeypatch):
    monkeypatch.setattr(response_generator, "_allow_rule_based_recovery", lambda: True)

    result = await response_generator.generate_ai_response(
        [{"sender_type": "customer", "content": "What services are you providing?"}],
        customer_info={"id": "cust-1", "name": "Ayesha"},
        company_id="company-1",
        knowledge_context="Services include CRM implementation, support automation, and reporting dashboards.",
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "company_question", "confidence": 0.9, "urgency": "low"},
    )

    assert "CRM implementation" in result["response"]
    assert "services" in result["response"].lower() or "support automation" in result["response"].lower()
    assert "budget" not in result["response"].lower()
    assert "[" not in result["response"]


@pytest.mark.asyncio
async def test_service_question_uses_public_context_without_bad_company_grammar(monkeypatch):
    monkeypatch.setattr(response_generator, "_allow_rule_based_recovery", lambda: True)

    result = await response_generator.generate_ai_response(
        [{"sender_type": "customer", "content": "Hello I want to ask about your services. What services are you providing?"}],
        customer_info={"id": "cust-1", "name": "Home Sweet Home"},
        company_id="company-1",
        knowledge_context=(
            "Company: Nexora Labs\n"
            "Brand description: Nexora Labs is a forward-thinking technology company.\n"
            "Services: software development, cloud services, AI automation"
        ),
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "service_question", "confidence": 0.9, "urgency": "low"},
    )

    response = result["response"]
    assert "Nexora Labs" in response
    assert "software development" in response
    assert "cloud services" in response
    assert "AI automation" in response
    assert "We provide Nexora Labs is" not in response
    assert "budget" not in response.lower()
    assert "Next action" not in response


@pytest.mark.asyncio
async def test_repeated_service_product_question_continues_without_exact_repeat(monkeypatch):
    monkeypatch.setattr(response_generator, "_allow_rule_based_recovery", lambda: True)
    context = (
        "Company: Nexora Labs\n"
        "Brand description: Nexora Labs is a forward-thinking technology company.\n"
        "Services: software development, cloud services, AI automation"
    )
    first = await response_generator.generate_ai_response(
        [{"sender_type": "customer", "content": "What services are you providing?"}],
        customer_info={"id": "cust-1"},
        company_id="company-1",
        knowledge_context=context,
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "service_question", "confidence": 0.9, "urgency": "low"},
    )

    second = await response_generator.generate_ai_response(
        [
            {"sender_type": "customer", "content": "What services are you providing?"},
            {"sender_type": "ai", "content": first["response"]},
            {"sender_type": "customer", "content": "What services or products do you have?"},
        ],
        customer_info={"id": "cust-1"},
        company_id="company-1",
        knowledge_context=context,
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "service_question", "confidence": 0.9, "urgency": "low"},
    )

    assert second["response"] != first["response"]
    assert response_generator.text_similarity(second["response"], first["response"]) < 0.9
    assert "services, products, or both" in second["response"].lower()
    assert "Next action" not in second["response"]


@pytest.mark.asyncio
async def test_short_next_followup_continues_previous_service_topic(monkeypatch):
    monkeypatch.setattr(response_generator, "_allow_rule_based_recovery", lambda: True)
    previous = "Nexora Labs provides software development, cloud services, and AI automation. Which area would you like details about first?"

    result = await response_generator.generate_ai_response(
        [
            {"sender_type": "customer", "content": "What services do you provide?"},
            {"sender_type": "ai", "content": previous},
            {"sender_type": "customer", "content": "Next"},
        ],
        customer_info={"id": "cust-1", "name": "Home Sweet Home"},
        company_id="company-1",
        knowledge_context=(
            "Company: Nexora Labs\n"
            "Services: software development, cloud services, AI automation"
        ),
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "general_question", "confidence": 0.4, "urgency": "low"},
    )

    assert "software development" in result["response"] or "cloud services" in result["response"] or "AI automation" in result["response"]
    assert "general_question" not in result["response"]
    assert "focused on general question" not in result["response"].lower()
    assert "Next action" not in result["response"]


@pytest.mark.asyncio
async def test_product_image_request_returns_relevant_product_attachments(monkeypatch):
    async def fake_context(*_args, **_kwargs):
        return {
            "knowledge_text": "Product: Solar Kit | Price: 120 USD | Description: Portable solar kit.",
            "products": [
                {
                    "id": "prod-1",
                    "name": "Solar Kit",
                    "category": "energy",
                    "price": "120",
                    "price_currency": "USD",
                    "features": ["portable", "backup power"],
                }
            ],
            "product_ids": ["prod-1"],
            "product_attachments": [
                {
                    "type": "image",
                    "url": "https://cdn.example.test/solar-kit.jpg",
                    "product_id": "prod-1",
                    "product_name": "Solar Kit",
                }
            ],
            "rag_called": True,
        }

    monkeypatch.setattr(response_generator, "_allow_rule_based_recovery", lambda: True)
    monkeypatch.setattr(response_generator, "build_ai_context", fake_context)

    result = await response_generator.generate_ai_response(
        [{"sender_type": "customer", "content": "Show me product pictures"}],
        customer_info={"id": "cust-1", "name": "Ayesha"},
        company_id="company-1",
        db=object(),
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "product_recommendation", "confidence": 0.9, "urgency": "low"},
    )

    assert "Solar Kit" in result["response"]
    assert result["product_ids"] == ["prod-1"]
    assert result["attachments"][0]["url"] == "https://cdn.example.test/solar-kit.jpg"
    assert result["product_images"] == result["attachments"]
    assert "budget" not in result["response"].lower()


@pytest.mark.asyncio
async def test_company_question_filters_private_fields(monkeypatch):
    monkeypatch.setattr(response_generator, "_allow_rule_based_recovery", lambda: True)

    result = await response_generator.generate_ai_response(
        [{"sender_type": "customer", "content": "Tell me about your company"}],
        customer_info={"id": "cust-1", "name": "Ayesha"},
        company_id="company-1",
        knowledge_context=(
            "Company: Pulse Solar\n"
            "Brand description: Clean energy products and installation support.\n"
            "Private phone: +1 555 123 4567\n"
            "Private email: owner@example.test\n"
            "company_id: internal-company-1\n"
            "API key: secret-value"
        ),
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "company_question", "confidence": 0.9, "urgency": "low"},
    )

    assert "Pulse Solar" in result["response"]
    assert "Clean energy" in result["response"]
    assert "555" not in result["response"]
    assert "owner@example" not in result["response"]
    assert "company_id" not in result["response"]
    assert "secret" not in result["response"].lower()


@pytest.mark.asyncio
async def test_product_question_returns_name_price_and_features(monkeypatch):
    async def fake_context(*_args, **_kwargs):
        return {
            "knowledge_text": "Product: Starter CRM | Price: 49 USD | Features: WhatsApp inbox, lead tracking",
            "products": [
                {
                    "id": "prod-crm",
                    "name": "Starter CRM",
                    "category": "software",
                    "price": "49",
                    "price_currency": "USD",
                    "features": ["WhatsApp inbox", "lead tracking"],
                }
            ],
            "product_ids": ["prod-crm"],
            "product_attachments": [],
            "public_company": {},
            "rag_called": True,
        }

    monkeypatch.setattr(response_generator, "_allow_rule_based_recovery", lambda: True)
    monkeypatch.setattr(response_generator, "build_ai_context", fake_context)

    result = await response_generator.generate_ai_response(
        [{"sender_type": "customer", "content": "What products do you have?"}],
        customer_info={"id": "cust-1"},
        company_id="company-1",
        db=object(),
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "product_recommendation", "confidence": 0.9, "urgency": "low"},
    )

    assert "Starter CRM" in result["response"]
    assert "49 USD" in result["response"]
    assert "WhatsApp inbox" in result["response"]
    assert "lead tracking" in result["response"]


@pytest.mark.asyncio
async def test_product_image_request_accepts_api_media_urls(monkeypatch):
    async def fake_context(*_args, **_kwargs):
        return {
            "knowledge_text": "Product: Red Shoes | Price: 80 USD",
            "products": [
                {
                    "id": "prod-shoes",
                    "name": "Red Shoes",
                    "category": "shoes",
                    "price": "80",
                    "price_currency": "USD",
                    "features": ["red leather"],
                }
            ],
            "product_ids": ["prod-shoes"],
            "product_attachments": [
                {
                    "type": "image",
                    "url": "/api/products/media/red-shoes.jpg",
                    "product_id": "prod-shoes",
                    "product_name": "Red Shoes",
                }
            ],
            "public_company": {},
            "rag_called": True,
        }

    monkeypatch.setattr(response_generator, "_allow_rule_based_recovery", lambda: True)
    monkeypatch.setattr(response_generator, "build_ai_context", fake_context)

    result = await response_generator.generate_ai_response(
        [{"sender_type": "customer", "content": "Show me pictures of red shoes"}],
        customer_info={"id": "cust-1"},
        company_id="company-1",
        db=object(),
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "product_image_request", "confidence": 0.9, "urgency": "low"},
    )

    assert "Red Shoes" in result["response"]
    assert result["attachments"][0]["url"] == "/api/products/media/red-shoes.jpg"


@pytest.mark.asyncio
async def test_product_without_image_still_returns_details(monkeypatch):
    async def fake_context(*_args, **_kwargs):
        return {
            "knowledge_text": "Product: Desk Lamp | Price: 35 USD",
            "products": [
                {
                    "id": "prod-lamp",
                    "name": "Desk Lamp",
                    "price": "35",
                    "price_currency": "USD",
                    "features": ["adjustable brightness"],
                }
            ],
            "product_ids": ["prod-lamp"],
            "product_attachments": [],
            "public_company": {},
            "rag_called": True,
        }

    monkeypatch.setattr(response_generator, "_allow_rule_based_recovery", lambda: True)
    monkeypatch.setattr(response_generator, "build_ai_context", fake_context)

    result = await response_generator.generate_ai_response(
        [{"sender_type": "customer", "content": "Send picture of desk lamp"}],
        customer_info={"id": "cust-1"},
        company_id="company-1",
        db=object(),
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "product_image_request", "confidence": 0.9, "urgency": "low"},
    )

    assert "Desk Lamp" in result["response"]
    assert "35 USD" in result["response"]
    assert result["attachments"] == []
    assert "do not see an image" in result["response"].lower()


@pytest.mark.asyncio
async def test_buying_intent_shares_website_only_at_order_step(monkeypatch):
    async def fake_context(*_args, **_kwargs):
        return {
            "knowledge_text": "Company: Pulse Store | Website: https://shop.example.test",
            "products": [{"id": "prod-1", "name": "Solar Kit", "price": "120", "price_currency": "USD"}],
            "product_ids": ["prod-1"],
            "product_attachments": [],
            "public_company": {"company_name": "Pulse Store", "website_address": "https://shop.example.test"},
            "rag_called": True,
        }

    monkeypatch.setattr(response_generator, "_allow_rule_based_recovery", lambda: True)
    monkeypatch.setattr(response_generator, "build_ai_context", fake_context)

    early = await response_generator.generate_ai_response(
        [{"sender_type": "customer", "content": "I am interested"}],
        customer_info={"id": "cust-1"},
        company_id="company-1",
        db=object(),
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "buying_intent", "confidence": 0.9, "urgency": "medium"},
    )
    final = await response_generator.generate_ai_response(
        [{"sender_type": "customer", "content": "I want to buy this. Where can I order?"}],
        customer_info={"id": "cust-1"},
        company_id="company-1",
        db=object(),
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "website_link_request", "confidence": 0.9, "urgency": "medium"},
    )

    assert "https://shop.example.test" not in early["response"]
    assert "Solar Kit" in early["response"]
    assert "https://shop.example.test" in final["response"]


@pytest.mark.asyncio
async def test_price_question_does_not_invent_missing_price(monkeypatch):
    async def fake_context(*_args, **_kwargs):
        return {
            "knowledge_text": "Product: Custom Plan | Price: Not listed",
            "products": [{"id": "prod-plan", "name": "Custom Plan", "features": ["tailored setup"]}],
            "product_ids": ["prod-plan"],
            "product_attachments": [],
            "public_company": {},
            "rag_called": True,
        }

    monkeypatch.setattr(response_generator, "_allow_rule_based_recovery", lambda: True)
    monkeypatch.setattr(response_generator, "build_ai_context", fake_context)

    result = await response_generator.generate_ai_response(
        [{"sender_type": "customer", "content": "What is the price?"}],
        customer_info={"id": "cust-1"},
        company_id="company-1",
        db=object(),
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "pricing_question", "confidence": 0.9, "urgency": "medium"},
    )

    assert "Custom Plan" in result["response"]
    assert "not listed" in result["response"].lower()
    assert "99" not in result["response"]


@pytest.mark.asyncio
async def test_rule_based_response_never_exposes_internal_phrases(monkeypatch):
    monkeypatch.setattr(response_generator, "_allow_rule_based_recovery", lambda: True)

    result = await response_generator.generate_ai_response(
        [{"sender_type": "customer", "content": "I need support"}],
        customer_info={"id": "cust-1"},
        company_id="company-1",
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "support_request", "confidence": 0.9, "urgency": "medium"},
    )

    forbidden = [
        "Here is the quickest path",
        "Next action:",
        "Conversation stage",
        "Intent shifted",
        "Give a direct",
        "Ask one specific",
        "Use available context",
    ]
    for phrase in forbidden:
        assert phrase not in result["response"]


@pytest.mark.asyncio
async def test_llm_internal_text_is_sanitized_before_return(monkeypatch):
    async def fake_engine(**_kwargs):
        return {"provider": "gemini", "model_name": "gemini-2.5-flash", "id": "llm-1"}

    async def bad_text(*_args, **_kwargs):
        return (
            "Thanks for your message, Home Sweet Home. I understand you are focused on general question. "
            "Next action: Answer the customer's question directly if enough context exists."
        )

    monkeypatch.setattr(response_generator, "_allow_rule_based_recovery", lambda: False)
    monkeypatch.setattr(response_generator, "_resolve_engine_cached", fake_engine)
    monkeypatch.setattr(response_generator, "_generate_response_text", bad_text)

    result = await response_generator.generate_ai_response(
        [{"sender_type": "customer", "content": "Next"}],
        customer_info={"id": "cust-1", "name": "Home Sweet Home"},
        company_id="company-1",
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "general_question", "confidence": 0.4, "urgency": "low"},
    )

    assert "Next action" not in result["response"]
    assert "focused on general question" not in result["response"].lower()
    assert "general_question" not in result["response"]
    assert "services, products, pricing, or support" in result["response"]


@pytest.mark.asyncio
async def test_provider_failure_fallback_for_next_is_customer_facing(monkeypatch):
    async def fake_engine(**_kwargs):
        return {"provider": "gemini", "model_name": "gemini-2.5-flash", "id": "llm-1"}

    async def fail_text(*_args, **_kwargs):
        raise RuntimeError("429 RESOURCE_EXHAUSTED quota exceeded")

    monkeypatch.setattr(response_generator, "_allow_rule_based_recovery", lambda: False)
    monkeypatch.setattr(response_generator, "_resolve_engine_cached", fake_engine)
    monkeypatch.setattr(response_generator, "_generate_response_text", fail_text)

    result = await response_generator.generate_ai_response(
        [
            {"sender_type": "customer", "content": "What services do you provide?"},
            {"sender_type": "ai", "content": "We provide software development, cloud services, and AI automation."},
            {"sender_type": "customer", "content": "Next"},
        ],
        customer_info={"id": "cust-1", "name": "Home Sweet Home"},
        company_id="company-1",
        knowledge_context="Services: software development, cloud services, AI automation",
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "general_question", "confidence": 0.4, "urgency": "low"},
    )

    assert result["degraded"] is True
    assert "Next action" not in result["response"]
    assert "focused on" not in result["response"].lower()
    assert "software development" in result["response"] or "cloud services" in result["response"]


@pytest.mark.asyncio
async def test_greeting_does_not_ask_for_budget(monkeypatch):
    monkeypatch.setattr(response_generator, "_allow_rule_based_recovery", lambda: True)

    result = await response_generator.generate_ai_response(
        [{"sender_type": "customer", "content": "Hi"}],
        customer_info={"id": "cust-1", "name": "Ali"},
        company_id="company-1",
        knowledge_context="Company: Pulse Solar\nServices: solar panels, energy storage, installation support",
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "greeting", "confidence": 0.95, "urgency": "low"},
    )

    assert "budget" not in result["response"].lower()
    assert "Hi" in result["response"] or "hi" in result["response"].lower() or "Hello" in result["response"] or "hey" in result["response"].lower() or "Welcome" in result["response"] or "Thanks" in result["response"]
    assert result["response"].strip() != ""


@pytest.mark.asyncio
async def test_greeting_with_no_context_does_not_ask_for_budget(monkeypatch):
    """Even with empty knowledge context, Hi must not produce a budget question."""
    monkeypatch.setattr(response_generator, "_allow_rule_based_recovery", lambda: True)

    result = await response_generator.generate_ai_response(
        [{"sender_type": "customer", "content": "Hi"}],
        customer_info={"id": "cust-2"},
        company_id="company-2",
        knowledge_context="",
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "greeting", "confidence": 0.95, "urgency": "low"},
    )

    assert "budget" not in result["response"].lower()
    assert result["response"].strip() != ""
    # Must not contain internal markers
    for phrase in ["Next action:", "Conversation stage", "focused on general question", "general_question"]:
        assert phrase not in result["response"]


def test_safe_ai_reply_default_product_prompt_has_no_budget():
    response = _safe_ai_reply_default("show me products", {"name": "Customer"})

    assert "budget" not in response.lower()


def test_build_pricing_response_single_product_is_prose():
    response = response_generator.build_pricing_response(
        "what is the price",
        ai_context={
            "products": [
                {
                    "name": "Starter Plan",
                    "price": "49",
                    "price_currency": "USD",
                    "features": ["inbox", "automation"],
                }
            ]
        },
    )

    assert response.startswith("The Starter Plan is 49 USD")
    assert not response.startswith("1.")


def test_build_support_response_acknowledges_delayed_delivery():
    response = response_generator.build_support_response("my order is delayed")

    assert "delivery" in response.lower()
    assert "not arrived" in response.lower() or "delayed" in response.lower()


def test_clean_customer_response_drops_only_internal_sentence():
    raw = "We can help with setup. Conversation stage: discovery. Which service do you want?"

    cleaned = response_generator._clean_customer_response_text(raw)

    assert "We can help with setup" in cleaned
    assert "Which service do you want" in cleaned
    assert "Conversation stage" not in cleaned


def test_fix_grammar_errors_removes_malformed_company_phrase():
    fixed = response_generator._fix_grammar_errors("We provide Nexora Labs is a software company")

    assert fixed != "We provide Nexora Labs is a software company"
    assert "We provide a software company" in fixed


@pytest.mark.asyncio
async def test_no_internal_phrases_in_any_response(monkeypatch):
    """Internal phrases must never leak into customer-facing responses."""
    monkeypatch.setattr(response_generator, "_allow_rule_based_recovery", lambda: True)

    internal_phrases = [
        "Next action:",
        "Conversation stage",
        "Intent shifted",
        "focused on general question",
        "general_question",
        "service_question",
        "product_catalog_question",
        "operating guidance:",
        "style guidance:",
        "agent tone:",
        "private planning notes",
    ]

    for query, intent in [
        ("Hi", "greeting"),
        ("What services do you provide?", "service_question"),
        ("Next", "general_question"),
        ("I need support", "support_request"),
    ]:
        result = await response_generator.generate_ai_response(
            [{"sender_type": "customer", "content": query}],
            customer_info={"id": "cust-3"},
            company_id="company-3",
            knowledge_context="Company: Nexora\nServices: CRM, automation",
            observed_sentiment={"emotion": "neutral", "score": 0.0},
            observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
            observed_intent={"intent": intent, "confidence": 0.9, "urgency": "low"},
        )
        for phrase in internal_phrases:
            assert phrase not in result["response"], f"Phrase '{phrase}' found in response for query='{query}'"


@pytest.mark.asyncio
async def test_product_image_request_api_url_returned_in_attachments(monkeypatch):
    """Relative /api/ media URLs must be accepted as valid attachment URLs."""
    async def fake_context(*_args, **_kwargs):
        return {
            "knowledge_text": "Product: Blue Sneakers | Price: 95 USD",
            "products": [
                {
                    "id": "prod-sneakers",
                    "name": "Blue Sneakers",
                    "category": "shoes",
                    "price": "95",
                    "price_currency": "USD",
                    "features": ["blue leather", "size 38-46"],
                }
            ],
            "product_ids": ["prod-sneakers"],
            "product_attachments": [
                {
                    "type": "image",
                    "url": "/api/products/media/company-1/blue-sneakers.jpg",
                    "product_id": "prod-sneakers",
                    "product_name": "Blue Sneakers",
                }
            ],
            "public_company": {},
            "rag_called": True,
        }

    monkeypatch.setattr(response_generator, "_allow_rule_based_recovery", lambda: True)
    monkeypatch.setattr(response_generator, "build_ai_context", fake_context)

    result = await response_generator.generate_ai_response(
        [{"sender_type": "customer", "content": "Show me pictures of blue sneakers"}],
        customer_info={"id": "cust-1"},
        company_id="company-1",
        db=object(),
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "product_image_request", "confidence": 0.9, "urgency": "low"},
    )

    assert "Blue Sneakers" in result["response"]
    assert len(result["attachments"]) == 1
    assert result["attachments"][0]["url"] == "/api/products/media/company-1/blue-sneakers.jpg"


@pytest.mark.asyncio
async def test_next_action_is_machine_safe_label_on_fallback(monkeypatch):
    async def fake_engine(**_kwargs):
        return {"provider": "gemini", "model_name": "gemini-2.5-flash", "id": "llm-1"}

    async def fail_text(*_args, **_kwargs):
        raise RuntimeError("429 RESOURCE_EXHAUSTED quota exceeded")

    monkeypatch.setattr(response_generator, "_allow_rule_based_recovery", lambda: False)
    monkeypatch.setattr(response_generator, "_resolve_engine_cached", fake_engine)
    monkeypatch.setattr(response_generator, "_generate_response_text", fail_text)

    result = await response_generator.generate_ai_response(
        [{"sender_type": "customer", "content": "I need support"}],
        customer_info={"id": "cust-1"},
        company_id="company-1",
        observed_sentiment={"emotion": "neutral", "score": 0.0},
        observed_conversation_sentiment={"sentiment_label": "neutral", "score": 0.0},
        observed_intent={"intent": "support_request", "confidence": 0.9, "urgency": "medium"},
    )

    forbidden = ["Answer the customer", "Ask one specific", "using available context"]
    assert result["next_action"] == "support_resolution"
    for phrase in forbidden:
        assert phrase.lower() not in result["next_action"].lower()
