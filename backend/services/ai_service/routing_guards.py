from __future__ import annotations

import re
from typing import Any


PRODUCT_INTENTS = {
    "product_catalog_question",
    "product_recommendation",
    "purchase_inquiry",
    "product_image_request",
    "pricing_question",
    "availability_question",
    "buying_intent",
    "order_intent",
    "website_link_request",
}

KNOWLEDGE_INTENTS = {
    "company_question",
    "service_question",
    "business_question",
    "support_request",
    "complaint",
    "refund",
    "cancel_request",
    "shipping_question",
}

LOW_VALUE_INTENTS = {
    "greeting",
    "social",
    "gratitude",
    "acknowledgement",
    "rejection_or_opt_out",
}

_LOW_VALUE_EXACT = {
    "hi",
    "hii",
    "hello",
    "hey",
    "salam",
    "assalam",
    "assalamu alaikum",
    "thanks",
    "thank you",
    "thx",
    "ok",
    "okay",
    "k",
    "yes",
    "yeah",
    "yep",
    "no",
    "nope",
    "sure",
    "alright",
    "got it",
    "understood",
    "fine",
}

_GREETING_EXACT = {"hi", "hii", "hello", "hey", "salam", "assalam", "assalamu alaikum"}
_SOCIAL_EXACT = {
    "how are you",
    "how r u",
    "how are u",
    "how you doing",
    "how are you doing",
    "how is it going",
    "how's it going",
}
_GRATITUDE_EXACT = {"thanks", "thank you", "thx"}
_ACK_EXACT = _LOW_VALUE_EXACT - _GREETING_EXACT - _GRATITUDE_EXACT

_PRODUCT_TERMS = {
    "product",
    "products",
    "catalog",
    "catalogue",
    "item",
    "items",
    "collection",
    "inventory",
    "price",
    "pricing",
    "cost",
    "rate",
    "available",
    "availability",
    "stock",
    "image",
    "images",
    "photo",
    "photos",
    "picture",
    "pictures",
    "recommend",
    "suggest",
}

_PRODUCT_PHRASES = (
    "what do you sell",
    "what products",
    "show products",
    "show me products",
    "show me catalog",
    "show your catalog",
    "send catalog",
    "product pictures",
    "product images",
    "show me pictures",
    "show me photos",
    "how much",
    "what is the price",
    "what's the price",
    "product kya hain",
    "products kya hain",
    "kya products hain",
    "kya services hain",
    "kya milta hai",
    "kya available hai",
    "price kya hai",
    "rate kya hai",
    "qeemat",
    "qeemat kya hai",
    "tasveer bhejo",
    "picture bhejo",
    "images bhejo",
    "catalog bhejo",
    "list bhejo",
    "kon si cheezen hain",
    "kya sell karte ho",
    "kya offer karte ho",
    "aap ke paas kya kya hai",
    "mujhe options dikhao",
    "available designs dikhao",
    "rates bata do",
    "iski price",
    "ye kitne ka hai",
    "services ka batao",
)

_SERVICE_TERMS = {"service", "services", "offer", "provide", "providing", "business", "company", "support", "help"}
_SERVICE_CATALOG_TERMS = {"service", "services", "offer", "provide", "providing"}
_SERVICE_PHRASES = (
    "what services",
    "which services",
    "show services",
    "show me services",
    "list services",
    "available services",
    "services do you offer",
    "services do you provide",
    "what do you offer",
    "what do you provide",
    "what are your services",
)
_AMBIGUOUS_PRODUCT_TERMS = {"order", "item", "items", "available", "availability"}
_STRONG_PRODUCT_TERMS = _PRODUCT_TERMS - _AMBIGUOUS_PRODUCT_TERMS
_SARCASM_PHRASES = (
    "thanks for nothing",
    "great service",
    "nice service",
    "great job",
    "nice job",
    "yeah right",
    "sure whatever",
    "as expected",
    "typical",
    "very helpful",
    "so helpful",
)
_PASSIVE_AGGRESSIVE_TERMS = {
    "whatever",
    "fine",
    "great",
    "nice",
    "brilliant",
    "wow",
    "typical",
    "useless",
    "nothing",
}
_NEGATIVE_CONTEXT_TERMS = {
    "sorry",
    "apologize",
    "unavailable",
    "failed",
    "cannot",
    "can't",
    "not able",
    "problem",
    "issue",
    "delay",
    "refund",
    "complaint",
}

