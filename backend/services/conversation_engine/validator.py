"""Response validator — pure string/data checks, no LLM calls.

Checks (in order):
  1. Schema — non-empty answer, no obvious garbage.
  2. Secret leak — regex pass for tokens / passwords / API keys.
  3. Product link integrity — every URL in product_links[] points to a
     retrieved product.
  4. Factual grounding — product names and prices in the answer must appear
     in a retrieved chunk's metadata.
  5. Tone — small wordlist for profanity, no shouting.

Confidence scoring lives here too so callers can use the same module for
both validation and the confidence number that ships in the API response.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from services.conversation_engine.schemas import (
    ContextChunk,
    ProductLink,
    ValidationReport,
)


_PROFANITY = {"damn", "shit", "fuck", "asshole", "bitch"}
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}"),
    re.compile(r"password\s*[:=]\s*\S{6,}", re.IGNORECASE),
)
# Require either a $ prefix OR 2+ digit numbers to avoid false positives
# like "5-carat", "18k gold" etc. being flagged as ungrounded prices.
_PRICE_RE = re.compile(
    r"\$\s*\d[\d,]*\.?\d*\s*(?:USD|EUR|GBP|INR|PKR|AED|SAR)?"           # $price or $price CURR
    r"|\d{2,}[\d,]*\.?\d*\s*(?:USD|EUR|GBP|INR|PKR|AED|SAR)"            # 2+ digit + CURRENCY
    r"|\d{4,}[\d,]*\.?\d*",                                               # standalone 4+ digit
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_CONTEXT_TAG_RE = re.compile(
    r"</?(?:retrieved_context|company_info|product_catalog|knowledge_base|faqs|response_style|user_input|conversation_history)\b[^>]*>",
    re.IGNORECASE,
)
_SOURCE_LABEL_RE = re.compile(r"\(?Source:\s*(?:Company Data|Product Database|Knowledge Base|FAQs?|Response Style)[^) \n]*\)?", re.IGNORECASE)
_CAP_PHRASE_RE = re.compile(r"\b([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+){0,3})\b")
_SHOUTING_RE = re.compile(r"(?:\b[A-Z]{4,}\b\s+){6,}")
_PURCHASE_TERMS = {
    "buy",
    "order",
    "purchase",
    "checkout",
    "cart",
    "place",
}


@dataclass
class GroundingContext:
    chunks: list[ContextChunk]


@dataclass
class SafeValidationResult:
    answer: str
    product_links: list[ProductLink]
    report: ValidationReport
    stripped_content: list[str]


def _grounded_product_names(chunks: Iterable[ContextChunk]) -> set[str]:
    """Return the set of full grounded product names (lower-cased).

    Only full names are stored — individual word tokens are intentionally
    excluded. Adding tokens like "blue" from "Blue Jacket" would cause unrelated
    product names that share a common word (e.g. "Blue Shoes") to pass the
    grounding check incorrectly. The substring check in _check_grounding handles
    partial matches via `name in candidate` comparisons on full product names.
    """
    out: set[str] = set()
    for chunk in chunks:
        if chunk.source_type != "product":
            continue
        name = (chunk.metadata or {}).get("name") or chunk.title
        if not name:
            continue
        out.add(str(name).lower().strip())
    return out


def _grounded_prices(chunks: Iterable[ContextChunk]) -> set[str]:
    out: set[str] = set()
    for chunk in chunks:
        if chunk.source_type != "product":
            continue
        price = (chunk.metadata or {}).get("price")
        if price is None:
            continue
        out.add(_normalise_price(price))
    return out


_PRICE_NORMALISE_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _normalise_price(value: object) -> str:
    """Reduce a price string to its bare numeric form (no currency, no
    punctuation) so we can compare answer text against catalog metadata
    consistently. `$99 USD`, `99`, `99.00` all collapse to comparable forms."""
    text = str(value)
    match = _PRICE_NORMALISE_RE.search(text)
    if not match:
        return ""
    return match.group(0).replace(",", "")


def _grounded_product_ids(chunks: Iterable[ContextChunk]) -> set[str]:
    return {chunk.source_id for chunk in chunks if chunk.source_type == "product" and chunk.source_id}


def _grounded_urls(chunks: Iterable[ContextChunk]) -> set[str]:
    urls: set[str] = set()
    for chunk in chunks:
        meta = chunk.metadata or {}
        for key in ("public_url", "links", "url"):
            value = str(meta.get(key) or "").strip()
            if value:
                urls.add(value.rstrip(".,);:!?\"'"))
        for match in _URL_RE.finditer(chunk.content or ""):
            urls.add(match.group(0).rstrip(".,);:!?\"'"))
    return urls


def _check_schema(answer: str) -> list[str]:
    if not answer or not answer.strip():
        return ["empty_answer"]
    return []


def _check_secrets(answer: str) -> list[str]:
    offences: list[str] = []
    for pat in _SECRET_PATTERNS:
        if pat.search(answer):
            offences.append(f"secret_leak:{pat.pattern[:32]}")
    return offences


def _is_acceptable_product_link(url: str) -> bool:
    """A product link is either a fully-qualified http(s) URL (manual link
    provided by the company) or an internal path under /c/<company>/product/<slug>
    which the frontend's router resolves to ProductDetailPage."""
    if not url:
        return False
    if _URL_RE.match(url):
        return True
    if url.startswith("/c/") and "/product/" in url:
        return True
    return False


