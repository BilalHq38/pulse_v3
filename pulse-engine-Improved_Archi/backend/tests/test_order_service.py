import re
from pathlib import Path

import pytest

from services import order_service
from services.ai_service import response_generator
from services.ai_service.routing_guards import (
    route_product_order_intent,
    should_send_product_images,
    should_start_or_continue_order,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeOrderDb:
    def __init__(self):
        self.orders = []
        self.notifications = []
        self.executed = []
        self.lead_updates = []
        self.customer_updates = []
        self.customer_creates = []
        self.conversation = {
            "id": "convo-1",
            "company_id": "co-1",
            "customer_id": "cust-1",
            "customer_name": "Avery",
            "channel": "whatsapp",
            "assigned_to": "agent-1",
            "assigned_name": "Agent One",
            "customer_record_name": "Avery Stone",
            "customer_record_email": "",
            "customer_record_phone": "+15550100",
            "customer_record_address": "",
            "lead_id": "lead-1",
        }
        self.products = [
            {"id": "prod-1", "name": "Product Alpha", "product_title": "", "description": "Alpha desc", "price": "10", "price_currency": "USD", "category": "general", "status": "active"},
            {"id": "prod-2", "name": "Product Beta", "product_title": "", "description": "Beta desc", "price": "20", "price_currency": "USD", "category": "general", "status": "active"},
            {"id": "prod-3", "name": "Product Gamma", "product_title": "", "description": "Gamma desc", "price": "30", "price_currency": "USD", "category": "general", "status": "active"},
            {"id": "prod-4", "name": "Product Delta", "product_title": "", "description": "Delta desc", "price": "40", "price_currency": "USD", "category": "general", "status": "active"},
            {"id": "prod-5", "name": "Product Epsilon", "product_title": "", "description": "Epsilon desc", "price": "50", "price_currency": "USD", "category": "general", "status": "active"},
            {"id": "prod-6", "name": "Product Zeta", "product_title": "", "description": "Zeta desc", "price": "60", "price_currency": "USD", "category": "general", "status": "active"},
            {"id": "prod-7", "name": "Product Eta", "product_title": "", "description": "Eta desc", "price": "70", "price_currency": "USD", "category": "general", "status": "active"},
            {"id": "prod-8", "name": "Product Theta", "product_title": "", "description": "Theta desc", "price": "80", "price_currency": "USD", "category": "general", "status": "active"},
        ]
        self.attachments = [
            {
                "raw_metadata": {
                    "product_id": "prod-1",
                    "product_name": "Product Alpha",
                }
            }
        ]
        self.users = [{"id": "admin-1"}, {"id": "agent-1"}]

    async def fetchrow(self, query, *args):
        if query.startswith("SELECT * FROM orders"):
            company_id, conversation_id, statuses = args[:3]
            for order in reversed(self.orders):
                if (
                    order["company_id"] == company_id
                    and order["conversation_id"] == conversation_id
                    and order["status"] in set(statuses)
                ):
                    return dict(order)
            return None
        if query.startswith("SELECT c.id"):
            return dict(self.conversation)
        if query.startswith("UPDATE orders SET status='admin_review'"):
            company_id, order_id = args[:2]
            for order in self.orders:
                if order["company_id"] == company_id and order["id"] == order_id:
                    order["status"] = "admin_review"
                    order["missing_fields"] = []
                    return dict(order)
            return None
        if query.startswith("UPDATE orders SET status=$1"):
            status, company_id, order_id = args[:3]
            for order in self.orders:
                if order["company_id"] == company_id and order["id"] == order_id:
                    order["status"] = status
                    return dict(order)
            return None
        if query.startswith("SELECT o.*"):
            company_id, order_id = args[:2]
            for order in self.orders:
                if order["company_id"] == company_id and order["id"] == order_id:
                    return {**order, "assigned_name": "Agent One"}
            return None
        if query.startswith("SELECT id FROM customers"):
            return None
        return None

    async def fetch(self, query, *args):
        if query.startswith("SELECT ma.raw_metadata"):
            return list(self.attachments)
        if query.startswith("SELECT id FROM users"):
            return list(self.users)
        if query.startswith("SELECT o.*"):
            return list(self.orders)
        if "FROM company_products" in query:
            if "NOT (id = ANY" in query:
                excluded = set(args[1] or [])
                limit = int(args[2] or 3)
                return [dict(product) for product in self.products if product["id"] not in excluded][:limit]
            return [dict(product) for product in self.products]
        return []

    async def execute(self, query, *args):
        self.executed.append((query, args))
        if query.startswith("INSERT INTO orders"):
            order = {
                "id": args[0],
                "company_id": args[1],
                "conversation_id": args[2],
                "lead_id": args[3],
                "customer_id": args[4],
                "product_id": args[5],
                "product_name": args[6],
                "quantity": args[7],
                "variant": args[8],
                "size": args[9],
                "color": args[10],
                "customer_name": args[11],
                "customer_email": args[12],
                "customer_phone": args[13],
                "delivery_address": args[14],
                "notes": args[15],
                "status": args[16],
                "source_channel": args[17],
                "created_by": args[18],
                "raw_details": order_service._json_loads(args[19], {}),
                "missing_fields": order_service._json_loads(args[20], []),
                "created_at": "2026-05-22T09:00:00+00:00",
                "updated_at": "2026-05-22T09:00:00+00:00",
            }
            self.orders.append(order)
        elif query.startswith("UPDATE orders SET product_id"):
            order_id = args[15]
            for order in self.orders:
                if order["id"] == order_id:
                    order.update(
                        {
                            "product_id": args[0],
                            "product_name": args[1],
                            "quantity": args[2],
                            "variant": args[3],
                            "size": args[4],
                            "color": args[5],
                            "customer_name": args[6],
                            "customer_email": args[7],
                            "customer_phone": args[8],
                            "delivery_address": args[9],
                            "notes": args[10],
                            "status": args[11],
                            "raw_details": {**order.get("raw_details", {}), **order_service._json_loads(args[12], {})},
                            "missing_fields": order_service._json_loads(args[13], []),
                        }
                    )
        elif query.startswith("UPDATE orders SET status='awaiting_confirmation'"):
            order_id = args[1]
            for order in self.orders:
                if order["id"] == order_id:
                    order["status"] = "awaiting_confirmation"
                    order["missing_fields"] = []
        elif query.startswith("UPDATE orders SET status='cancelled'"):
            order_id = args[1]
            for order in self.orders:
                if order["id"] == order_id:
                    order["status"] = "cancelled"
        elif query.startswith("INSERT INTO notifications"):
            self.notifications.append(args)
        elif query.startswith("UPDATE leads SET"):
            self.lead_updates.append(args)
        elif query.startswith("UPDATE customers SET"):
            self.customer_updates.append(args)
        elif query.startswith("INSERT INTO customers"):
            self.customer_creates.append(args)
        elif query.startswith("INSERT INTO lead_activities"):
            self.lead_updates.append(args)
        elif query.startswith("UPDATE orders SET customer_id"):
            customer_id, company_id, order_id = args[:3]
            for order in self.orders:
                if order["company_id"] == company_id and order["id"] == order_id:
                    order["customer_id"] = customer_id
        return "OK"


def test_orders_schema_sync_migration_includes_required_customer_columns():
    migration = (REPO_ROOT / "backend" / "sql_migrations" / "014_orders_schema_sync.sql").read_text(encoding="utf-8").lower()
    schema = (REPO_ROOT / "backend" / "sql_schema.sql").read_text(encoding="utf-8").lower()

    for column in (
        "customer_email",
        "customer_name",
        "customer_phone",
        "delivery_address",
        "quantity",
        "raw_details",
        "missing_fields",
    ):
        assert column in migration
        assert column in schema


def test_order_intent_detected_after_catalog_context():
    assert order_service.detect_order_intent("I want this", has_catalog_context=True)
    assert order_service.detect_order_intent("Place order", has_catalog_context=False)
    assert not order_service.detect_order_intent("what is your address", has_catalog_context=False)


def test_central_router_classifies_product_and_image_intents():
    assert route_product_order_intent("what products do you have")["intent"] == "product_query"
    assert route_product_order_intent("price kya hai")["intent"] == "product_query"
    assert route_product_order_intent("tasveer bhejo")["intent"] == "product_query"
    assert should_send_product_images("show images again")
    assert route_product_order_intent("my login is not working")["intent"] == "normal_support"


def test_central_router_prioritizes_order_over_catalog_after_products():
    context = {"has_catalog_context": True, "shown_product_count": 1}

    for message in (
        "how can I purchase this?",
        "how can I order it?",
        "I want this one",
        "ye wala chahiye",
        "order karna hai",
        "kaise purchase karun",
        "send this",
    ):
        result = route_product_order_intent(message, context)
        assert result["intent"] == "order_intent", message
        assert should_start_or_continue_order(message, context)
        assert not should_send_product_images(message, context)


def test_selected_item_after_multiple_products_needs_clarification():
    result = route_product_order_intent("selected item", {"has_catalog_context": True, "shown_product_count": 2})

    assert result["intent"] == "clarification_needed"
    assert result["needs_clarification"] is True


def test_confirm_without_active_order_needs_clarification():
    result = route_product_order_intent("confirm", {})

    assert result["intent"] == "clarification_needed"
    assert result["needs_clarification"] is True


def test_order_detail_extraction_uses_context_and_customer():
    details = order_service.extract_order_details(
        "I want 2 medium black Product Alpha. Deliver to House 12, Islamabad. Email avery@example.com Phone +15550123",
        customer_info={"name": "Avery"},
        product_context={"product_id": "prod-1", "product_name": "Product Alpha"},
    )

    assert details["product_id"] == "prod-1"
    assert details["product_name"] == "Product Alpha"
    assert details["quantity"] == 2
    assert "size" not in details
    assert "color" not in details
    assert "variant" not in details
    assert details["customer_phone"] == "+15550123"
    assert "House 12" in details["delivery_address"]


@pytest.mark.asyncio
async def test_product_query_does_not_enter_order_flow():
    db = FakeOrderDb()

    result = await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="what products do you have",
        customer_info={"id": "cust-1"},
        conversation_context=[],
        source_channel="whatsapp",
        source="webhook",
    )

    assert result is None
    assert db.orders == []


