"""Deterministic grounded answers for high-volume commerce questions.

Catalog browsing, buy intent, budget filtering, image requests, and simple
referential follow-ups can be answered directly from retrieved product context.
Company profile questions intentionally stay on the LLM path so structured
company context is rewritten naturally instead of leaking raw key-value facts.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from services.conversation_engine.schemas import ContextChunk, ProductLink, SourceType


@dataclass
class DeterministicAnswer:
    answer: str
    product_links: list[ProductLink]
    sources_used: list[SourceType]
    confidence: float = 0.98


_CATALOG_RE = re.compile(
    r"\b("
    r"what\s+(products?|items?)\s+(do\s+you\s+)?(have|sell|offer|carry)|"
    r"what\s+do\s+you\s+(have|sell|offer|carry)|"
    r"show\s+(me\s+)?(your\s+|all\s+)?(products?|items?|catalog|catalogue|collection)|"
    r"(list|browse|see)\s+(your\s+|all\s+)?(products?|items?|catalog|catalogue|collection)|"
    r"product\s+(list|catalog|catalogue|range|collection)|"
    r"available\s+(products?|items?)|"
    r"all\s+(your\s+)?(products?|items?)"
    r")\b",
    re.IGNORECASE,
)
_PURCHASE_RE = re.compile(
    r"\b("
    r"buy|purchase|order|checkout|place\s+an\s+order|add\s+to\s+cart|"
    r"want\s+to\s+(buy|purchase|order|get)|how\s+can\s+i\s+(buy|purchase|order|get)|"
    r"i\s+want\s+this|i\s+want\s+that|this\s+one|that\s+one"
    r")\b",
    re.IGNORECASE,
)
_BUDGET_RE = re.compile(r"\b(budget|affordable|cheap|under|below|less than|price range|within)\b", re.IGNORECASE)
_IMAGE_RE = re.compile(
    r"\b(image|images|photo|photos|picture|pictures|show\s+me\s+(?:it|them|those|these)|see\s+(?:it|them|those|these))\b",
    re.IGNORECASE,
)
_REFERENTIAL_MORE_RE = re.compile(
    r"\b("
    r"more\s+of\s+(?:them|those|these|it)|"
    r"show\s+me\s+more|"
    r"(?:them|those|these)\s+more|"
    r"all\s+of\s+(?:them|those|these)|"
    r"the\s+ones\s+you\s+showed|"
    r"the\s+one\s+you\s+showed"
    r")\b",
    re.IGNORECASE,
)
_PRODUCT_BROWSE_RE = re.compile(
    r"\b("
    r"show\s+me|list|browse|see|what\s+(?:.+\s+)?(?:do\s+you\s+have|are\s+available)|"
    r"available|options|collection|catalog|catalogue"
    r")\b",
    re.IGNORECASE,
)


def build_grounded_answer(user_message: str, chunks: Iterable[ContextChunk]) -> DeterministicAnswer | None:
    text = str(user_message or "").strip()
    chunk_list = list(chunks or [])
    end_of_catalog = _end_of_catalog_fact(chunk_list)
    products = _product_facts(chunk_list)

    if end_of_catalog and _REFERENTIAL_MORE_RE.search(text):
        return _end_of_catalog_answer(end_of_catalog)

    if products and _IMAGE_RE.search(text):
        return _image_answer(products)

    if products and (_PURCHASE_RE.search(text) or _BUDGET_RE.search(text)):
        return _purchase_answer(text, products)

    if products and (_CATALOG_RE.search(text) or _REFERENTIAL_MORE_RE.search(text) or _PRODUCT_BROWSE_RE.search(text)):
        return _catalog_answer(products)

    return None


def _product_facts(chunks: list[ContextChunk]) -> list[dict]:
    products: list[dict] = []
    seen: set[str] = set()
    for chunk in chunks:
        if chunk.source_type != "product":
            continue
        metadata = dict(chunk.metadata or {})
        if metadata.get("is_category_overview"):
            continue
        if metadata.get("is_end_of_catalog"):
            continue
        product_id = str(chunk.source_id or "").strip()
        name = str(metadata.get("name") or chunk.title or "").strip()
        if not product_id or not name:
            continue
        key = product_id or name.lower()
        if key in seen:
            continue
        seen.add(key)
        content = str(chunk.content or "")
        products.append(
            {
                "id": product_id,
                "name": name,
                "category": str(metadata.get("category") or _extract_field(content, "Category") or "general").strip(),
                "price": _price_display(metadata, content),
                "description": _description(content),
                "url": str(metadata.get("public_url") or _extract_field(content, "Product page") or "").strip(),
                "image_url": str(metadata.get("image_url") or "").strip(),
                "score": float(chunk.relevance_score or 0.0),
            }
        )
    products.sort(key=lambda item: item.get("score", 0.0), reverse=True)
    return products


def _end_of_catalog_fact(chunks: list[ContextChunk]) -> dict:
    for chunk in chunks:
        metadata = dict(chunk.metadata or {})
        if chunk.source_type == "product" and metadata.get("is_end_of_catalog"):
            return {
                "category": str(metadata.get("category") or "products").strip() or "products",
            }
    return {}


def _catalog_answer(products: list[dict]) -> DeterministicAnswer:
    lines = ["Here are the available products:"]
    for index, product in enumerate(products[:6], start=1):
        lines.extend(
            [
                f"{index}. {product['name']}",
                f"   Category: {product['category'] or 'general'}",
                f"   Price: {product['price']}",
                "",
            ]
        )
    if lines and lines[-1] == "":
        lines.pop()
    lines.append("Tell me which product you want to buy and I will send the direct purchase link.")
    return DeterministicAnswer(
        answer="\n".join(lines),
        product_links=_links(products[:6]),
        sources_used=["product"],
    )


def _purchase_answer(user_message: str, products: list[dict]) -> DeterministicAnswer:
    product = _select_product(user_message, products)
    url = product.get("url") or ""
    description = product.get("description") or "it is ready to order"
    if url:
        answer = (
            f"Here's the link to purchase {product['name']}: {url}. "
            f"It's priced at {product['price']} and {description}. "
            "Click the link to complete your order."
        )
    else:
        answer = (
            f"{product['name']} is priced at {product['price']} and {description}. "
            "The direct purchase link is not available right now."
        )
    return DeterministicAnswer(
        answer=answer,
        product_links=_links([product]),
        sources_used=["product"],
    )


def _image_answer(products: list[dict]) -> DeterministicAnswer | None:
    image_products = [product for product in products if product.get("image_url")]
    if not image_products:
        return None
    product = image_products[0]
    return DeterministicAnswer(
        answer=f"Here's an image of {product['name']}:",
        product_links=_links(image_products[:3]),
        sources_used=["product"],
        confidence=0.99,
    )


def _end_of_catalog_answer(fact: dict) -> DeterministicAnswer:
    category = str(fact.get("category") or "products").strip().lower() or "products"
    return DeterministicAnswer(
        answer=f"Those are all the {category} I have available. Would you like to see something else?",
        product_links=[],
        sources_used=["product"],
        confidence=0.99,
    )


def _links(products: list[dict]) -> list[ProductLink]:
    links: list[ProductLink] = []
    for product in products:
        url = str(product.get("url") or "").strip()
        if not url:
            continue
        links.append(
            ProductLink(
                product_id=str(product.get("id") or ""),
                url=url,
                name=str(product.get("name") or ""),
                image_url=str(product.get("image_url") or ""),
            )
        )
    return links


def _select_product(user_message: str, products: list[dict]) -> dict:
    query = _normalise(user_message)
    best = products[0]
    best_score = -1
    for product in products:
        name = _normalise(product.get("name", ""))
        category = _normalise(product.get("category", ""))
        score = 0
        if name and name in query:
            score += 100
        for token in set(name.split()):
            if len(token) >= 3 and token in query:
                score += 10
        if category and category in query:
            score += 8
        if score > best_score:
            best = product
            best_score = score
    return best


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def _extract_field(content: str, field: str) -> str:
    match = re.search(rf"{re.escape(field)}:\s*([^|]+)", content or "", flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _description(content: str) -> str:
    description = _extract_field(content, "Description")
    if not description or description.lower() == "no description provided.":
        return ""
    return re.sub(r"\s+", " ", description).strip()[:240]


def _price_display(metadata: dict, content: str) -> str:
    price = str(metadata.get("price") or "").strip()
    currency = str(metadata.get("price_currency") or "").strip()
    if price and currency:
        return f"{currency} {price}"
    if price:
        return price
    parsed = _extract_field(content, "Price")
    return parsed or "Not listed"
