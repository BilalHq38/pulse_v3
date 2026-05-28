from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections import defaultdict

from core.utils import is_valid_image_url, normalize_product_images
from shared.cache import get_cache_client
from shared.metrics import increment_counter, observe_histogram, timed_metric
from services.ai_service.embedding_service import search_similar_embeddings, store_embedding
from services.ai_service.llm_tracking import has_embedding_budget_remaining
from services.ai_service.routing_guards import is_low_value_message

_CATALOG_CACHE_TTL_SECONDS = max(
    30,
    int(os.environ.get("RAG_CATALOG_CACHE_TTL_SECONDS", "300") or 300),
)

logger = logging.getLogger(__name__)

_CATALOG_CACHE = get_cache_client(namespace="ai-rag-catalog")
_RANKING_CACHE = get_cache_client(namespace="ai-rag-ranking")
_COMPANY_PRODUCTS_COLUMNS: set[str] | None = None

GENERAL_PRODUCT_PATTERNS = (
    "what do you sell",
    "what do you have",
    "what do you offer",
    "what are you offering",
    "what you are offering",
    "what products do you provide",
    "show me products",
    "show products",
    "tell me about products",
    "show your catalog",
    "catalog",
    "collection",
    "inventory",
    "available products",
    "available options",
    "options dikhao",
    "products kya hain",
    "kya available hai",
    "what services do you offer",
    "show services",
)

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "can",
    "for",
    "which",
    "have",
    "i",
    "please",
    "need",
    "want",
    "in",
    "is",
    "me",
    "of",
    "show",
    "sell",
    "the",
    "to",
    "what",
    "you",
    "your",
    # Added: generic intent and query words that are not product names
    "know",
    "about",
    "tell",
    "us",
    "do",
    "does",
    "offering",
    "offer",
    "buy",
    "purchase",
    "order",
    "get",
    "any",
    "some",
    "all",
    "available",
    "more",
    "options",
    "option",
    "how",
    "with",
    "list",
    "catalog",
    "our",
    "this",
    "that",
    "just",
    "also",
    "item",
    "items",
    "see",
    "would",
    "like",
    "from",
    "by",
    "on",
    "at",
    "been",
    "we",
    "provide",
    "providing",
    "product",
    "products",
    "catalog",
    "catalogue",
    "service",
    "services",
    "option",
    "options",
    "available",
}

_RAG_SKIP_PHRASES = {
    "hi",
    "hello",
    "hey",
    "thanks",
    "thank you",
    "ok",
    "okay",
    "yes",
    "no",
}


def _normalize_term(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9\s-]", " ", str(text or "").lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _to_singular(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("ses") and len(token) > 4:
        return token[:-2]
    if token.endswith("s") and len(token) > 3 and not token.endswith("ss"):
        return token[:-1]
    return token