@pytest.mark.asyncio
async def test_buy_without_product_returns_numbered_db_product_list():
    db = FakeOrderDb()

    result = await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="I want to buy",
        customer_info={"id": "cust-1"},
        conversation_context=[],
        source_channel="whatsapp",
        source="webhook",
    )

    assert result["next_action"] == "awaiting_product_selection"
    assert "1. Product Alpha" in result["response"]
    assert "2. Product Beta" in result["response"]
    assert "3. Product Gamma" in result["response"]
    assert len(db.orders) == 1
    assert db.orders[0]["raw_details"]["order_state"] == "awaiting_product_selection"


@pytest.mark.asyncio
async def test_product_selection_by_number_updates_existing_draft():
    db = FakeOrderDb()
    await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="I want to buy",
        customer_info={"id": "cust-1"},
        conversation_context=[],
        source_channel="whatsapp",
        source="webhook",
    )

    result = await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="1",
        customer_info={"id": "cust-1"},
        conversation_context=[],
        source_channel="whatsapp",
        source="webhook",
    )

    assert len(db.orders) == 1
    assert db.orders[0]["product_name"] == "Product Alpha"
    assert result["next_action"] == "collect_order_details"
    assert "Name:" in result["response"]
    assert "Email:" in result["response"]
    assert "Phone:" in result["response"]
    assert "Delivery Address:" in result["response"]
    assert "Quantity:" in result["response"]
    assert "payment" not in result["response"].lower()
    assert "variant" not in result["response"].lower()
    assert "size" not in result["response"].lower()
    assert "color" not in result["response"].lower()


