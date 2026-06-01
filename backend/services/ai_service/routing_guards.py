"""Backward-compatibility shim. Logic lives in services.conversation_engine.routing_guards."""
from services.conversation_engine.routing_guards import *  # noqa: F401, F403
from services.conversation_engine.routing_guards import (
    is_low_value_message,
    lightweight_route_message,
    should_lightweight_bypass,
    route_product_order_intent,
    detect_product_intent,
    detect_order_intent,
    detect_order_continuation,
    should_send_product_images,
    should_start_or_continue_order,
    assess_lightweight_conversational_risk,
    explicit_product_signal,
    is_high_confidence_product_intent,
    should_fetch_knowledge_context,
    classify_product_order_demand,
    normalize_message_text,
    PRODUCT_INTENTS,
    KNOWLEDGE_INTENTS,
    LOW_VALUE_INTENTS,
)