def _build_dynamic_category_aliases(categories: list[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for raw_category in categories:
        canonical = _normalize_term(raw_category)
        if not canonical:
            continue
        parts = [part for part in canonical.split(" ") if part]
        candidates = {canonical, canonical.replace("-", " ")}
        candidates.update(parts)
        candidates.update(_to_singular(part) for part in parts)
        for alias in candidates:
            alias = _normalize_term(alias)
            if alias and alias not in aliases:
                aliases[alias] = canonical
    return aliases


def _tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", (text or "").lower()) if token]


def _should_skip_rag_query(query: str, *, intent_name: str = "", has_history: bool = False) -> tuple[bool, str]:
    normalized = _normalize_term(query)
    if not normalized:
        return True, "empty_query"
    if is_low_value_message(normalized):
        return True, "low_value_message"
    if intent_name == "follow_up_continue" or has_history:
        return False, ""
    if normalized in _RAG_SKIP_PHRASES:
        return True, "greeting_or_gratitude"
    tokens = _tokenize(normalized)
    if len(tokens) <= 2 and not any(
        term in normalized
        for term in (
            "product",
            "catalog",
            "price",
            "buy",
            "order",
            "shipping",
            "refund",
            "support",
            "feature",
            "service",
        )
    ):
        return True, "short_low_value_query"
    query_info = understand_product_query(normalized)
    needs_knowledge = any(
        term in normalized
        for term in (
            "product",
            "catalog",
            "price",
            "buy",
            "order",
            "shipping",
            "refund",
            "policy",
            "support",
            "feature",
            "service",
            "plan",
        )
    )
    if not (query_info.get("general") or query_info.get("specific") or needs_knowledge):
        return True, "no_retrieval_intent"
    return False, ""


def understand_product_query(
    query: str,
    catalog_categories: list[str] | None = None,
) -> dict:
    lowered = _normalize_term(query)
    query_tokens = [token for token in _tokenize(lowered) if token not in STOPWORDS]
    category_aliases = _build_dynamic_category_aliases(catalog_categories or [])

    matched_categories: list[str] = []
    if lowered and category_aliases:
        padded = f" {lowered} "
        for alias, canonical in category_aliases.items():
            alias_token = _normalize_term(alias)
            if alias_token and f" {alias_token} " in padded:
                matched_categories.append(canonical)
    matched_categories = list(dict.fromkeys(matched_categories))

    matched_category_tokens = {
        _to_singular(part) for category in matched_categories for part in _normalize_term(category).split(" ") if part
    }
    keyword_tokens = [token for token in query_tokens if _to_singular(token) not in matched_category_tokens]

    mentions_catalog = any(
        token in lowered
        for token in (
            "product",
            "products",
            "catalog",
            "collection",
            "inventory",
            "item",
            "items",
            "service",
            "services",
            "solution",
            "solutions",
            "offer",
            "offering",
            "option",
            "options",
            "available",
        )
    )
    is_general_phrase = any(pattern in lowered for pattern in GENERAL_PRODUCT_PATTERNS)
    is_general = (is_general_phrase or mentions_catalog) and not matched_categories and not keyword_tokens

    return {
        "query": lowered,
        "categories": matched_categories,
        "keywords": keyword_tokens,
        "general": is_general,
        "specific": bool(matched_categories or keyword_tokens),
    }


def _catalog_kind_for_query(query: str) -> str:
    normalized = _normalize_term(query)
    service_signal = any(term in normalized for term in ("service", "services", "solution", "solutions"))
    product_signal = any(term in normalized for term in ("product", "products", "catalog", "item", "items", "collection", "inventory"))
    if service_signal and not product_signal:
        return "service"
    if product_signal and not service_signal:
        return "product"
    return ""


async def _load_product_catalog(
    db,
    company_id: str,
    limit: int = 120,
    *,
    bypass_cache: bool = False,
    catalog_kind: str = "",
) -> list[dict]:
    if not db or not company_id:
        return []

    catalog_kind = str(catalog_kind or "").strip().lower()
    cache_key = f"catalog:{company_id}:{max(1, int(limit))}:{catalog_kind}"
    if not bypass_cache:
        cached = await _CATALOG_CACHE.get_json(cache_key)
        if isinstance(cached, list):
            increment_counter("ai.cache.catalog.hit")
            return [dict(item) for item in cached if isinstance(item, dict)]

    increment_counter("ai.cache.catalog.miss")
    started = time.perf_counter()
    product_columns = await _company_products_columns(db)
    tags_select = "tags" if "tags" in product_columns else "NULL::jsonb AS tags"
    sku_select = "sku" if "sku" in product_columns else "''::text AS sku"
    stock_select = "stock_quantity" if "stock_quantity" in product_columns else "0::integer AS stock_quantity"
    rows = await db.fetch(
        "SELECT id, company_id, name, product_title, description, category, product_type, "
        f"       price, price_currency, status, {tags_select}, {sku_select}, {stock_select}, created_at, updated_at "
        "FROM company_products "
        "WHERE company_id=$1 AND (status='active' OR status IS NULL OR status='') "
        "  AND ($3='' OR ($3='service' AND LOWER(COALESCE(product_type,'')) IN ('service','services')) "
        "       OR ($3='product' AND LOWER(COALESCE(product_type,'')) NOT IN ('service','services'))) "
        "ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST, name ASC LIMIT $2",
        company_id,
        limit,
        catalog_kind,
    )
    products = [dict(row) for row in rows]
    if not products:
        observe_histogram("ai.catalog.load_ms", (time.perf_counter() - started) * 1000.0)
        return []

    product_ids = [product["id"] for product in products if product.get("id")]
    if not product_ids:
        observe_histogram("ai.catalog.load_ms", (time.perf_counter() - started) * 1000.0)
        return []

    feature_rows = await db.fetch(
        "SELECT product_id,feature FROM product_features WHERE product_id = ANY($1::text[]) ORDER BY sort_order",
        product_ids,
    )
    image_rows = await db.fetch(
        "SELECT product_id,image_url FROM product_images WHERE product_id = ANY($1::text[]) ORDER BY sort_order",
        product_ids,
    )
    features_by_product: dict[str, list[str]] = defaultdict(list)
    images_by_product: dict[str, list[str]] = defaultdict(list)
    for row in feature_rows:
        features_by_product[str(row["product_id"])].append(str(row["feature"]))
    for row in image_rows:
        images_by_product[str(row["product_id"])].append(str(row["image_url"]))

    for product in products:
        product_id = str(product.get("id") or "")
        product["features"] = features_by_product.get(product_id, [])
        product["images"] = normalize_product_images(images_by_product.get(product_id, []), limit=3)
        raw_tags: list[str] = []
        if isinstance(product.get("tags"), list):
            raw_tags.extend(str(item) for item in product.get("tags") or [])
        elif isinstance(product.get("tags"), str):
            raw_tags.extend(part.strip() for part in str(product.get("tags") or "").split(","))
        raw_tags.extend(product.get("features") or [])
        raw_tags.extend(
            [
                str(product.get("category") or ""),
                str(product.get("product_type") or ""),
            ]
        )
        product["normalized_name"] = _normalize_term(str(product.get("name") or product.get("product_title") or ""))
        product["normalized_category"] = _normalize_term(str(product.get("category") or ""))
        product["normalized_tags"] = sorted({_normalize_term(item) for item in raw_tags if _normalize_term(item)})

    observe_histogram("ai.catalog.load_ms", (time.perf_counter() - started) * 1000.0)
    if not bypass_cache:
        await _CATALOG_CACHE.set_json(cache_key, products, ttl_seconds=_CATALOG_CACHE_TTL_SECONDS)
    return products


async def _company_products_columns(db) -> set[str]:
    global _COMPANY_PRODUCTS_COLUMNS
    if _COMPANY_PRODUCTS_COLUMNS is not None:
        return _COMPANY_PRODUCTS_COLUMNS
    try:
        rows = await db.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ANY(current_schemas(FALSE)) "
            "  AND table_name='company_products'"
        )
        _COMPANY_PRODUCTS_COLUMNS = {str(row["column_name"]) for row in rows if row["column_name"]}
    except Exception as exc:
        logger.warning("company_products column validation failed: %s", exc)
        _COMPANY_PRODUCTS_COLUMNS = set()
    return _COMPANY_PRODUCTS_COLUMNS


def _product_search_text(product: dict) -> str:
    return " ".join(
        filter(
            None,
            [
                str(product.get("name") or ""),
                str(product.get("product_title") or ""),
                str(product.get("description") or ""),
                str(product.get("category") or ""),
                str(product.get("product_type") or ""),
                " ".join(str(item) for item in product.get("features") or []),
                " ".join(str(item) for item in product.get("normalized_tags") or []),
            ],
        )
    ).lower()


def _token_set(text: str) -> set[str]:
    return set(_tokenize(text or ""))


def _is_acceptable_product_image_url(image_url: str) -> bool:
    image_url = str(image_url or "").strip()
    if image_url.startswith("/api/") or image_url.startswith("/media/"):
        return True
    return is_valid_image_url(image_url)


def _pick_product_image_url(product: dict) -> str:
    for image_url in normalize_product_images(product.get("images") or [], limit=3):
        if _is_acceptable_product_image_url(image_url):
            return image_url
    return ""


def _build_product_attachment(product: dict, image_url: str, image_index: int) -> dict | None:
    if not image_url or not _is_acceptable_product_image_url(image_url):
        return None
    name = str(product.get("name") or "").strip()
    title = str(product.get("product_title") or "").strip()
    category = str(product.get("category") or "").strip()
    label = f"{name} ({title})" if name and title and title.lower() != name.lower() else (name or title or "Product")
    caption_lines = [f"Product: {label}"]
    if category:
        caption_lines.append(f"Category: {category}")
    caption = "\n".join(caption_lines)
    return {
        "type": "image",
        "url": image_url,
        "name": name or title,
        "size": 0,
        "product_id": str(product.get("id") or "").strip(),
        "product_name": name,
        "product_title": title,
        "product_category": category,
        "image_index": image_index,
        "caption": caption,
        "raw_metadata": {
            "product_id": str(product.get("id") or "").strip(),
            "product_name": name,
            "product_title": title,
            "product_category": category,
            "image_index": image_index,
            "caption": caption,
        },
    }


async def _ensure_product_embeddings(db, company_id: str, products: list[dict], *, limit: int = 20) -> None:
    if not db or not company_id or not products:
        return
    product_ids = [product["id"] for product in products[:limit] if product.get("id")]
    if not product_ids:
        return
    existing_rows = await db.fetch(
        "SELECT DISTINCT source_id FROM embeddings "
        "WHERE company_id=$1 AND source_type='company_product' AND source_id = ANY($2::text[])",
        company_id,
        product_ids,
    )
    existing_ids = {str(row["source_id"]) for row in existing_rows if str(row.get("source_id") or "").strip()}
    for product in products[:limit]:
        if product["id"] in existing_ids:
            continue
        try:
            await store_embedding(
                db,
                company_id,
                "company_product",
                product["id"],
                _product_search_text(product),
                metadata=product.get("category", ""),
            )
        except Exception as exc:
            logger.debug("Product embedding seed skipped for %s: %s", product["id"], exc)


def _score_product(product: dict, query_info: dict, vector_score: float = 0.0) -> float:
    haystack = _product_search_text(product)
    normalized_name = str(product.get("normalized_name") or "")
    normalized_category = str(product.get("normalized_category") or "")
    normalized_tags = set(product.get("normalized_tags") or [])
    score = 0.0
    if query_info["general"]:
        score += 0.2
        if product.get("images"):
            score += 0.15
        if product.get("features"):
            score += 0.1
    for category in query_info["categories"]:
        normalized_category_query = _normalize_term(category)
        if not normalized_category_query:
            continue
        if normalized_category_query == normalized_category:
            score += 1.2
        if normalized_category_query in haystack:
            score += 0.8
    for keyword in query_info["keywords"]:
        normalized_keyword = _normalize_term(keyword)
        if not normalized_keyword:
            continue
        if normalized_keyword == normalized_category:
            score += 0.8
        if normalized_keyword in normalized_name:
            score += 1.0
        elif normalized_keyword in _normalize_term(str(product.get("product_title") or "")):
            score += 0.8
        elif normalized_keyword in normalized_tags:
            score += 0.6
        elif normalized_keyword in haystack:
            score += 0.45
    if vector_score > 0:
        score += vector_score * 1.7
    return score


async def rank_products_for_query(
    db,
    company_id: str,
    query: str,
    *,
    limit: int = 6,
    exclude_ids: list[str] | None = None,
    history_text: str = "",
    history_product_ids: list[str] | None = None,
    customer_id: str = "",
    conversation_id: str = "",
    bypass_product_cache: bool = False,
) -> list[dict]:
    excluded = {str(item).strip() for item in [*(exclude_ids or []), *(history_product_ids or [])] if str(item).strip()}
    history_affects_ranking = bool(str(history_text or "").strip() or excluded)
    cache_material = json.dumps(
        {
            "company_id": company_id,
            "conversation_id": str(conversation_id or "").strip() if history_affects_ranking else "",
            "customer_id": str(customer_id or "").strip() if history_affects_ranking else "",
            "query": str(query or "").strip().lower(),
            "exclude": sorted(excluded),
            "history": str(history_text or "").strip().lower()[:600],
            "limit": max(1, int(limit)),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    ranking_key = f"rank:{hashlib.sha256(cache_material.encode('utf-8')).hexdigest()}"

    if not bypass_product_cache:
        cached_ranked = await _RANKING_CACHE.get_json(ranking_key)
        if isinstance(cached_ranked, list):
            increment_counter("ai.cache.ranking.hit")
            return [dict(item) for item in cached_ranked if isinstance(item, dict)][: max(1, int(limit))]

    increment_counter("ai.cache.ranking.miss")
    catalog_kind = _catalog_kind_for_query(query)
    products = await _load_product_catalog(
        db,
        company_id,
        limit=max(3, min(max(1, int(limit or 3)) * 4, 50)),
        bypass_cache=bypass_product_cache,
        catalog_kind=catalog_kind,
    )
    if not products:
        return []
    query_info = understand_product_query(
        query,
        catalog_categories=[str(product.get("category") or "") for product in products],
    )
    if query_info.get("general") and not query_info.get("specific"):
        selected = [
            product
            for product in products
            if str(product.get("id") or "").strip() not in excluded
        ][: max(1, int(limit or 3))]
        logger.info(
            "top_products_selected company_id=%s conversation_id=%s batch_limit=%s selected_product_ids=%s recently_shown_product_ids=%s reason=generic_product_query",
            company_id,
            conversation_id or "",
            max(1, int(limit or 3)),
            ",".join(str(product.get("id") or "") for product in selected),
            ",".join(sorted(excluded)),
        )
        return selected

    with timed_metric("ai.ranking.total_ms"):
        history_terms = _token_set(history_text)
        if has_embedding_budget_remaining():
            vector_rows = await search_similar_embeddings(
                db,
                company_id,
                query,
                source_type="company_product",
                top_k=max(limit * 4, 12),
            )
        else:
            logger.info("rag_skipped_embedding company_id=%s reason=embedding_budget_exhausted", company_id)
            vector_rows = []
        if not vector_rows and query_info["specific"]:
            increment_counter("ai.ranking.embedding_seeded")
            if has_embedding_budget_remaining():
                await _ensure_product_embeddings(db, company_id, products)
                vector_rows = await search_similar_embeddings(
                    db,
                    company_id,
                    query,
                    source_type="company_product",
                    top_k=max(limit * 4, 12),
                )
        if not vector_rows:
            logger.info(
                "embedding_fallback_catalog_keyword_match_used company_id=%s conversation_id=%s query_specific=%s query_general=%s product_count=%s",
                company_id,
                conversation_id or "",
                bool(query_info.get("specific")),
                bool(query_info.get("general")),
                len(products),
            )
        vector_scores = {
            str(row.get("source_id") or ""): float(row.get("similarity") or 0)
            for row in vector_rows
            if str(row.get("source_id") or "").strip()
        }

        ranked: list[tuple[float, dict]] = []
        for product in products:
            score = _score_product(product, query_info, vector_scores.get(product["id"], 0.0))
            if history_terms:
                product_terms = _token_set(_product_search_text(product))
                overlap = len(product_terms & history_terms)
                if overlap:
                    score += min(0.55, overlap * 0.09)
                category = str(product.get("category") or "").lower()
                if category and category in history_terms:
                    score += 0.2
            ranked.append((score, product))
        ranked.sort(
            key=lambda item: (
                item[0],
                len(item[1].get("features") or []),
                len(item[1].get("images") or []),
                str(item[1].get("updated_at") or item[1].get("created_at") or ""),
            ),
            reverse=True,
        )

        selected: list[dict] = []
        seen_ids: set[str] = set()
        min_specific_score = 0.45 if query_info.get("specific") and not query_info.get("general") else 0.0
        for score, product in ranked:
            if min_specific_score and score < min_specific_score:
                continue
            product_id = str(product.get("id") or "").strip()
            if not product_id or product_id in excluded or product_id in seen_ids:
                continue
            selected.append(product)
            seen_ids.add(product_id)
            if len(selected) >= limit:
                break

    if not bypass_product_cache:
        await _RANKING_CACHE.set_json(ranking_key, selected, ttl_seconds=30)
    return selected


def _format_product_context(product: dict) -> str:
    description = " ".join(str(product.get("description") or "").split()) or "No description provided."
    features = ", ".join(product.get("features") or []) or "No key features listed."
    price = str(product.get("price") or "").strip()
    currency = str(product.get("price_currency") or "").strip() or "USD"
    price_display = f"{price} {currency}".strip() if price else "Not listed"
    return (
        f"Product: {product.get('name') or 'Unnamed product'}"
        f" | Title: {str(product.get('product_title') or '').strip() or 'N/A'}"
        f" | Category: {str(product.get('category') or 'general').strip() or 'general'}"
        f" | Type: {str(product.get('product_type') or 'standard').strip() or 'standard'}"
        f" | Price: {price_display}"
        f" | Description: {description}"
        f" | Features: {features}"
    )


async def build_ai_context(
    db,
    company_id: str | None = None,
    current_query: str = "",
    *,
    exclude_product_ids: list[str] | None = None,
    max_products: int = 6,
    history_text: str = "",
    history_product_ids: list[str] | None = None,
    customer_id: str = "",
    conversation_id: str = "",
    intent_name: str = "",
    has_history: bool = False,
    include_products: bool = True,
    bypass_product_cache: bool = False,
) -> dict:
    if not db:
        return {
            "knowledge_text": "",
            "product_ids": [],
            "product_attachments": [],
            "products": [],
            "public_company": {},
        }
    skip_rag, skip_reason = _should_skip_rag_query(
        current_query,
        intent_name=intent_name,
        has_history=has_history or bool(history_product_ids),
    )
    if skip_rag:
        logger.info(
            "rag_skipped company_id=%s reason=%s query_len=%s",
            company_id or "",
            skip_reason,
            len(current_query or ""),
        )
        return {
            "knowledge_text": "",
            "product_ids": [],
            "product_attachments": [],
            "products": [],
            "rag_called": False,
            "public_company": {},
        }

    chunks: list[str] = []
    product_attachments: list[dict] = []
    selected_products: list[dict] = []
    public_company: dict = {}
    excluded_ids = {
        str(item).strip() for item in [*(exclude_product_ids or []), *(history_product_ids or [])] if str(item).strip()
    }

    try:
        company_profile = (
            await db.fetchrow(
                "SELECT c.name AS company_name, cs.industry, cs.tagline, cs.description, cs.website_address "
                "FROM companies c LEFT JOIN company_settings cs ON cs.company_id = c.id "
                "WHERE c.id=$1 LIMIT 1",
                company_id,
            )
            if company_id
            else None
        )
        if company_profile:
            profile = dict(company_profile)
            public_company = {
                "company_name": str(profile.get("company_name") or "").strip(),
                "industry": str(profile.get("industry") or "").strip(),
                "tagline": str(profile.get("tagline") or "").strip(),
                "description": str(profile.get("description") or "").strip(),
                "website_address": str(profile.get("website_address") or "").strip(),
            }
            intro = []
            if profile.get("company_name"):
                intro.append(f"Company: {profile['company_name']}")
            if profile.get("industry"):
                intro.append(f"Industry: {profile['industry']}")
            if profile.get("tagline"):
                intro.append(f"Tagline: {profile['tagline']}")
            if profile.get("description"):
                intro.append(f"Brand description: {profile['description']}")
            if profile.get("website_address"):
                intro.append(f"Website: {profile['website_address']}")
            if intro:
                chunks.append(" | ".join(intro))

        if include_products and company_id and current_query:
            selected_products = await rank_products_for_query(
                db,
                company_id,
                current_query,
                limit=max_products,
                exclude_ids=exclude_product_ids,
                history_text=history_text,
                history_product_ids=history_product_ids,
                customer_id=customer_id,
                conversation_id=conversation_id,
                bypass_product_cache=bypass_product_cache,
            )
        elif include_products and company_id:
            catalog = await _load_product_catalog(
                db,
                company_id,
                limit=max(max_products * 4, 12),
                bypass_cache=bypass_product_cache,
                catalog_kind=_catalog_kind_for_query(current_query),
            )
            selected_products = [
                product for product in catalog if str(product.get("id") or "").strip() not in excluded_ids
            ][:max_products]

        for product in selected_products[:max_products]:
            chunks.append(_format_product_context(product))
            attachment = _build_product_attachment(product, _pick_product_image_url(product), 0)
            if attachment:
                product_attachments.append(attachment)

        faq_rows = (
            await db.fetch(
                "SELECT question,answer FROM company_faqs WHERE company_id=$1 ORDER BY created_at DESC LIMIT 10",
                company_id,
            )
            if company_id
            else []
        )
        for row in faq_rows:
            chunks.append(f"FAQ: Q: {row['question']} A: {row['answer']}")

        kb_rows = (
            await db.fetch(
                "SELECT title,content,key_points FROM knowledge_base "
                "WHERE company_id=$1 AND ai_context_enabled=TRUE ORDER BY created_at DESC LIMIT 10",
                company_id,
            )
            if company_id
            else []
        )
        for row in kb_rows:
            chunks.append(f"KB: {row['title']} - {row['content']}")
            if row.get("key_points"):
                chunks.append(f"KB Key Points: {row['key_points']}")
    except Exception as exc:
        logger.error("Knowledge fetch failed: %s", exc)

    return {
        "knowledge_text": "\n".join(dict.fromkeys([chunk for chunk in chunks if chunk])),
        "products": selected_products,
        "product_ids": [product["id"] for product in selected_products],
        "product_attachments": product_attachments[:max_products],
        "rag_called": True,
        "public_company": public_company,
    }


async def get_company_knowledge(db, company_id: str | None = None, current_query: str = "", top_k: int = 5) -> str:
    context = await build_ai_context(
        db,
        company_id=company_id,
        current_query=current_query,
        max_products=max(5, int(top_k or 5)),
    )
    return context["knowledge_text"]


def recent_customer_image_urls(conversation_context: list[dict]) -> list[str]:
    for message in reversed(conversation_context or []):
        if message.get("sender_type") != "customer":
            continue
        urls = []
        for attachment in message.get("attachments") or []:
            url = str(attachment.get("url") or attachment.get("file_url") or "").strip()
            file_type = str(attachment.get("type") or attachment.get("file_type") or "").strip().lower()
            if file_type == "image" and url.startswith("data:image/"):
                urls.append(url)
        if urls:
            return urls[:2]
    return []


__all__ = [
    "build_ai_context",
    "get_company_knowledge",
    "rank_products_for_query",
    "recent_customer_image_urls",
    "understand_product_query",
]
