"""Product retriever — thin wrapper around the existing hybrid ranker.

The heavy lifting (keyword + vector + history boost, query understanding,
catalog caching) already lives in `services.ai_service.rag.rank_products_for_query`.
We call into it and translate the returned product dicts into ContextChunks.

Relevance is approximated from the ranker's relative ordering because the
existing function does not surface its internal score. This is good enough for
the compression and budgeter layers, which only use the score for trimming
order, not for absolute thresholding.

`OrderRelatedProductRetriever` is the proactive-upsell variant: instead of
searching the catalog by the user's query, it pulls the products linked to
the purchased item via `product_relationships`. The orchestrator swaps it in
for the default ProductRetriever when the turn is a proactive upsell anchored
to a specific order_id.

Each chunk's `content` includes the public product URL so the LLM has the
exact string to drop into its reply, and `metadata.public_url` is set so the
orchestrator can fall back to a product card when the model declines to
write the URL inline.
"""

from __future__ import annotations

import asyncio
import logging
import re

import time as _time

from services.conversation_engine.product_ranker import _format_product_context, rank_products_for_query
from services.conversation_engine.schemas import ContextChunk
from shared.config import frontend_url
from shared.product_ref_token import create_ref_token

# Short-lived in-process cache for the full product catalog (browse queries).
# Products change infrequently; 30 seconds cuts repeated DB reads per burst.
# Cache stores raw product rows + company_slug + images dict — NOT ContextChunks.
# ContextChunks contain customer-specific signed ref-token URLs and must be
# rebuilt per request; caching them would leak one customer's URL to another.
_CATALOG_CACHE: dict[str, tuple[float, list, str, dict]] = {}
_CATALOG_TTL = 30.0

logger = logging.getLogger(__name__)