@pytest.mark.asyncio
async def test_more_products_returns_next_batch_without_repeating():
    db = FakeOrderDb()
    await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="I want to buy",
        customer_info={"id": "cust-1"},
        conversation_context=[],
        source_channel="whatsapp",
        source="webhook",
    )

    result = await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="show more",
        customer_info={"id": "cust-1"},
        conversation_context=[],
        source_channel="whatsapp",
        source="webhook",
    )

    assert "Product Eta" in result["response"]
    assert "Product Theta" in result["response"]
    assert "Product Alpha" not in result["response"]
    assert len(db.orders) == 1


@pytest.mark.asyncio
async def test_specific_product_resolution_uses_db_products_only():
    db = FakeOrderDb()

    matches = await order_service.resolve_order_product_matches(db, "co-1", "Product Alpha")
    missing = await order_service.resolve_order_product_matches(db, "co-1", "Product Unknown")

    assert [item["product_name"] for item in matches] == ["Product Alpha"]
    assert missing == []


@pytest.mark.asyncio
async def test_purchase_question_starts_order_and_does_not_send_images():
    db = FakeOrderDb()

    result = await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="how can I purchase this?",
        customer_info={"id": "cust-1", "name": "Avery", "phone": "+15550100"},
        conversation_context=[],
        source_channel="whatsapp",
        source="webhook",
    )

    assert result["intent_name"] == "order_intent"
    assert result["attachments"] == []
    assert result["product_images"] == []
    assert db.orders[0]["product_id"] == "prod-1"