_ORDER_TERMS = {
    "order",
    "buy",
    "purchase",
    "checkout",
    "book",
    "reserve",
    "confirm",
    "proceed",
    "finalize",
    "done",
}
_ORDER_PHRASES = (
    "place order",
    "place an order",
    "confirm order",
    "take my order",
    "i want this",
    "i want this one",
    "i need this",
    "i need this one",
    "selected item",
    "selected product",
    "this product",
    "order this",
    "buy this",
    "purchase this",
    "deliver this",
    "send this",
    "send it",
    "i want to buy",
    "i want to order",
    "i want to purchase",
    "i want to place an order",
    "i would like to order",
    "i would like to buy",
    "how can i buy",
    "how can i purchase",
    "how can i order",
    "how do i get this",
    "how to place order",
    "how can i get it delivered",
    "order process",
    "checkout",
    "proceed",
    "finalize",
    "order karna hai",
    "order place karna hai",
    "ye order karna hai",
    "ye lena hai",
    "ye chahiye",
    "yahi chahiye",
    "ye wala chahiye",
    "ye wala order karna hai",
    "isko order karna hai",
    "khareedna hai",
    "buy karna hai",
    "purchase karna hai",
    "booking karni hai",
    "confirm kar do",
    "order confirm",
    "deliver kar do",
    "bhej do",
    "mujhe ye chahiye",
    "main ye lena chahta hun",
    "main ye lena chahti hun",
    "main ye lena chahta hoon",
    "main lena chahta hoon",
    "main order karna chahta hoon",
    "main khareedna chahta hoon",
    "iska order kar dein",
    "kaise order karun",
    "kaise purchase karun",
    "kaise buy karun",
    "theek hai ye wala de dein",
    "mujhe same ye chahiye",
    "iska parcel kar dein",
    "isko finalize karte hain",
    "ye pack kar dein",
)
_SELECTED_ITEM_PHRASES = (
    "this one",
    "same item",
    "selected item",
    "selected product",
    "this product",
    "this item",
    "ye wala",
    "yahi",
    "same ye",
    "mujhe ye",
)
_IMAGE_REQUEST_PHRASES = (
    "send pictures",
    "send images",
    "show pictures",
    "show images",
    "show photos",
    "pictures again",
    "images again",
    "show images again",
    "send images again",
    "tasveer bhejo",
    "picture bhejo",
    "images bhejo",
)
_ORDER_DETAIL_TERMS = {
    "quantity",
    "qty",
    "address",
    "delivery",
    "deliver",
    "phone",
    "number",
    "contact",
    "name",
    "size",
    "color",
    "colour",
    "medium",
    "large",
    "small",
    "black",
    "white",
    "house",
    "street",
    "road",
    "sector",
}
_ORDER_CONFIRM_TERMS = {"confirm", "confirmed", "yes", "ok", "okay", "done", "proceed", "finalize"}
_ORDER_CANCEL_PHRASES = ("cancel", "stop order", "do not order", "don't order", "leave it", "changed my mind")
_ORDER_CANCEL_SHORT_REJECTIONS = {"no", "nope", "nah", "reset"}
_ORDER_CANCEL_NON_REJECTIONS = {"no problem", "no worries"}
_NUMBER_SELECTION_WORDS = {
    "first",
    "second",
    "third",
    "pehla",
    "dosra",
    "doosra",
    "teesra",
}


def normalize_message_text(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9\s]", " ", str(text or "").strip().lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _context_bool(context: dict[str, Any] | None, *keys: str) -> bool:
    payload = dict(context or {})
    return any(bool(payload.get(key)) for key in keys)


def _context_int(context: dict[str, Any] | None, *keys: str) -> int:
    payload = dict(context or {})
    for key in keys:
        try:
            value = int(payload.get(key) or 0)
        except Exception:
            value = 0
        if value:
            return value
    return 0


def _find_phrase_matches(normalized: str, phrases: tuple[str, ...]) -> list[str]:
    return [phrase for phrase in phrases if phrase and phrase in normalized]


def _is_numeric_product_selection(normalized: str) -> bool:
    if not normalized:
        return False
    if re.fullmatch(r"\d{1,2}", normalized):
        return True
    if re.fullmatch(r"(?:number|option|product|item)\s+\d{1,2}", normalized):
        return True
    return normalized in _NUMBER_SELECTION_WORDS or any(
        re.fullmatch(rf"{re.escape(word)}(?:\s+one)?", normalized)
        for word in _NUMBER_SELECTION_WORDS
    )


def _is_active_order_cancel_message(normalized: str) -> bool:
    if normalized in _ORDER_CANCEL_NON_REJECTIONS:
        return False
    return (
        normalized in _ORDER_CANCEL_SHORT_REJECTIONS
        or normalized.startswith("no i ")
        or any(phrase in normalized for phrase in _ORDER_CANCEL_PHRASES)
    )