def _check_links(product_links: list[ProductLink], grounded_ids: set[str]) -> list[str]:
    offences: list[str] = []
    for link in product_links:
        if link.product_id and link.product_id not in grounded_ids:
            offences.append(f"link_to_unretrieved_product:{link.product_id}")
        if not _is_acceptable_product_link(link.url):
            offences.append(f"link_malformed:{link.url[:32]}")
    return offences


def _check_answer_urls(answer: str, chunks: list[ContextChunk]) -> list[str]:
    allowed_urls = _grounded_urls(chunks)
    offences: list[str] = []
    for match in _URL_RE.finditer(answer or ""):
        url = match.group(0).rstrip(".,);:!?\"'")
        if allowed_urls and url not in allowed_urls:
            offences.append(f"ungrounded_url:{url[:48]}")
        if not allowed_urls:
            offences.append(f"ungrounded_url:{url[:48]}")
    return offences


def _check_context_leak(answer: str) -> list[str]:
    offences: list[str] = []
    if _CONTEXT_TAG_RE.search(answer or ""):
        offences.append("context_tag_leak")
    if _SOURCE_LABEL_RE.search(answer or ""):
        offences.append("source_label_leak")
    return offences


def _requires_purchase_link(user_message: str) -> bool:
    normalized = re.sub(r"[^a-z0-9\s]", " ", str(user_message or "").lower())
    tokens = set(normalized.split())
    if not tokens & _PURCHASE_TERMS:
        return False
    return any(phrase in normalized for phrase in ("buy", "order", "purchase", "checkout", "place order", "add to cart"))


def _check_required_purchase_link(
    *,
    user_message: str,
    product_links: list[ProductLink],
    chunks: list[ContextChunk],
) -> list[str]:
    if not _requires_purchase_link(user_message):
        return []
    if not any(chunk.source_type == "product" for chunk in chunks):
        return []
    return [] if product_links else ["missing_purchase_link"]