# Queries that ask for a catalog overview rather than a specific product.
# When matched, the retriever returns category names instead of individual products.
_BROWSE_RE = re.compile(
    r"\b(what (products?|items?|do you (sell|have|carry|offer))|"
    r"show (me )?(all |your )?(products?|catalog|catalogue|collection|range|items?)|"
    r"(list|browse|see) (your |all )?(products?|catalog|catalogue|collection|items?)|"
    r"product (catalog|catalogue|list|range|collection)|"
    r"what('?s| is) (available|in stock|on offer)|"
    r"full (catalog|catalogue|collection|range)|"
    r"all (your |the )?(products?|items?))\b",
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
_IMAGE_REFERENCE_RE = re.compile(
    r"\b("
    r"image|images|photo|photos|picture|pictures|"
    r"show\s+me\s+(?:it|them|those|these)|"
    r"can\s+i\s+see\s+(?:it|them|those|these)"
    r")\b",
    re.IGNORECASE,
)


async def _set_tenant(conn, company_id: str) -> None:
    """Set app.current_company on this connection so FORCE RLS passes."""
    try:
        await conn.execute("SELECT set_config('app.current_company', $1, false)", company_id)
    except Exception:
        pass


async def _rls_fetch(db, company_id: str, sql: str, *args) -> list:
    """Acquire a pool connection, set the tenant, then run a fetch."""
    try:
        async with db.acquire() as conn:
            await _set_tenant(conn, company_id)
            return list(await conn.fetch(sql, *args))
    except AttributeError:
        # db is already a plain connection — use directly
        try:
            return list(await db.fetch(sql, *args))
        except Exception as exc:
            logger.warning("product_rls_fetch_failed company_id=%s error=%s", company_id, exc)
            return []
    except Exception as exc:
        logger.warning("product_rls_fetch_failed company_id=%s error=%s", company_id, exc)
        return []


async def _rls_fetchrow(db, company_id: str, sql: str, *args):
    rows = await _rls_fetch(db, company_id, sql, *args)
    return rows[0] if rows else None


async def _fetch_categories(db, company_id: str) -> list[ContextChunk]:
    """Return a single ContextChunk listing the product categories available."""
    if not company_id:
        return []
    try:
        rows = await _rls_fetch(
            db,
            company_id,
            "SELECT category, COUNT(*) AS cnt FROM company_products "
            "WHERE company_id = $1 AND COALESCE(status, 'active') != 'archived' "
            "AND TRIM(category) != '' "
            "GROUP BY category ORDER BY cnt DESC LIMIT 20",
            company_id,
        )
    except Exception:
        return []
    if not rows:
        return []
    cats = [str(r["category"]) for r in rows if r.get("category")]
    if not cats:
        return []
    content = (
        "Available product categories: " + ", ".join(cats) + ". "
        "When listing categories, present them as a short bullet list and invite "
        "the customer to ask about any specific category they are interested in."
    )
    return [
        ContextChunk(
            source_type="product",
            source_id="categories",
            title="Product Categories",
            content=content,
            metadata={"is_category_overview": True},
            relevance_score=1.0,
        )
    ]


async def _fetch_first_images(db, product_ids: list[str], company_id: str = "") -> dict[str, str]:
    """Batch-fetch the first image URL for each product with RLS context set."""
    if not product_ids:
        return {}
    try:
        if company_id:
            rows = await _rls_fetch(
                db,
                company_id,
                "SELECT DISTINCT ON (pi.product_id) pi.product_id, pi.image_url "
                "FROM product_images pi "
                "JOIN company_products cp ON cp.id = pi.product_id "
                "WHERE pi.product_id = ANY($1::text[]) AND cp.company_id = $2 "
                "ORDER BY pi.product_id, pi.sort_order ASC, pi.id ASC",
                product_ids,
                company_id,
            )
        else:
            rows = await _rls_fetch(
                db,
                "",
                "SELECT DISTINCT ON (product_id) product_id, image_url "
                "FROM product_images WHERE product_id = ANY($1::text[]) "
                "ORDER BY product_id, sort_order ASC, id ASC",
                product_ids,
            )
        return {str(r["product_id"]): str(r["image_url"]) for r in (rows or [])}
    except Exception:
        return {}


async def _resolve_company_slug(db, company_id: str) -> str:
    if not company_id:
        return ""
    try:
        row = await _rls_fetchrow(
            db,
            company_id,
            "SELECT slug FROM companies WHERE id = $1",
            company_id,
        )
    except Exception:
        return ""
    if not row:
        return ""
    return str((dict(row) if row else {}).get("slug") or "").strip()


def _build_public_url(company_slug: str, product: dict) -> str:
    slug = str(product.get("slug") or "").strip()
    if company_slug and slug:
        return f"/c/{company_slug}/product/{slug}"
    manual = str(product.get("links") or "").strip()
    if manual.startswith("http://") or manual.startswith("https://"):
        return manual
    return ""


def _category_label(category: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(category or "").strip().lower())
    return cleaned or "products"


def _is_referential_more_request(query: str) -> bool:
    return bool(_REFERENTIAL_MORE_RE.search(str(query or "")))


def _is_image_reference_request(query: str) -> bool:
    return bool(_IMAGE_REFERENCE_RE.search(str(query or "")))


def _product_row_to_chunk(
    product: dict,
    *,
    score: float,
    company_slug: str = "",
    company_id: str = "",
    image_url: str = "",
    customer_id: str = "",
    session_id: str = "",
) -> ContextChunk | None:
    product_id = str(product.get("id") or "").strip()
    if not product_id:
        return None
    public_url = _build_public_url(company_slug, product)
    content = _format_product_context(product)
    if public_url:
        # Build an absolute URL so Gemini can write it directly into the reply.
        # Relative paths (/c/<slug>/product/<slug>) are prefixed with the
        # configured FRONTEND_URL so the customer gets a clickable link.
        if public_url.startswith("/"):
            base = frontend_url().rstrip("/")
            absolute_url = f"{base}{public_url}" if base else public_url
        else:
            absolute_url = public_url
        # Append a signed opaque ref token so the buy endpoint can link the
        # order back to this customer without exposing internal IDs in the URL.
        if customer_id and company_id:
            ref = create_ref_token(
                customer_id=customer_id,
                session_id=session_id,
                company_id=company_id,
            )
            sep = "&" if "?" in absolute_url else "?"
            absolute_url = f"{absolute_url}{sep}ref={ref}"
        content = f"{content} | Product page: {absolute_url}"
        public_url = absolute_url
    if image_url:
        content = f"{content} | Image: available"
    return ContextChunk(
        source_type="product",
        source_id=product_id,
        title=str(product.get("name") or "Product"),
        content=content,
        metadata={
            "name": str(product.get("name") or ""),
            "price": product.get("price"),
            "price_currency": str(product.get("price_currency") or ""),
            "category": str(product.get("category") or ""),
            "slug": str(product.get("slug") or ""),
            "links": str(product.get("links") or ""),
            "public_url": public_url,
            "stock_quantity": product.get("stock_quantity"),
            "image_url": image_url,
        },
        relevance_score=score,
    )


class ProductRetriever:
    source_type = "product"

    def __init__(self, *, customer_id: str = "", session_id: str = "") -> None:
        self._customer_id = customer_id
        self._session_id = session_id

    async def fetch(self, db, *, company_id: str, query: str, top_k: int = 6) -> list[ContextChunk]:
        if not company_id or not query:
            return []

        referential = await self._fetch_referential_context(
            db,
            company_id=company_id,
            query=query,
            top_k=top_k,
        )
        if referential is not None:
            return referential

        # For catalog-browse queries ("what products do you have?"), the scored
        # ranker rank_products_for_query returns 0 results because generic browse
        # phrases score below its relevance threshold. Bypass the ranker and
        # directly load the top catalog entries so browse queries always have
        # product context and image cards.
        if _BROWSE_RE.search(query):
            return await self._fetch_full_catalog(db, company_id=company_id, top_k=top_k)

        # Acquire a connection with RLS context set so rank_products_for_query
        # can read company_products (FORCE RLS requires app.current_company).
        try:
            async with db.acquire() as conn:
                await _set_tenant(conn, company_id)
                products = await rank_products_for_query(
                    conn,
                    company_id,
                    query,
                    limit=max(1, int(top_k)),
                )
        except AttributeError:
            # db is already a plain connection — use directly
            try:
                products = await rank_products_for_query(db, company_id, query, limit=max(1, int(top_k)))
            except Exception:
                products = []
        except Exception:
            # Ranker threw (e.g. connection in bad state after embedding timeout).
            # Fall through to _fetch_full_catalog below so the LLM always has context.
            products = []

        # If the ranker returned nothing (query too generic or below threshold),
        # fall back to the full catalog so the LLM always has product context.
        if not products:
            return await self._fetch_full_catalog(db, company_id=company_id, top_k=top_k)
        product_ids = [str(p.get("id") or "") for p in products if p.get("id")]
        company_slug, images = await asyncio.gather(
            _resolve_company_slug(db, company_id),
            _fetch_first_images(db, product_ids, company_id=company_id),
        )
        chunks: list[ContextChunk] = []
        denom = max(1, len(products))
        for index, product in enumerate(products):
            score = (denom - index) / denom
            pid = str(product.get("id") or "")
            chunk = _product_row_to_chunk(
                product,
                score=score,
                company_slug=company_slug,
                company_id=company_id,
                image_url=images.get(pid, ""),
                customer_id=self._customer_id,
                session_id=self._session_id,
            )
            if chunk is not None:
                chunks.append(chunk)
        return chunks

    async def _fetch_referential_context(
        self,
        db,
        *,
        company_id: str,
        query: str,
        top_k: int,
    ) -> list[ContextChunk] | None:
        wants_more = _is_referential_more_request(query)
        wants_images = _is_image_reference_request(query)
        if not (wants_more or wants_images):
            return None
        if not (self._customer_id and self._session_id):
            return None

        try:
            from memory_engine.long_term import LongTermMemory  # noqa: PLC0415

            memory = LongTermMemory()
            context = await memory._fetch_shown_product_context(
                db,
                company_id,
                self._customer_id,
                self._session_id,
            )
        except Exception:
            context = {}

        shown_ids = [
            str(item).strip()
            for item in (context.get("product_ids") or context.get("last_shown_product_ids") or [])
            if str(item).strip()
        ]
        if not shown_ids:
            return []

        if wants_images and not wants_more:
            return await self._fetch_products_by_ids(
                db,
                company_id=company_id,
                product_ids=list(reversed(shown_ids[-3:])),
                top_k=min(3, max(1, int(top_k))),
            )

        category = str(context.get("last_product_category") or "").strip()
        if not category:
            category = await self._infer_category_from_products(
                db,
                company_id=company_id,
                product_ids=shown_ids,
            )
        if not category:
            return []

        rows = await _rls_fetch(
            db,
            company_id,
            "SELECT id, name, product_title, category, product_type, price, "
            "price_currency, description, stock_quantity, slug, links "
            "FROM company_products "
            "WHERE company_id = $1 "
            "AND LOWER(COALESCE(category, '')) = LOWER($2) "
            "AND NOT (id = ANY($3::text[])) "
            "AND COALESCE(status, 'active') != 'archived' "
            "ORDER BY name ASC LIMIT $4",
            company_id,
            category,
            shown_ids,
            max(1, int(top_k)),
        )
        if not rows:
            label = _category_label(category)
            return [
                ContextChunk(
                    source_type="product",
                    source_id=f"end-of-catalog:{label}",
                    title=f"No more {label}",
                    content=f"No more products are available in category: {label}.",
                    metadata={"is_end_of_catalog": True, "category": label},
                    relevance_score=1.0,
                )
            ]
        return await self._rows_to_chunks(
            db,
            company_id=company_id,
            products=[dict(row) for row in rows],
        )

    async def _fetch_products_by_ids(
        self,
        db,
        *,
        company_id: str,
        product_ids: list[str],
        top_k: int,
    ) -> list[ContextChunk]:
        ids = [str(item).strip() for item in product_ids if str(item).strip()]
        if not ids:
            return []
        rows = await _rls_fetch(
            db,
            company_id,
            "SELECT id, name, product_title, category, product_type, price, "
            "price_currency, description, stock_quantity, slug, links "
            "FROM company_products "
            "WHERE company_id = $1 AND id = ANY($2::text[]) "
            "AND COALESCE(status, 'active') != 'archived' "
            "ORDER BY array_position($2::text[], id) LIMIT $3",
            company_id,
            ids,
            max(1, int(top_k)),
        )
        return await self._rows_to_chunks(
            db,
            company_id=company_id,
            products=[dict(row) for row in rows],
        )

    async def _infer_category_from_products(
        self,
        db,
        *,
        company_id: str,
        product_ids: list[str],
    ) -> str:
        ids = [str(item).strip() for item in product_ids if str(item).strip()]
        if not ids:
            return ""
        rows = await _rls_fetch(
            db,
            company_id,
            "SELECT id, category FROM company_products "
            "WHERE company_id = $1 AND id = ANY($2::text[]) "
            "ORDER BY array_position($2::text[], id)",
            company_id,
            ids,
        )
        categories = {
            str(dict(row).get("id") or ""): str(dict(row).get("category") or "").strip()
            for row in rows
        }
        for product_id in reversed(ids):
            category = categories.get(product_id, "")
            if category:
                return category
        return ""

    async def _rows_to_chunks(
        self,
        db,
        *,
        company_id: str,
        products: list[dict],
    ) -> list[ContextChunk]:
        if not products:
            return []
        product_ids = [str(p.get("id") or "") for p in products if p.get("id")]
        company_slug, images = await asyncio.gather(
            _resolve_company_slug(db, company_id),
            _fetch_first_images(db, product_ids, company_id=company_id),
        )
        chunks: list[ContextChunk] = []
        denom = max(1, len(products))
        for index, product in enumerate(products):
            pid = str(product.get("id") or "")
            chunk = _product_row_to_chunk(
                product,
                score=(denom - index) / denom,
                company_slug=company_slug,
                company_id=company_id,
                image_url=images.get(pid, ""),
                customer_id=self._customer_id,
                session_id=self._session_id,
            )
            if chunk is not None:
                chunks.append(chunk)
        return chunks

    async def _fetch_full_catalog(self, db, *, company_id: str, top_k: int = 6) -> list[ContextChunk]:
        """Return top-N active products ordered by name — used when the scored
        ranker returns nothing (browse / generic queries).

        The in-process cache stores raw product rows + company_slug + images so
        that customer-specific signed ref-token URLs are rebuilt fresh per
        request. Caching ContextChunks would embed one customer's URL into the
        catalog and return it to unrelated customers on cache hits.
        """
        cache_key = f"{company_id}:{top_k}"
        cached = _CATALOG_CACHE.get(cache_key)
        now = _time.monotonic()
        if cached and (now - cached[0]) < _CATALOG_TTL:
            _ts, products, company_slug, images = cached
        else:
            try:
                rows = await _rls_fetch(
                    db,
                    company_id,
                    "SELECT id, name, product_title, category, product_type, price, "
                    "price_currency, description, stock_quantity, slug, links "
                    "FROM company_products "
                    "WHERE company_id = $1 AND COALESCE(status, 'active') != 'archived' "
                    "ORDER BY name ASC LIMIT $2",
                    company_id,
                    max(1, int(top_k)),
                )
            except Exception:
                return []
            if not rows:
                return []
            products = [dict(r) for r in rows]
            product_ids = [str(p.get("id") or "") for p in products if p.get("id")]
            company_slug, images = await asyncio.gather(
                _resolve_company_slug(db, company_id),
                _fetch_first_images(db, product_ids, company_id=company_id),
            )
            _CATALOG_CACHE[cache_key] = (now, products, company_slug, images)

        # Rebuild ContextChunks fresh for this request so URLs carry the
        # current customer's signed ref token, not a cached customer's token.
        chunks: list[ContextChunk] = []
        denom = max(1, len(products))
        for index, product in enumerate(products):
            score = (denom - index) / denom
            pid = str(product.get("id") or "")
            chunk = _product_row_to_chunk(
                product,
                score=score,
                company_slug=company_slug,
                company_id=company_id,
                image_url=images.get(pid, ""),
                customer_id=self._customer_id,
                session_id=self._session_id,
            )
            if chunk is not None:
                chunks.append(chunk)
        return chunks


class OrderRelatedProductRetriever:
    """Proactive-upsell variant.

    Looks up the purchased product on `orders.product_id`, then walks
    `product_relationships` to find linked products. Their relevance score
    falls out of the `weight` column (clamped to [0, 1]).

    Returns an empty list when:
      - order_id is missing or unknown to this tenant
      - the purchased product has no relationships defined
      - any of the related products were deleted

    The orchestrator catches the empty case and the upsell turn either
    composes a generic suggestion (constrained by the no-hallucination
    validator) or falls back to the static safe message.
    """

    source_type = "product"

    def __init__(self, *, order_id: str) -> None:
        self._order_id = (order_id or "").strip()

    async def fetch(self, db, *, company_id: str, query: str, top_k: int = 4) -> list[ContextChunk]:
        if not company_id or not self._order_id:
            return []
        try:
            order_row = await _rls_fetchrow(
                db,
                company_id,
                "SELECT product_id FROM orders WHERE company_id = $1 AND id = $2",
                company_id,
                self._order_id,
            )
        except Exception:
            return []
        if not order_row:
            return []
        purchased_id = str((dict(order_row) if order_row else {}).get("product_id") or "").strip()
        if not purchased_id:
            return []
        try:
            rel_rows = await _rls_fetch(
                db,
                company_id,
                "SELECT pr.related_id AS id, pr.relation_kind, pr.weight, "
                "       cp.name, cp.price, cp.category, cp.slug, cp.links, "
                "       cp.stock_quantity, cp.product_title, cp.product_type, "
                "       cp.description, cp.features, cp.price_currency "
                "FROM product_relationships pr "
                "JOIN company_products cp ON cp.id = pr.related_id "
                "WHERE pr.company_id = $1 AND pr.product_id = $2 "
                "ORDER BY pr.weight DESC LIMIT $3",
                company_id,
                purchased_id,
                max(1, int(top_k)),
            )
        except Exception:
            return []
        company_slug = await _resolve_company_slug(db, company_id)
        rel_ids = [str((dict(r) if r else {}).get("id") or "") for r in (rel_rows or []) if r]
        images = await _fetch_first_images(db, rel_ids, company_id=company_id)
        chunks: list[ContextChunk] = []
        for row in rel_rows or []:
            record = dict(row)
            weight = float(record.get("weight") or 0.5)
            score = max(0.0, min(1.0, weight))
            pid = str(record.get("id") or "")
            chunk = _product_row_to_chunk(record, score=score, company_slug=company_slug, company_id=company_id, image_url=images.get(pid, ""))
            if chunk is None:
                continue
            chunk.metadata["relation_kind"] = str(record.get("relation_kind") or "")
            chunks.append(chunk)
        return chunks