@pytest.mark.asyncio
async def test_selected_item_after_multiple_products_asks_clarification():
    db = FakeOrderDb()
    context = [
        {
            "sender_type": "ai",
            "content": "Here are products",
            "attachments": [
                {"raw_metadata": {"product_id": "prod-1", "product_name": "Product Alpha"}},
                {"raw_metadata": {"product_id": "prod-2", "product_name": "Product Beta"}},
            ],
        }
    ]

    result = await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="selected item",
        customer_info={"id": "cust-1", "name": "Avery"},
        conversation_context=context,
        source_channel="whatsapp",
        source="webhook",
    )

    assert result["next_action"] == "clarify_selected_product"
    assert db.orders == []


@pytest.mark.asyncio
async def test_order_draft_created_and_missing_fields_requested():
    db = FakeOrderDb()

    result = await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="I want this",
        customer_info={"id": "cust-1", "name": "Avery", "phone": "+15550100"},
        conversation_context=[],
        source_channel="whatsapp",
        source="webhook",
    )

    assert result["intent_name"] == "order_intent"
    assert result["next_action"] == "collect_order_details"
    assert db.orders[0]["status"] == "collecting_details"
    assert "delivery address" in result["response"].lower()


@pytest.mark.asyncio
async def test_active_order_address_message_continues_without_images():
    db = FakeOrderDb()
    await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="I want this",
        customer_info={"id": "cust-1", "name": "Avery", "phone": "+15550100"},
        conversation_context=[],
        source_channel="whatsapp",
        source="webhook",
    )

    result = await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="Deliver to House 12, Islamabad",
        customer_info={"id": "cust-1", "name": "Avery", "phone": "+15550100"},
        conversation_context=[],
        source_channel="whatsapp",
        source="webhook",
    )

    assert result["next_action"] == "collect_order_details"
    assert result["attachments"] == []
    assert result["product_images"] == []
    assert len(db.orders) == 1
    assert db.orders[0]["delivery_address"] == "House 12, Islamabad"


@pytest.mark.asyncio
async def test_order_confirmation_requested_when_details_complete():
    db = FakeOrderDb()

    result = await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="I want 2 Product Alpha deliver to House 12, Islamabad email avery@example.com phone +15550123",
        customer_info={"id": "cust-1", "name": "Avery"},
        conversation_context=[],
        source_channel="whatsapp",
        source="webhook",
    )

    assert result["next_action"] == "request_order_confirmation"
    assert db.orders[0]["status"] == "awaiting_confirmation"
    assert "Reply 'Confirm'" in result["response"]


@pytest.mark.asyncio
async def test_order_placed_and_admin_notification_created(monkeypatch):
    db = FakeOrderDb()
    await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="I want 1 Product Alpha deliver to House 12, Islamabad email avery@example.com phone +15550123",
        customer_info={"id": "cust-1", "name": "Avery"},
        conversation_context=[],
        source_channel="whatsapp",
        source="webhook",
    )

    async def fake_notification(db_arg, *_args, **kwargs):
        db_arg.notifications.append(kwargs)
        return {"id": "note-1"}

    monkeypatch.setattr(order_service, "create_notification", fake_notification)
    result = await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="confirm",
        customer_info={"id": "cust-1", "name": "Avery"},
        conversation_context=[],
        source_channel="whatsapp",
        source="webhook",
    )

    assert result["next_action"] == "order_placed"
    assert db.orders[0]["id"] in result["response"]
    assert db.orders[0]["status"] == "admin_review"
    assert len(db.notifications) == 2
    assert db.lead_updates
    assert db.customer_updates


@pytest.mark.asyncio
async def test_full_product_picker_flow_places_order_and_lists_it(monkeypatch):
    db = FakeOrderDb()

    async def fake_notification(db_arg, *_args, **kwargs):
        db_arg.notifications.append(kwargs)
        return {"id": "note-1"}

    monkeypatch.setattr(order_service, "create_notification", fake_notification)

    await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="I want to buy",
        customer_info={"id": "cust-1"},
        conversation_context=[],
        source_channel="whatsapp",
        source="webhook",
    )
    await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="Product Alpha",
        customer_info={"id": "cust-1"},
        conversation_context=[],
        source_channel="whatsapp",
        source="webhook",
    )
    confirmation = await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="Name: Avery Stone\nEmail: avery@example.com\nPhone: +15550123\nDelivery Address: House 12, Islamabad\nQuantity: 2",
        customer_info={"id": "cust-1"},
        conversation_context=[],
        source_channel="whatsapp",
        source="webhook",
    )

    assert confirmation["next_action"] == "request_order_confirmation"
    assert "Product: Product Alpha" in confirmation["response"]
    assert "Name: Avery Stone" in confirmation["response"]
    assert "Email: avery@example.com" in confirmation["response"]
    assert "Phone: +15550123" in confirmation["response"]
    assert "Delivery Address: House 12" in confirmation["response"]
    assert "Quantity: 2" in confirmation["response"]
    assert "payment" not in confirmation["response"].lower()
    assert "variant" not in confirmation["response"].lower()
    assert "size" not in confirmation["response"].lower()
    assert "color" not in confirmation["response"].lower()

    placed = await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="confirm",
        customer_info={"id": "cust-1"},
        conversation_context=[],
        source_channel="whatsapp",
        source="webhook",
    )
    orders = await order_service.list_orders(db, company_id="co-1")

    assert placed["next_action"] == "order_placed"
    assert db.orders[0]["status"] == "admin_review"
    assert db.orders[0]["customer_email"] == "avery@example.com"
    assert orders[0]["customer_email"] == "avery@example.com"
    assert db.orders[0]["id"] in placed["response"]
    assert db.notifications


