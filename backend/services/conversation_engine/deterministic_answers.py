"""Deterministic grounded answers for high-volume commerce questions.

Catalog browsing, buy intent, budget filtering, and company profile questions
can be answered directly from retrieved context. This avoids LLM latency and
prevents a static fallback when the facts are already available.
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
_COMPANY_RE = re.compile(
    r"\b("
    r"your\s+company|your\s+business|company\s+about|business\s+about|"
    r"what\s+is\s+your\s+company|what\s+are\s+you\s+about|"
    r"who\s+are\s+you|what\s+do\s+you\s+do|about\s+you"
    r")\b",
    re.IGNORECASE,
)


def build_grounded_answer(user_message: str, chunks: Iterable[ContextChunk]) -> DeterministicAnswer | None:
    text = str(user_message or "").strip()
    chunk_list = list(chunks or [])
    products = _product_facts(chunk_list)
    company = _company_facts(chunk_list)

    if products and (_PURCHASE_RE.search(text) or _BUDGET_RE.search(text)):
        return _purchase_answer(text, products)

    if products and _CATALOG_RE.search(text):
        return _catalog_answer(products)

    if company and _COMPANY_RE.search(text):
        return _company_answer(company)

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


def _company_facts(chunks: list[ContextChunk]) -> dict:
    for chunk in chunks:
        if chunk.source_type != "company_data":
            continue
        facts: dict[str, str] = {}
        for line in str(chunk.content or "").splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            facts[key.strip().lower()] = value.strip()
        if facts:
            return facts
    return {}


def _catalog_answer(products: list[dict]) -> DeterministicAnswer:
    lines = ["Here are the available products:"]
    for index, product in enumerate(products[:6], start=1):
        lines.extend(
            [
                f"{index}. {product['name']}",
                f"   Category: {product['category'] or 'general'}",
                f"   Budget/price: {product['price']}",
            ]
        )
    lines.append("Tell me which product you want to buy and I will send the direct purchase link.")
    return DeterministicAnswer(
        answer="\n".join(lines),
        product_links=_links(products[:6]),
        sources_used=["product"],
    )


def _purchase_answer(user_message: str, products: list[dict]) -> DeterministicAnswer:
    product = _select_product(user_message, products)
    url = product.get("url") or ""
    lines = [
        f"You can buy {product['name']} here:",
        url or "The product page link is not available right now.",
        "",
        f"Name: {product['name']}",
        f"Category: {product['category'] or 'general'}",
        f"Budget/price: {product['price']}",
    ]
    if product.get("description"):
        lines.append(f"Description: {product['description']}")
    if url:
        lines.append("Click the link above to complete the purchase.")
    return DeterministicAnswer(
        answer="\n".join(lines),
        product_links=_links([product]),
        sources_used=["product"],
    )


def _company_answer(facts: dict[str, str]) -> DeterministicAnswer:
    name = facts.get("company name") or "The company"
    tagline = facts.get("tagline")
    description = facts.get("about")
    industry = facts.get("industry")
    parts: list[str] = []
    if tagline:
        parts.append(f"{name} is {tagline}.")
    elif industry:
        parts.append(f"{name} works in {industry}.")
    else:
        parts.append(f"{name} is the business behind this chat.")
    if description:
        parts.append(description.rstrip(".") + ".")
    if industry and tagline:
        parts.append(f"Industry: {industry}.")
    return DeterministicAnswer(
        answer=" ".join(parts),
        product_links=[],
        sources_used=["company_data"],
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
