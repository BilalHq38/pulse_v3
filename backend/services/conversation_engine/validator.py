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
_CAP_PHRASE_RE = re.compile(r"\b([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+){0,3})\b")
_SHOUTING_RE = re.compile(r"(?:\b[A-Z]{4,}\b\s+){6,}")


@dataclass
class GroundingContext:
    chunks: list[ContextChunk]


def _grounded_product_names(chunks: Iterable[ContextChunk]) -> set[str]:
    out: set[str] = set()
    for chunk in chunks:
        if chunk.source_type != "product":
            continue
        name = (chunk.metadata or {}).get("name") or chunk.title
        if name:
            out.add(str(name).lower())
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
}


def _looks_like_product_name(candidate: str) -> bool:
    """Conservative heuristic: a candidate is product-name-shaped if it's
    multi-word with 3+ words, contains a digit, or has internal uppercase
    (CamelCase). Short two-word phrases are often descriptive terms or context
    labels rather than product names, so we require 3+ words for multi-word."""
    if " " in candidate:
        # Require at least 3 words to reduce false positives on phrases like
        # "Company Data" or "Product Database" (context section labels).
        return candidate.count(" ") >= 2
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
        if grounded_prices and token not in grounded_prices:
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
) -> ValidationReport:
    offences: list[str] = []
    offences.extend(_check_schema(answer))
    if not offences:
        offences.extend(_check_secrets(answer))
        offences.extend(_check_links(product_links, _grounded_product_ids(chunks)))
        offences.extend(_check_grounding(answer, chunks))
        offences.extend(_check_tone(answer, style_prompt))
    return ValidationReport(ok=not offences, offences=offences)


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
