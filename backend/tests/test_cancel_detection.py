import pytest
from services.ai_service.routing_guards import classify_product_order_demand

def test_cancel_detection_phrases():
    # Setup context indicating there is an active order
    context = {
        "active_order": True,
        "has_active_order": True
    }

    # 1. Standalone 'no' must trigger order cancel intent
    res_no = classify_product_order_demand("no", context)
    assert res_no.get("intent") == "order_cancel", f"Expected order_cancel, got: {res_no.get('intent')}"

    # 2. Standalone 'nope' must trigger order cancel intent
    res_nope = classify_product_order_demand("nope", context)
    assert res_nope.get("intent") == "order_cancel", f"Expected order_cancel, got: {res_nope.get('intent')}"

    # 3. Standalone 'reset' must trigger order cancel intent
    res_reset = classify_product_order_demand("reset", context)
    assert res_reset.get("intent") == "order_cancel", f"Expected order_cancel, got: {res_reset.get('intent')}"

    # 4. Phrase 'no problem' must NOT trigger order cancel
    res_no_problem = classify_product_order_demand("no problem", context)
    assert res_no_problem.get("intent") != "order_cancel", f"Expected non-cancel intent, got: {res_no_problem.get('intent')}"

    # 5. Phrase 'no worries' must NOT trigger order cancel
    res_no_worries = classify_product_order_demand("no worries", context)
    assert res_no_worries.get("intent") != "order_cancel", f"Expected non-cancel intent, got: {res_no_worries.get('intent')}"

    # 6. Phrase 'no i don\'t' must trigger order cancel
    res_no_dont = classify_product_order_demand("no i don't", context)
    assert res_no_dont.get("intent") == "order_cancel", f"Expected order_cancel, got: {res_no_dont.get('intent')}"

    # 7. Phrase 'i changed my mind' must trigger order cancel
    res_changed_mind = classify_product_order_demand("i changed my mind", context)
    assert res_changed_mind.get("intent") == "order_cancel", f"Expected order_cancel, got: {res_changed_mind.get('intent')}"