def _check_duplicate_response(answer: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", answer or "") if part.strip()]
    if len(paragraphs) >= 2 and paragraphs[-1].lower() in {part.lower() for part in paragraphs[:-1]}:
        return ["duplicate_response"]
    compact = re.sub(r"\s+", " ", answer or "").strip()
    if len(compact) >= 80 and len(compact) % 2 == 0:
        midpoint = len(compact) // 2
        if compact[:midpoint].strip().lower() == compact[midpoint:].strip().lower():
            return ["duplicate_response"]
    return []


_GROUNDING_STOPWORDS = {
    # Articles, pronouns, common words
    "the", "a", "an", "i", "we", "our", "you", "your", "this", "that",
    "these", "those", "it", "they", "them", "their", "his", "her", "hers",
    "sure", "yes", "no", "okay", "ok", "hi", "hello", "hey", "thanks",
    "thank", "indeed", "absolutely", "certainly", "right", "well", "actually",
    "perhaps", "maybe", "also", "still", "now", "then", "however", "btw",
    "first", "second", "third", "next", "finally", "lastly", "additionally",
    "however", "moreover", "therefore", "anyway", "alright", "great",
    "perfect", "excellent",
    # Context section labels (old markdown headers and new XML tags) — the LLM
    # occasionally echoes these and they must not be treated as product names.
    "company data", "product database", "response style", "faqs",
    "knowledge base", "company information", "retrieved context",
    "sources consulted", "company info", "product catalog",
    # Common business and technology terms that are NOT product names
    "saas", "services", "products", "catalog", "offerings", "items",
    "team", "support", "business", "company", "store", "shop", "platform",
    "solutions", "software", "hardware", "digital", "online", "website",
    "collection", "range", "lineup", "inventory",
    # Common 2-word phrases that are NOT product names
    "please note", "great choice", "good news", "free shipping", "quick question",
    "happy help", "feel free", "let know", "best regards", "thank you",
    "right away", "sure thing", "absolutely right", "totally understand",
    "delivery time", "shipping time", "business days", "working days",
    "order now", "buy now", "add cart", "view product", "learn more",
    "our team", "our store", "our shop", "our products", "our catalog",
    "your order", "your account", "your cart", "your question",
    "high quality", "best quality", "great quality", "top quality",
    "new arrival", "best seller", "top rated", "hot deal", "special offer",
    "limited stock", "in stock", "out stock",
}


def _looks_like_product_name(candidate: str) -> bool:
    """Conservative heuristic: a candidate is product-name-shaped if it's
    multi-word (2+ words), contains a digit, or has internal uppercase (CamelCase).

    Two-word phrases are now included since product names like "Blue Jacket" or
    "Gold Watch" are commonly 2 words. The stopword list and grounded-name
    partial-match logic prevent common phrases from false-positiving.
    """
    if " " in candidate:
        # 2+ words — include to catch short product names like "Gold Watch"
        return True
    if any(ch.isdigit() for ch in candidate):
        return True
    return False


def _check_grounding(answer: str, chunks: list[ContextChunk]) -> list[str]:
    offences: list[str] = []
    grounded_names = _grounded_product_names(chunks)
    grounded_prices = _grounded_prices(chunks)
    # Candidate names: capitalised noun phrases in the answer. Capture the
    # original case so we can detect CamelCase product names like "iPhone".
    raw_candidates = [match.group(1) for match in _CAP_PHRASE_RE.finditer(answer)]
    # Multi-word stopword phrases for substring matching (e.g. "our company data"
    # contains "company data" which is a context label, not a product name).
    _multi_stopwords = {sw for sw in _GROUNDING_STOPWORDS if " " in sw}
    if grounded_names:
        for raw in raw_candidates:
            candidate = raw.lower()
            if candidate in _GROUNDING_STOPWORDS:
                continue
            # Substring check: skip if the candidate *contains* a known
            # multi-word stopword (handles "Our Company Data" → "company data").
            if any(sw in candidate for sw in _multi_stopwords):
                continue
            # Skip phrases whose first word is all-uppercase (company acronyms
            # like "ABC Tech" or initialisms like "USA Today") — these are not
            # CamelCase product names even though later chars are uppercase.
            _first_word = raw.split()[0]
            if len(_first_word) > 1 and _first_word.isalpha() and _first_word == _first_word.upper():
                continue
            if not _looks_like_product_name(candidate) and not any(ch.isupper() for ch in raw[1:]):
                # Single-word, no digit, no internal caps — almost certainly a
                # sentence starter rather than a product name.
                continue
            if candidate in grounded_names:
                continue
            if any(candidate in name or name in candidate for name in grounded_names):
                continue
            if len(candidate) < 4:
                continue
            offences.append(f"ungrounded_product_name:{candidate[:40]}")
    for match in _PRICE_RE.finditer(answer):
        token = _normalise_price(match.group(0))
        if not token or not any(ch.isdigit() for ch in token):
            continue
        if (grounded_prices and token not in grounded_prices) or (not grounded_prices and any(c.source_type == "product" for c in chunks)):
            offences.append(f"ungrounded_price:{token[:24]}")
    return offences


def _check_tone(answer: str, style_prompt: str) -> list[str]:
    offences: list[str] = []
    lowered = answer.lower()
    for word in _PROFANITY:
        if word in lowered:
            offences.append(f"profanity:{word}")
    if _SHOUTING_RE.search(answer):
        offences.append("shouting")
    return offences


def validate(
    *,
    answer: str,
    product_links: list[ProductLink],
    chunks: list[ContextChunk],
    style_prompt: str = "",
    user_message: str = "",
) -> ValidationReport:
    offences: list[str] = []
    offences.extend(_check_schema(answer))
    if not offences:
        offences.extend(_check_secrets(answer))
        offences.extend(_check_context_leak(answer))
        offences.extend(_check_links(product_links, _grounded_product_ids(chunks)))
        offences.extend(_check_answer_urls(answer, chunks))
        offences.extend(_check_grounding(answer, chunks))
        offences.extend(_check_required_purchase_link(user_message=user_message, product_links=product_links, chunks=chunks))
        offences.extend(_check_duplicate_response(answer))
        offences.extend(_check_tone(answer, style_prompt))
    return ValidationReport(ok=not offences, offences=offences)


def _strip_offending_prices(answer: str, chunks: list[ContextChunk], stripped: list[str]) -> str:
    grounded_prices = _grounded_prices(chunks)
    has_product_context = any(chunk.source_type == "product" for chunk in chunks)
    if not has_product_context:
        return answer

    def repl(match: re.Match) -> str:
        token = _normalise_price(match.group(0))
        if token and ((grounded_prices and token not in grounded_prices) or not grounded_prices):
            stripped.append(f"price:{match.group(0).strip()[:40]}")
            return ""
        return match.group(0)

    return _PRICE_RE.sub(repl, answer)


def _strip_offending_urls(answer: str, chunks: list[ContextChunk], stripped: list[str]) -> str:
    allowed_urls = _grounded_urls(chunks)

    def repl(match: re.Match) -> str:
        url = match.group(0).rstrip(".,);:!?\"'")
        if (allowed_urls and url not in allowed_urls) or not allowed_urls:
            stripped.append(f"url:{url[:80]}")
            return ""
        return match.group(0)

    return _URL_RE.sub(repl, answer)


def _strip_context_leaks(answer: str, stripped: list[str]) -> str:
    cleaned = _CONTEXT_TAG_RE.sub(lambda match: stripped.append(f"context_tag:{match.group(0)[:40]}") or "", answer)
    cleaned = _SOURCE_LABEL_RE.sub(lambda match: stripped.append(f"source_label:{match.group(0)[:40]}") or "", cleaned)
    return cleaned


def _strip_offending_product_names(answer: str, offences: list[str], stripped: list[str]) -> str:
    cleaned = answer
    for offence in offences:
        if not offence.startswith("ungrounded_product_name:"):
            continue
        name = offence.split(":", 1)[1].strip()
        if not name:
            continue
        pattern = re.compile(re.escape(name), re.IGNORECASE)
        if pattern.search(cleaned):
            stripped.append(f"product_name:{name[:80]}")
            cleaned = pattern.sub("the available product", cleaned)
    return cleaned


def _dedupe_answer(answer: str, stripped: list[str]) -> str:
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", answer or "") if part.strip()]
    if len(paragraphs) >= 2:
        kept: list[str] = []
        seen: set[str] = set()
        for paragraph in paragraphs:
            key = paragraph.lower()
            if key in seen:
                stripped.append("duplicate_paragraph")
                continue
            seen.add(key)
            kept.append(paragraph)
        return "\n\n".join(kept)
    return answer


def _answer_contains_grounded_price(answer: str, chunks: list[ContextChunk]) -> bool:
    grounded_prices = {price for price in _grounded_prices(chunks) if price}
    if not grounded_prices:
        return False
    answer_prices = {_normalise_price(match.group(0)) for match in _PRICE_RE.finditer(answer or "")}
    return bool(grounded_prices & answer_prices)


def _answer_contains_grounded_product_name(answer: str, chunks: list[ContextChunk]) -> bool:
    grounded_names = _grounded_product_names(chunks)
    lowered = (answer or "").lower()
    return any(name and name in lowered for name in grounded_names)


def safe_validate_response(
    *,
    answer: str,
    product_links: list[ProductLink],
    chunks: list[ContextChunk],
    style_prompt: str = "",
    user_message: str = "",
) -> SafeValidationResult:
    stripped: list[str] = []
    grounded_ids = _grounded_product_ids(chunks)
    filtered_links = [
        link
        for link in product_links
        if (not link.product_id or link.product_id in grounded_ids) and _is_acceptable_product_link(link.url)
    ]
    if len(filtered_links) != len(product_links):
        stripped.append("invalid_product_link")

    cleaned = _strip_context_leaks(answer or "", stripped)
    cleaned = _strip_offending_urls(cleaned, chunks, stripped)
    cleaned = _strip_offending_prices(cleaned, chunks, stripped)
    initial_report = validate(
        answer=cleaned,
        product_links=filtered_links,
        chunks=chunks,
        style_prompt=style_prompt,
        user_message=user_message,
    )
    cleaned = _strip_offending_product_names(cleaned, initial_report.offences, stripped)
    cleaned = _dedupe_answer(cleaned, stripped)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    final_report = validate(
        answer=cleaned,
        product_links=filtered_links,
        chunks=chunks,
        style_prompt=style_prompt,
        user_message=user_message,
    )
    if any(item.startswith("price:") for item in stripped) and not _answer_contains_grounded_price(cleaned, chunks):
        final_report.ok = False
        final_report.offences.append("stripped_required_price")
    if any(item.startswith("product_name:") for item in stripped) and not _answer_contains_grounded_product_name(cleaned, chunks):
        final_report.ok = False
        final_report.offences.append("stripped_required_product_name")
    return SafeValidationResult(
        answer=cleaned,
        product_links=filtered_links,
        report=final_report,
        stripped_content=stripped,
    )


def compute_confidence(chunks: list[ContextChunk]) -> float:
    if not chunks:
        return 0.0
    top = sorted([c.relevance_score for c in chunks], reverse=True)[:3]
    weights = [0.6, 0.3, 0.1][: len(top)]
    weighted = sum(s * w for s, w in zip(top, weights)) / sum(weights)
    sources = {c.source_type for c in chunks}
    coverage_bonus = 0.1 if len(sources) >= 2 else 0.0
    return min(1.0, weighted + coverage_bonus)


def confidence_bucket(score: float) -> str:
    if score >= 0.7:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"