def _clean_extracted_product_text(value: str) -> str:
    cleaned = normalize_message_text(value)
    if not cleaned:
        return ""
    cleaned = re.split(
        r"\b(?:please|pls|phone|number|contact|address|deliver|delivery|qty|quantity|size|color|colour|price|rate|qeemat)\b",
        cleaned,
        maxsplit=1,
    )[0]
    stop_words = {
        "this",
        "that",
        "the",
        "a",
        "an",
        "product",
        "products",
        "item",
        "items",
        "one",
        "it",
        "ye",
        "yahi",
        "wala",
        "wali",
        "isko",
        "is",
        "ka",
        "ki",
        "ke",
        "know",
        "about",
        "offering",
        "offer",
        "options",
        "option",
        "available",
        "tell",
        "me",
        "us",
        "your",
        "our",
        "any",
        "some",
        "all",
        "more",
        "want",
        "need",
        "like",
        "how",
        "what",
        "which",
        "do",
        "does",
        "did",
        "get",
        "have",
        "see",
        "show",
        "list",
        "catalog",
        "catalogue",
        "services",
        "service",
        "stuff",
        "things",
        "thing",
        # verb/intent words that are not product names
        "suggest", "suggested", "suggestion", "suggestions",
        "recommend", "recommended", "recommendation", "recommendations",
        "something", "anything", "everything", "nothing",
        "help", "assist", "assistance",
        "back", "later", "now", "here", "there",
        "details", "detail", "info", "information",
        "buy", "order", "purchase",
        "please", "thanks", "thank",
    }
    tokens = [token for token in cleaned.split() if token not in stop_words]
    return " ".join(tokens).strip()


# Generic product/catalog query phrases that must NOT be treated as product names.
_GENERIC_PRODUCT_QUERY_TERMS = {
    "products",
    "product",
    "catalog",
    "catalogue",
    "services",
    "service",
    "offering",
    "offer",
    "options",
    "option",
    "available",
    "availability",
    "items",
    "item",
    "collection",
    "inventory",
    "list",
    "what do you have",
    "what you have",
    "what you offer",
    "what you sell",
    "what you are offering",
}


def _is_generic_product_query(normalized: str) -> bool:
    """Return True if the message is a generic catalog browse, not a specific product lookup."""
    tokens = set(normalized.split())
    generic_hits = tokens & {
        "products",
        "product",
        "catalog",
        "catalogue",
        "services",
        "service",
        "offering",
        "offer",
        "options",
        "available",
        "items",
        "collection",
        "inventory",
    }
    if not generic_hits:
        return False
    # If the only meaningful content is generic product words + common intent words,
    # treat as generic query
    intent_words = {
        "i",
        "want",
        "need",
        "know",
        "about",
        "tell",
        "show",
        "see",
        "list",
        "what",
        "which",
        "do",
        "does",
        "have",
        "you",
        "your",
        "are",
        "the",
        "a",
        "an",
        "and",
        "or",
        "to",
        "me",
        "us",
        "all",
        "some",
        "any",
        "buy",
        "order",
        "purchase",
        "get",
        "how",
        "can",
        "would",
        "kya",
        "hain",
        "hai",
        "batao",
        "dikhao",
    }
    meaningful_tokens = tokens - generic_hits - intent_words
    # If no meaningful tokens remain (i.e., no actual product name words), it is generic
    return len(meaningful_tokens) == 0


