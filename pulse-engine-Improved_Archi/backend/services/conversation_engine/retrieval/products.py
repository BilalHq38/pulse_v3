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

import logging

from services.ai_service.rag import _format_product_context, rank_products_for_query
from services.conversation_engine.schemas import ContextChunk

logger = logging.getLogger(__name__)


async def _resolve_company_slug(db, company_id: str) -> str:
    if not company_id:
        return ""
    try:
        row = await db.fetchrow(
            "SELECT slug FROM companies WHERE id = $1",
            company_id,
        )
    except Exception:
        return ""
    if not row:
        return ""
    return str((dict(row) if row else {}).get("slug") or "").strip()


def _build_public_url(company_slug: str, product: dict) -> str:
    manual = str(product.get("links") or "").strip()
    if manual.startswith("http://") or manual.startswith("https://"):
        return manual
    slug = str(product.get("slug") or "").strip()
    if company_slug and slug:
        return f"/c/{company_slug}/product/{slug}"
    return ""


def _product_row_to_chunk(product: dict, *, score: float, company_slug: str = "") -> ContextChunk | None:
    product_id = str(product.get("id") or "").strip()
    if not product_id:
        return None
    public_url = _build_public_url(company_slug, product)
    content = _format_product_context(product)
    if public_url:
        # Putting the URL inside the context block lets the LLM cite it
        # directly. The validator's link integrity check still gates what
        # actually ships to the customer.
        content = f"{content} | Public link: {public_url}"
    return ContextChunk(
        source_type="product",
        source_id=product_id,
        title=str(product.get("name") or "Product"),
        content=content,
        metadata={
            "name": str(product.get("name") or ""),
            "price": product.get("price"),
            "category": str(product.get("category") or ""),
            "slug": str(product.get("slug") or ""),
            "links": str(product.get("links") or ""),
            "public_url": public_url,
            "stock_quantity": product.get("stock_quantity"),
        },
        relevance_score=score,
    )


class ProductRetriever:
    source_type = "product"

    async def fetch(self, db, *, company_id: str, query: str, top_k: int = 6) -> list[ContextChunk]:
        if not company_id or not query:
            return []
        try:
            products = await rank_products_for_query(
                db,
                company_id,
                query,
                limit=max(1, int(top_k)),
            )
        except Exception:
            return []
        company_slug = await _resolve_company_slug(db, company_id)
        chunks: list[ContextChunk] = []
        denom = max(1, len(products))
        for index, product in enumerate(products):
            # Rank-based normalisation: top product -> 1.0, last -> 1/denom.
            score = (denom - index) / denom
            chunk = _product_row_to_chunk(product, score=score, company_slug=company_slug)
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
            order_row = await db.fetchrow(
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
            rel_rows = await db.fetch(
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
        chunks: list[ContextChunk] = []
        for row in rel_rows or []:
            record = dict(row)
            weight = float(record.get("weight") or 0.5)
            score = max(0.0, min(1.0, weight))
            chunk = _product_row_to_chunk(record, score=score, company_slug=company_slug)
            if chunk is None:
                continue
            chunk.metadata["relation_kind"] = str(record.get("relation_kind") or "")
            chunks.append(chunk)
        return chunks
