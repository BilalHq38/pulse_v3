"""Templates + FAQs retriever.

Pulls the active response template (so the orchestrator can inject its
`style_prompt`) and the company's FAQ rows whose question text shares any
keyword with the user query. FAQ ranking is a simple keyword-overlap score
because FAQ entries are short and the corpus is bounded — vector search would
be overkill.

RLS note: response_templates has Row Level Security enabled. The fetch uses an
acquired connection with set_config so the policy passes regardless of which
pooled connection asyncpg returns.
"""

from __future__ import annotations

import asyncio
import re

from services.conversation_engine.schemas import ContextChunk


_WORD_RE = re.compile(r"[a-zA-Z0-9']+")
_FAQ_MIN_RELEVANCE_SCORE = 0.15  # FAQs with score below this are too weakly matched to be useful
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "i", "in", "is", "it", "its", "of", "on", "or",
    "that", "the", "this", "to", "was", "what", "when", "where", "why",
    "with", "you", "your", "do", "does", "how", "can", "we",
}


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD_RE.findall(text or "") if t.lower() not in _STOPWORDS and len(t) > 1}


async def _set_tenant(conn, company_id: str) -> None:
    try:
        await conn.execute(
            "SELECT set_config('app.current_company', $1, false)",
            company_id,
        )
    except Exception:
        pass


class TemplatesAndFaqsRetriever:
    source_type = "faq"

    async def fetch(self, db, *, company_id: str, query: str, top_k: int = 4) -> list[ContextChunk]:
        if not company_id:
            return []
        query_tokens = _tokens(query)
        chunks: list[ContextChunk] = []

        template_chunks, faq_rows = await asyncio.gather(
            self._fetch_active_template(db, company_id),
            self._fetch_faqs(db, company_id),
        )
        chunks.extend(template_chunks)

        scored: list[tuple[float, dict]] = []
        for record in faq_rows:
            question = str(record.get("question") or "")
            if not question:
                continue
            faq_tokens = _tokens(question + " " + str(record.get("answer") or ""))
            if not faq_tokens:
                continue
            if not query_tokens:
                overlap = 0
            else:
                overlap = len(query_tokens & faq_tokens)
            if overlap == 0 and query_tokens:
                continue
            score = overlap / max(len(query_tokens), 1) if query_tokens else 0.5
            if score < _FAQ_MIN_RELEVANCE_SCORE:
                continue
            scored.append((score, record))

        scored.sort(key=lambda item: item[0], reverse=True)
        for score, record in scored[: max(1, int(top_k))]:
            question = str(record.get("question") or "")
            answer = str(record.get("answer") or "")
            chunks.append(
                ContextChunk(
                    source_type="faq",
                    source_id=str(record.get("id") or ""),
                    title=question or "FAQ",
                    content=f"Q: {question}\nA: {answer}",
                    metadata={"category": str(record.get("category") or "")},
                    relevance_score=min(1.0, max(0.0, score)),
                )
            )
        return chunks

    async def _fetch_active_template(self, db, company_id: str) -> list[ContextChunk]:
        """Fetch default response template with RLS-safe connection."""
        try:
            async with db.acquire() as conn:
                await _set_tenant(conn, company_id)
                row = await conn.fetchrow(
                    "SELECT id, name, style_prompt FROM response_templates "
                    "WHERE company_id = $1 AND is_default = TRUE LIMIT 1",
                    company_id,
                )
        except AttributeError:
            # db is a plain connection
            try:
                row = await db.fetchrow(
                    "SELECT id, name, style_prompt FROM response_templates "
                    "WHERE company_id = $1 AND is_default = TRUE LIMIT 1",
                    company_id,
                )
            except Exception:
                return []
        except Exception:
            return []

        if not row:
            return []
        record = dict(row)
        return [
            ContextChunk(
                source_type="template",
                source_id=str(record.get("id") or ""),
                title=str(record.get("name") or "Default template"),
                content=str(record.get("style_prompt") or ""),
                metadata={"name": str(record.get("name") or "")},
                relevance_score=1.0,
            )
        ]

    async def _fetch_faqs(self, db, company_id: str) -> list[dict]:
        """Fetch FAQ rows. company_faqs typically has no RLS, but use acquire for safety."""
        try:
            faq_rows = await db.fetch(
                "SELECT id, question, answer, category "
                "FROM company_faqs WHERE company_id = $1 ORDER BY updated_at DESC NULLS LAST LIMIT 200",
                company_id,
            )
            return [dict(r) for r in (faq_rows or [])]
        except Exception:
            return []
