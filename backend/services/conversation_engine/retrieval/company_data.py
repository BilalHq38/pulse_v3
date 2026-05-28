"""Company profile + settings retriever.

Returns a single ContextChunk that captures the tenant-level facts the LLM
needs as ambient context (name, description, website, support contacts, etc.).

Schema reality (from sql_schema.sql):
  companies       — id, name, slug  (core identity only)
  company_settings — description, industry, tagline, phone, support_email,
                     website_address, country, city, bh_start, bh_end

The two tables are joined on company_settings.company_id = companies.id.
Columns that don't exist on the queried table are NOT fetched — unknown
columns cause the asyncpg query to fail silently and return [], which
produces a "no company context" turn that the LLM handles with "I don't
know" rather than giving the customer real business information.
"""

from __future__ import annotations

import time

from services.conversation_engine.schemas import ContextChunk

# In-process cache for company data — this rarely changes and is fetched
# on every single turn.  A 60-second TTL cuts one DB round-trip per turn.
_CACHE: dict[str, tuple[float, list[ContextChunk]]] = {}
_CACHE_TTL = 60.0


class CompanyDataRetriever:
    source_type = "company_data"

    async def fetch(self, db, *, company_id: str, query: str, top_k: int = 1) -> list[ContextChunk]:
        if not company_id:
            return []
        cached = _CACHE.get(company_id)
        if cached and (time.monotonic() - cached[0]) < _CACHE_TTL:
            return cached[1]
        try:
            row = await db.fetchrow(
                """
                SELECT
                    c.id,
                    c.name,
                    c.slug,
                    cs.industry,
                    cs.tagline,
                    cs.description,
                    cs.phone,
                    cs.support_email,
                    cs.website_address,
                    cs.country,
                    cs.city,
                    cs.bh_start,
                    cs.bh_end
                FROM companies c
                LEFT JOIN company_settings cs ON cs.company_id = c.id
                WHERE c.id = $1
                """,
                company_id,
            )
        except Exception:
            return []
        if not row:
            return []

        record = dict(row)
        name = str(record.get("name") or "")

        # Build a structured text block — only include lines that have a value.
        field_labels = {
            "name": "Company name",
            "tagline": "Tagline",
            "description": "About",
            "industry": "Industry",
            "website_address": "Website",
            "support_email": "Support email",
            "phone": "Phone",
            "city": "City",
            "country": "Country",
            "bh_start": "Business hours start",
            "bh_end": "Business hours end",
        }
        lines: list[str] = []
        for field, label in field_labels.items():
            val = str(record.get(field) or "").strip()
            if val:
                lines.append(f"{label}: {val}")

        content = "\n".join(lines)
        if not content:
            # Fallback: at least include the company name so identity questions
            # can be answered even when company_settings has no data yet.
            content = f"Company name: {name}" if name else ""

        if not content:
            return []

        chunks = [
            ContextChunk(
                source_type="company_data",
                source_id=str(record.get("id") or company_id),
                title=name or "Company profile",
                content=content,
                metadata={"slug": str(record.get("slug") or "")},
                relevance_score=1.0,
            )
        ]
        _CACHE[company_id] = (time.monotonic(), chunks)
        return chunks