@pytest.mark.asyncio
async def test_duplicate_order_prevented_after_placement(monkeypatch):
    db = FakeOrderDb()
    await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="I want 1 Product Alpha deliver to House 12, Islamabad email avery@example.com phone +15550123",
        customer_info={"id": "cust-1", "name": "Avery"},
        conversation_context=[],
        source_channel="whatsapp",
        source="webhook",
    )
    monkeypatch.setattr(order_service, "create_notification", lambda *_args, **_kwargs: {"id": "note-1"})
    await order_service.place_order(db, "co-1", db.orders[0]["id"])

    result = await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="confirm",
        customer_info={"id": "cust-1", "name": "Avery"},
        conversation_context=[],
        source_channel="whatsapp",
        source="webhook",
    )

    assert result["next_action"] == "order_already_placed"
    assert len(db.orders) == 1


@pytest.mark.asyncio
async def test_manual_ai_draft_does_not_place_order():
    db = FakeOrderDb()

    result = await order_service.handle_order_flow(
        db=db,
        company_id="co-1",
        conversation_id="convo-1",
        message_text="Place order",
        customer_info={"id": "cust-1", "name": "Avery"},
        conversation_context=[],
        source_channel="whatsapp",
        source="manual_ai_respond",
        message_id="manual_ai_draft:co-1:convo-1",
    )

    assert result is None
    assert db.orders == []


@pytest.mark.asyncio
async def test_combined_ai_returns_order_flow_without_model_call(monkeypatch):
    async def fake_handle_order_flow(**kwargs):
        assert kwargs["company_id"] == "co-1"
        assert kwargs["conversation_id"] == "convo-1"
        return {
            "response": "Please confirm your order.",
            "confidence": 0.98,
            "provider": "rule",
            "model_name": "deterministic-order-flow",
            "intent_name": "order_intent",
            "order_id": "order-1",
            "order_status": "awaiting_confirmation",
            "attachments": [],
            "product_images": [],
            "product_ids": [],
            "rag_called": False,
        }

    async def fail_model_call(*_args, **_kwargs):
        raise AssertionError("model should not be called for deterministic order flow")

    monkeypatch.setattr(response_generator, "handle_order_flow", fake_handle_order_flow)
    monkeypatch.setattr(response_generator, "call_unified_message_ai", fail_model_call)

    result = await response_generator.generate_combined_ai_analysis(
        "confirm",
        [{"sender_type": "customer", "content": "confirm"}],
        customer_info={"id": "cust-1"},
        company_id="co-1",
        db=object(),
        conversation_id="convo-1",
        message_id="msg-1",
        source="webhook",
    )

    assert result["intent"]["intent"] == "order_intent"
    assert result["ai_response"]["response"] == "Please confirm your order."


@pytest.mark.asyncio
async def test_combined_purchase_question_uses_order_flow_not_catalog(monkeypatch):
    db = FakeOrderDb()

    async def fail_model_call(*_args, **_kwargs):
        raise AssertionError("model should not be called for deterministic order flow")

    monkeypatch.setattr(response_generator, "call_unified_message_ai", fail_model_call)

    result = await response_generator.generate_combined_ai_analysis(
        "how can I purchase this?",
        [{"sender_type": "customer", "content": "how can I purchase this?"}],
        customer_info={"id": "cust-1", "name": "Avery", "phone": "+15550100"},
        company_id="co-1",
        db=db,
        conversation_id="convo-1",
        message_id="msg-1",
        source="webhook",
    )

    ai_response = result["ai_response"]
    assert result["intent"]["intent"] == "order_intent"
    assert ai_response["attachments"] == []
    assert ai_response["product_images"] == []
    assert not re.search(r"\b(ai|llm|gemini|google|model|provider)\b", ai_response["response"].lower())
    assert "gemini" not in ai_response["response"].lower()
