"""
rank_products_for_query and _format_product_context have moved to
services.conversation_engine.product_ranker. The rest of this file
(build_ai_context, get_company_knowledge) remains here until the old
ai_service conversation path is fully removed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from collections import defaultdict
from difflib import SequenceMatcher

from core.utils import is_valid_image_url, normalize_product_images
from shared.cache import get_cache_client
from shared.config import frontend_url
from shared.metrics import increment_counter, observe_histogram, timed_metric
from shared.product_ref_token import create_ref_token
from services.ai_service.llm_tracking import has_embedding_budget_remaining
from services.ai_service.routing_guards import is_low_value_message
from services.conversation_engine.product_ranker import (  # noqa: F401
    rank_products_for_query,
    _format_product_context,
    understand_product_query,
    _should_skip_rag_query,
    _score_product,
    fuzzy_category_match,
    _catalog_kind_for_query,
    _load_product_catalog,
    _build_product_public_url,
    _build_product_attachment,
    _pick_product_image_url,
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
                "SELECT c.name AS company_name, c.slug AS company_slug, "
                "cs.industry, cs.tagline, cs.description, cs.website_address "
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
                "company_slug": str(profile.get("company_slug") or "").strip(),
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
            product["public_url"] = _build_product_public_url(
                product,
                company_slug=str(public_company.get("company_slug") or ""),
                company_id=str(company_id or ""),
                customer_id=customer_id,
                conversation_id=conversation_id,
            )
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
    "fuzzy_category_match",
    "get_company_knowledge",
    "rank_products_for_query",
    "recent_customer_image_urls",
    "understand_product_query",
]
