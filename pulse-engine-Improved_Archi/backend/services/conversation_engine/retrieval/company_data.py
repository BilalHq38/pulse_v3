"""Company profile + settings retriever.

Returns a single ContextChunk that captures the tenant-level facts the LLM
needs as ambient context (name, slug, business hours, support email, public
description, etc.). Latency is dominated by the single SELECT and the row is
small enough that we always include it.
"""

from __future__ import annotations

from typing import Final

from services.conversation_engine.schemas import ContextChunk


_COMPANY_FIELDS: Final = (
    "id",
    "name",
    "slug",
    "industry",
    "description",
    "primary_contact_email",
    "support_phone",
    "website",
    "country",
)


class CompanyDataRetriever:
    source_type = "company_data"

    async def fetch(self, db, *, company_id: str, query: str, top_k: int = 1) -> list[ContextChunk]:
        if not company_id:
            return []
        cols = ", ".join(_COMPANY_FIELDS)
        try:
            row = await db.fetchrow(
                f"SELECT {cols} FROM companies WHERE id = $1",
                company_id,
            )
        except Exception:
            return []
        if not row:
            return []
        # Be tolerant of extra/missing columns — the schema mirror is the source
        # of truth, but optional fields shouldn't break retrieval.
        record = dict(row)
        lines = [f"{key}: {record.get(key) or ''}" for key in _COMPANY_FIELDS if key in record]
        content = "\n".join(line for line in lines if line and not line.endswith(": "))
        return [
            ContextChunk(
                source_type="company_data",
                source_id=str(record.get("id") or company_id),
                title=str(record.get("name") or "Company profile"),
                content=content,
                metadata={"slug": record.get("slug") or ""},
                relevance_score=1.0,  # always relevant when present
            )
        ]