def extract_product_text_from_message(text: str) -> str:
    normalized = normalize_message_text(text)
    if not normalized:
        return ""
    # Skip extraction for generic catalog queries
    if _is_generic_product_query(normalized):
        return ""
    patterns = (
        r"\b(?:place\s+order|place\s+an\s+order|order)\s+(?:(?:of|for)\s+)?(.+)",
        r"\b(?:buy|purchase|book|reserve)\s+(.+)",
        r"\bi\s+(?:want|need)\s+to\s+(?:buy|purchase|order)\s+(.+)",
        r"\bmujhe\s+(.+?)\s+(?:chahiye|chaiye|lena|khareedna)\b",
        r"\b(.+?)\s+(?:chahiye|chaiye|lena\s+hai|khareedna\s+hai|order\s+karna\s+hai)\b",
        r"\b(?:price|rate|qeemat|details?)\s+(?:(?:of|for)\s+)?(.+)",
        r"\b(?:is|kya)\s+(.+?)\s+(?:available|milta|maujood)\b",
        r"\b(.+?)\s+(?:price|rate|qeemat|available)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        extracted = _clean_extracted_product_text(match.group(1))
        if not extracted:
            continue
        if _is_numeric_product_selection(extracted):
            continue
        _intent_only = {
            "want", "need", "buy", "purchase", "order", "send", "deliver",
            "suggest", "suggested", "suggestion", "recommend", "recommended",
            "something", "anything", "back", "help", "detail", "details",
            "info", "information", "more", "please", "now", "here",
        }
        if extracted in _intent_only:
            continue
        return extracted
    return ""


def _legacy_intent_for_route(route: str) -> str:
    if route == "product_flow":
        return "product_query"
    if route == "product_selection":
        return "product_selection"
    if route == "order_continuation":
        return "order_continuation"
    if route == "clarification_needed":
        return "clarification_needed"
    if route in {"order_flow", "mixed_product_order"}:
        return "order_intent"
    return "normal_support"


def _intent_result(
    route: str,
    *,
    confidence: str = "medium",
    product_intent: bool = False,
    order_intent: bool = False,
    image_request: bool = False,
    price_request: bool = False,
    selected_item_reference: bool = False,
    extracted_product_text: str = "",
    reason: str = "",
    matched_terms: list[str] | None = None,
    needs_clarification: bool = False,
    should_send_images: bool = False,
) -> dict[str, Any]:
    matched = list(dict.fromkeys(matched_terms or []))
    return {
        "route": route,
        "intent": _legacy_intent_for_route(route),
        "confidence": confidence,
        "product_intent": bool(product_intent),
        "order_intent": bool(order_intent),
        "image_request": bool(image_request),
        "price_request": bool(price_request),
        "selected_item_reference": bool(selected_item_reference),
        "extracted_product_text": _clean_extracted_product_text(extracted_product_text),
        "reason": reason,
        "matched_terms": matched,
        "needs_clarification": bool(needs_clarification),
        "should_send_images": bool(should_send_images),
    }


def classify_product_order_demand(text: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Neutral deterministic product/order classifier used before catalog or LLM fallback."""
    normalized = normalize_message_text(text)
    context = dict(context or {})
    active_order = _context_bool(context, "active_order", "has_active_order", "active_order_id")
    catalog_context = _context_bool(
        context,
        "catalog_recent",
        "product_context_present",
        "has_catalog_context",
        "product_images_recent",
        "has_product_history",
    ) or _context_int(context, "shown_product_count", "product_count", "recent_product_count") > 0
    product_count = _context_int(context, "shown_product_count", "product_count", "recent_product_count")
    matched_images = _find_phrase_matches(normalized, _IMAGE_REQUEST_PHRASES)
    matched_order = _find_phrase_matches(normalized, _ORDER_PHRASES)
    matched_product = _find_phrase_matches(normalized, _PRODUCT_PHRASES)
    tokens = set(normalized.split())
    image_request = bool(
        matched_images
        or tokens & {"image", "images", "photo", "photos", "picture", "pictures", "tasveer"}
    )
    price_request = bool(
        tokens & {"price", "prices", "pricing", "cost", "rate", "rates", "qeemat", "kitne"}
        or any(phrase in normalized for phrase in ("how much", "ye kitne ka", "price kya", "rate kya", "qeemat kya"))
    )
    availability_request = bool(tokens & {"available", "availability", "stock"} or "kya available" in normalized)
    details_request = bool(tokens & {"detail", "details", "features", "specs", "designs", "options"})
    selected_matches = _find_phrase_matches(normalized, _SELECTED_ITEM_PHRASES)
    numeric_selection = _is_numeric_product_selection(normalized)
    extracted_product_text = extract_product_text_from_message(normalized)

    if not normalized:
        return _intent_result("normal_support", confidence="low", reason="empty_message")

    if active_order:
        if _is_active_order_cancel_message(normalized):
            return _intent_result(
                "order_continuation",
                confidence="high",
                order_intent=True,
                reason="active_order_cancel",
                matched_terms=["cancel"],
            ) | {"intent": "order_cancel"}
        if tokens & _ORDER_CONFIRM_TERMS:
            return _intent_result(
                "order_continuation",
                confidence="high",
                order_intent=True,
                reason="active_order_confirmation",
                matched_terms=sorted(tokens & _ORDER_CONFIRM_TERMS),
            ) | {"intent": "order_confirmation"}
        if image_request:
            return _intent_result(
                "order_continuation",
                confidence="high",
                order_intent=True,
                image_request=image_request,
                reason="active_order_blocks_image_resend",
                matched_terms=matched_images,
            )
        if tokens & _ORDER_DETAIL_TERMS or re.search(r"\+?\d[\d\s().-]{5,}\d", normalized):
            return _intent_result(
                "order_continuation",
                confidence="high",
                order_intent=True,
                extracted_product_text=extracted_product_text,
                reason="active_order_detail_message",
                matched_terms=sorted(tokens & _ORDER_DETAIL_TERMS),
            )
        return _intent_result(
            "order_continuation",
            confidence="medium",
            order_intent=True,
            extracted_product_text=extracted_product_text,
            reason="active_order_default",
        )

    order_term_match = bool(matched_order or tokens & _ORDER_TERMS)
    service_catalog_term_match = bool(tokens & _SERVICE_CATALOG_TERMS or any(phrase in normalized for phrase in _SERVICE_PHRASES))
    product_term_match = bool(
        matched_product
        or tokens & _PRODUCT_TERMS
        or service_catalog_term_match
        or "do you have" in normalized
        or "tell me details" in normalized
    )
    product_intent = bool(product_term_match or image_request or price_request or availability_request or details_request)
    order_intent = bool(order_term_match or selected_matches)
    matched_terms = [
        *matched_order,
        *matched_product,
        *matched_images,
        *sorted((tokens & _ORDER_TERMS) | (tokens & _PRODUCT_TERMS) | (tokens & _SERVICE_CATALOG_TERMS)),
    ]

    if image_request and not order_term_match and not selected_matches:
        return _intent_result(
            "product_flow",
            confidence="high",
            product_intent=True,
            image_request=True,
            price_request=price_request,
            extracted_product_text=extracted_product_text,
            reason="explicit_image_request",
            matched_terms=matched_images,
            should_send_images=True,
        )

    if catalog_context and numeric_selection:
        return _intent_result(
            "product_selection",
            confidence="high",
            product_intent=True,
            selected_item_reference=True,
            reason="numeric_selection_after_catalog",
            matched_terms=[normalized],
        )

    if tokens & _ORDER_CONFIRM_TERMS and len(tokens) <= 3:
        return _intent_result(
            "clarification_needed",
            confidence="medium",
            order_intent=True,
            reason="confirmation_without_active_order",
            matched_terms=sorted(tokens & _ORDER_CONFIRM_TERMS),
            needs_clarification=True,
        )

    if selected_matches and catalog_context:
        if product_count > 1:
            return _intent_result(
                "clarification_needed",
                confidence="high",
                order_intent=True,
                selected_item_reference=True,
                extracted_product_text=extracted_product_text,
                reason="selected_item_multiple_products",
                matched_terms=selected_matches,
                needs_clarification=True,
            )
        return _intent_result(
            "order_flow",
            confidence="high",
            order_intent=True,
            selected_item_reference=True,
            extracted_product_text=extracted_product_text,
            reason="selected_item_after_catalog",
            matched_terms=selected_matches,
        )
    if selected_matches and not catalog_context:
        if extracted_product_text:
            return _intent_result(
                "order_flow",
                confidence="high",
                order_intent=True,
                selected_item_reference=True,
                extracted_product_text=extracted_product_text,
                reason="selected_item_with_product_text",
                matched_terms=selected_matches,
            )
        return _intent_result(
            "clarification_needed",
            confidence="medium",
            order_intent=True,
            selected_item_reference=True,
            reason="selected_item_without_product_context",
            matched_terms=selected_matches,
            needs_clarification=True,
        )

    if order_intent and product_intent:
        return _intent_result(
            "mixed_product_order",
            confidence="high",
            product_intent=True,
            order_intent=True,
            image_request=image_request,
            price_request=price_request,
            selected_item_reference=bool(selected_matches),
            extracted_product_text=extracted_product_text,
            reason="mixed_product_and_order_terms",
            matched_terms=matched_terms,
        )

    if order_intent:
        if any(term in normalized for term in ("this", "it", "ye", "yahi", "isko")) and not catalog_context and not extracted_product_text:
            return _intent_result(
                "clarification_needed",
                confidence="medium",
                order_intent=True,
                selected_item_reference=bool(selected_matches),
                reason="order_reference_without_product_context",
                matched_terms=matched_terms,
                needs_clarification=True,
            )
        return _intent_result(
            "order_flow",
            confidence="high",
            order_intent=True,
            selected_item_reference=bool(selected_matches),
            extracted_product_text=extracted_product_text,
            reason="explicit_order_terms",
            matched_terms=matched_terms,
        )

    if catalog_context and (tokens & _ORDER_DETAIL_TERMS or re.search(r"\b\d{1,3}\s*(?:pcs|pieces|items|units|x)\b", normalized)):
        return _intent_result(
            "order_continuation",
            confidence="medium",
            order_intent=True,
            extracted_product_text=extracted_product_text,
            reason="order_detail_after_catalog",
            matched_terms=sorted(tokens & _ORDER_DETAIL_TERMS),
        )

    if product_intent:
        return _intent_result(
            "product_flow",
            confidence="high" if matched_product or image_request or price_request else "medium",
            product_intent=True,
            image_request=image_request,
            price_request=price_request,
            extracted_product_text=extracted_product_text,
            reason="product_or_service_terms",
            matched_terms=matched_terms,
            should_send_images=image_request,
        )

    return _intent_result("normal_support", confidence="low", reason="no_product_or_order_signal")


def route_product_order_intent(text: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    return classify_product_order_demand(text, context)


def detect_product_intent(message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    result = route_product_order_intent(message, context)
    return result if result.get("intent") == "product_query" else _intent_result(
        "normal_support",
        confidence="low",
        reason=result.get("reason", ""),
        matched_terms=result.get("matched_terms", []),
    )


def detect_order_intent(message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    result = route_product_order_intent(message, context)
    if result.get("intent") == "clarification_needed" and str(result.get("reason") or "").startswith(
        ("selected_", "order_reference", "confirmation_without")
    ):
        return result
    return result if result.get("intent") in {"order_intent", "order_confirmation", "order_cancel"} else _intent_result(
        "normal_support",
        confidence="low",
        reason=result.get("reason", ""),
        matched_terms=result.get("matched_terms", []),
    )


def detect_order_continuation(message: str, active_order: dict[str, Any] | bool = False, context: dict[str, Any] | None = None) -> dict[str, Any]:
    routing_context = {**dict(context or {}), "active_order": bool(active_order)}
    result = route_product_order_intent(message, routing_context)
    return result if result.get("intent") in {"order_continuation", "order_confirmation", "order_cancel"} else _intent_result(
        "normal_support",
        confidence="low",
        reason=result.get("reason", ""),
        matched_terms=result.get("matched_terms", []),
    )


def should_send_product_images(message: str, context: dict[str, Any] | None = None) -> bool:
    result = route_product_order_intent(message, context)
    return bool(result.get("intent") == "product_query" and result.get("should_send_images"))


def should_start_or_continue_order(message: str, context: dict[str, Any] | None = None) -> bool:
    result = route_product_order_intent(message, context)
    if result.get("intent") == "clarification_needed":
        return str(result.get("reason") or "").startswith(("selected_", "order_reference"))
    return result.get("intent") in {
        "order_intent",
        "order_continuation",
        "order_confirmation",
        "order_cancel",
    }


def assess_lightweight_conversational_risk(
    text: str,
    *,
    previous_ai_message: str = "",
) -> dict[str, Any]:
    raw = str(text or "").strip().lower()
    normalized = normalize_message_text(raw)
    tokens = set(normalized.split())
    previous = str(previous_ai_message or "").strip().lower()
    reasons: list[str] = []
    sarcasm_score = 0.0
    ambiguity_score = 0.0
    negativity_score = 0.0

    if not normalized:
        return {
            "should_bypass": True,
            "sarcasm_score": 0.0,
            "ambiguity_score": 0.0,
            "negativity_score": 0.0,
            "reasons": [],
        }

    if any(phrase in raw for phrase in _SARCASM_PHRASES):
        sarcasm_score += 0.75
        reasons.append("sarcasm_phrase")
    if any(marker in raw for marker in ("🙄", "😒", "👎", "/s")):
        sarcasm_score += 0.35
        reasons.append("negative_or_sarcastic_emoji")
    if "!" in raw and tokens & _PASSIVE_AGGRESSIVE_TERMS:
        sarcasm_score += 0.18
        reasons.append("emphatic_acknowledgement")
    if tokens & {"nothing", "whatever", "useless", "bad", "poor", "terrible", "wrong"}:
        negativity_score += 0.55
        reasons.append("negative_acknowledgement")
    if previous and any(term in previous for term in _NEGATIVE_CONTEXT_TERMS):
        ambiguity_score += 0.35
        reasons.append("negative_previous_context")
    if tokens & _PASSIVE_AGGRESSIVE_TERMS and tokens & _LOW_VALUE_EXACT:
        ambiguity_score += 0.35
        reasons.append("ambiguous_acknowledgement_tone")
    if len(tokens) <= 4 and tokens & {"fine", "sure", "ok", "okay"} and previous and "?" not in previous:
        ambiguity_score += 0.15
        reasons.append("short_ack_without_clear_question")

    should_bypass = (sarcasm_score < 0.45 and negativity_score < 0.35 and ambiguity_score < 0.5)
    return {
        "should_bypass": should_bypass,
        "sarcasm_score": round(min(sarcasm_score, 1.0), 3),
        "ambiguity_score": round(min(ambiguity_score, 1.0), 3),
        "negativity_score": round(min(negativity_score, 1.0), 3),
        "reasons": reasons,
    }


def is_low_value_message(text: str) -> bool:
    normalized = normalize_message_text(text)
    if not normalized:
        return True
    risk = assess_lightweight_conversational_risk(normalized)
    if not risk.get("should_bypass", True):
        return False
    if normalized in _LOW_VALUE_EXACT:
        return True
    if normalized in _SOCIAL_EXACT:
        return True
    tokens = normalized.split()
    if len(tokens) <= 3 and any(token in _LOW_VALUE_EXACT for token in tokens):
        product_overlap = any(token in _PRODUCT_TERMS for token in tokens)
        service_overlap = any(token in _SERVICE_TERMS for token in tokens)
        return not (product_overlap or service_overlap)
    return False


def should_lightweight_bypass(text: str, *, previous_ai_message: str = "") -> bool:
    if not is_low_value_message(text):
        return False
    return bool(assess_lightweight_conversational_risk(text, previous_ai_message=previous_ai_message).get("should_bypass", True))


def lightweight_route_message(
    text: str,
    *,
    previous_intent: str = "",
    previous_ai_message: str = "",
) -> dict[str, Any]:
    normalized = normalize_message_text(text)
    risk = assess_lightweight_conversational_risk(text, previous_ai_message=previous_ai_message)
    if not normalized:
        return {
            "intent": "general_question",
            "confidence": 0.0,
            "entities": {},
            "urgency": "low",
            "source": "lightweight_empty",
            "low_value": True,
            "lightweight_risk": risk,
        }

    if not risk.get("should_bypass", True) and (
        normalized in _LOW_VALUE_EXACT or any(token in _LOW_VALUE_EXACT for token in normalized.split())
    ):
        return {}

    product_order_route = route_product_order_intent(
        text,
        {
            "has_catalog_context": bool(previous_ai_message),
            "has_product_history": previous_intent in PRODUCT_INTENTS,
        },
    )
    product_order_intent = str(product_order_route.get("intent") or "")
    product_order_route_name = str(product_order_route.get("route") or "")
    if product_order_route_name == "product_selection":
        return {
            "intent": "product_selection",
            "confidence": 0.9,
            "entities": {
                "route_selected": "product_selection",
                "matched_terms": product_order_route.get("matched_terms", []),
            },
            "urgency": "medium",
            "source": "deterministic_product_order_router",
            "low_value": False,
        }
    if (
        product_order_route_name == "mixed_product_order"
        and not str(product_order_route.get("extracted_product_text") or "").strip()
    ):
        return {
            "intent": "product_catalog_question",
            "confidence": 0.9,
            "entities": {
                "route_selected": "mixed_product_order",
                "matched_terms": product_order_route.get("matched_terms", []),
                "order_handoff_after_selection": True,
            },
            "urgency": "medium",
            "source": "deterministic_product_order_router",
            "low_value": False,
        }
    if product_order_intent in {"order_intent", "order_confirmation", "order_cancel"}:
        return {
            "intent": "order_intent",
            "confidence": 0.92 if product_order_route.get("confidence") == "high" else 0.78,
            "entities": {
                "route_selected": product_order_intent,
                "matched_terms": product_order_route.get("matched_terms", []),
                "needs_clarification": bool(product_order_route.get("needs_clarification")),
            },
            "urgency": "medium",
            "source": "deterministic_product_order_router",
            "low_value": False,
        }
    if product_order_intent == "product_query" and product_order_route.get("matched_terms"):
        intent_name = "product_catalog_question"
        if product_order_route.get("should_send_images"):
            intent_name = "product_image_request"
        elif any(term in normalized for term in ("price", "pricing", "cost", "rate", "qeemat", "kitne")):
            intent_name = "pricing_question"
        elif any(term in normalized for term in ("available", "availability", "stock")):
            intent_name = "availability_question"
        return {
            "intent": intent_name,
            "confidence": 0.88 if product_order_route.get("confidence") == "high" else 0.76,
            "entities": {
                "route_selected": product_order_intent,
                "matched_terms": product_order_route.get("matched_terms", []),
            },
            "urgency": "medium" if intent_name in {"pricing_question", "availability_question"} else "low",
            "source": "deterministic_product_order_router",
            "low_value": False,
        }

    if normalized in _GREETING_EXACT:
        return {
            "intent": "greeting",
            "confidence": 0.98,
            "entities": {},
            "urgency": "low",
            "source": "lightweight_rule",
            "low_value": True,
            "lightweight_risk": risk,
        }
    if normalized in _SOCIAL_EXACT:
        return {
            "intent": "social",
            "confidence": 0.98,
            "entities": {},
            "urgency": "low",
            "source": "lightweight_rule",
            "low_value": True,
            "lightweight_risk": risk,
        }
    if normalized in _GRATITUDE_EXACT:
        return {
            "intent": "gratitude",
            "confidence": 0.98,
            "entities": {},
            "urgency": "low",
            "source": "lightweight_rule",
            "low_value": True,
            "lightweight_risk": risk,
        }
    if normalized in _ACK_EXACT:
        return {
            "intent": "acknowledgement",
            "confidence": 0.96,
            "entities": {"previous_intent": previous_intent or ""},
            "urgency": "low",
            "source": "lightweight_rule",
            "low_value": True,
            "lightweight_risk": risk,
        }

    if any(phrase in normalized for phrase in _SERVICE_PHRASES):
        return {
            "intent": "service_question",
            "confidence": 0.84,
            "entities": {},
            "urgency": "low",
            "source": "lightweight_rule",
            "low_value": False,
        }

    if any(phrase in normalized for phrase in _PRODUCT_PHRASES):
        intent_name = "product_catalog_question"
        if any(term in normalized for term in ("image", "images", "photo", "photos", "picture", "pictures")):
            intent_name = "product_image_request"
        elif any(term in normalized for term in ("price", "pricing", "cost", "rate", "how much")):
            intent_name = "pricing_question"
        elif any(term in normalized for term in ("buy", "order", "checkout", "purchase")):
            intent_name = "buying_intent"
        return {
            "intent": intent_name,
            "confidence": 0.86,
            "entities": {},
            "urgency": "medium" if intent_name in {"buying_intent", "pricing_question"} else "low",
            "source": "lightweight_rule",
            "low_value": False,
        }

    tokens = set(normalized.split())
    if "order" in tokens and (
        any(term in normalized for term in ("delayed", "tracking", "shipped", "shipping", "delivery"))
        or tokens & {"where", "status", "late", "arrived"}
    ):
        return {
            "intent": "shipping_question",
            "confidence": 0.82,
            "entities": {},
            "urgency": "medium",
            "source": "lightweight_rule",
            "low_value": False,
        }
    support_issue_terms = {"issue", "problem", "refund", "cancel", "complaint", "delayed", "late", "wrong", "broken"}
    if "order" in tokens and tokens & support_issue_terms:
        return {
            "intent": "support_request",
            "confidence": 0.82,
            "entities": {},
            "urgency": "medium",
            "source": "lightweight_rule",
            "low_value": False,
        }
    if tokens & _PRODUCT_TERMS:
        intent_name = "product_catalog_question"
        if tokens & {"image", "images", "photo", "photos", "picture", "pictures"}:
            intent_name = "product_image_request"
        elif tokens & {"price", "pricing", "cost", "rate"}:
            intent_name = "pricing_question"
        elif tokens & {"buy", "purchase", "order", "checkout"}:
            intent_name = "buying_intent"
        elif tokens & {"available", "availability", "stock"}:
            intent_name = "availability_question"
        elif tokens & {"recommend", "suggest"}:
            intent_name = "product_recommendation"
        confidence = 0.82 if explicit_product_signal(normalized) else 0.66
        return {
            "intent": intent_name,
            "confidence": confidence,
            "entities": {},
            "urgency": "medium" if intent_name in {"buying_intent", "pricing_question"} else "low",
            "source": "lightweight_rule",
            "low_value": False,
        }

    if len(normalized.split()) <= 4 and previous_ai_message and previous_intent in PRODUCT_INTENTS:
        if any(term in normalized for term in ("price", "cost", "image", "photo", "picture", "available", "stock")):
            return {
                "intent": "pricing_question" if any(term in normalized for term in ("price", "cost")) else previous_intent,
                "confidence": 0.78,
                "entities": {"previous_intent": previous_intent},
                "urgency": "medium",
                "source": "lightweight_context_rule",
                "low_value": False,
            }

    # MiniLM semantic fallback — handles complex/ambiguous messages the rule engine misses
    try:
        from services.ai_service.local_ml import classify_intent as _ml_intent  # noqa: PLC0415
        ml = _ml_intent(text)
        if ml.get("confidence", 0.0) >= 0.45 and ml.get("intent") not in {"general_question"}:
            return {
                "intent": ml["intent"],
                "confidence": ml["confidence"],
                "entities": {},
                "urgency": ml.get("urgency", "low"),
                "source": "local_minilm",
                "low_value": False,
            }
    except Exception:
        pass

    return {}


def explicit_product_signal(text: str) -> bool:
    normalized = normalize_message_text(text)
    if not normalized:
        return False
    if any(phrase in normalized for phrase in _PRODUCT_PHRASES):
        return True
    tokens = set(normalized.split())
    if tokens & _STRONG_PRODUCT_TERMS:
        return True
    if "order" in tokens:
        return bool(tokens & {"buy", "purchase", "checkout", "link", "place"})
    return False


def is_high_confidence_product_intent(
    intent: dict[str, Any] | None,
    text: str = "",
    *,
    has_product_history: bool = False,
) -> bool:
    payload = dict(intent or {})
    intent_name = str(payload.get("intent") or "").strip().lower()
    if intent_name not in PRODUCT_INTENTS:
        return False
    try:
        confidence = float(payload.get("confidence") or 0.0)
    except Exception:
        confidence = 0.0
    normalized = normalize_message_text(text)
    explicit_signal = explicit_product_signal(normalized)

    if intent_name == "website_link_request":
        website_purchase_signal = any(
            phrase in normalized
            for phrase in (
                "where can i buy",
                "where can i order",
                "how can i order",
                "order link",
                "checkout",
                "buy link",
                "purchase link",
            )
        )
        return confidence >= 0.78 and (website_purchase_signal or has_product_history)

    if intent_name in {"buying_intent", "order_intent"}:
        routed = route_product_order_intent(text, {"has_catalog_context": has_product_history})
        if routed.get("intent") in {"order_intent", "order_continuation", "order_confirmation", "clarification_needed"}:
            return False
        return confidence >= 0.9 or (confidence >= 0.78 and (explicit_signal or has_product_history))

    if confidence >= 0.8:
        return True
    if confidence >= 0.68 and explicit_signal:
        return True
    if confidence >= 0.72 and has_product_history and not is_low_value_message(text):
        return True
    return False


def should_fetch_knowledge_context(intent: dict[str, Any] | None, text: str = "") -> bool:
    intent_name = str((intent or {}).get("intent") or "").strip().lower()
    if intent_name in KNOWLEDGE_INTENTS:
        return True
    normalized = normalize_message_text(text)
    if not normalized or is_low_value_message(normalized):
        return False
    return any(term in normalized for term in _SERVICE_TERMS)
