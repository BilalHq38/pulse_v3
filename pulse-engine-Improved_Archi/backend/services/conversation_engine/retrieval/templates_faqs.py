"""Templates + FAQs retriever.

Pulls the active response template (so the orchestrator can inject its
`style_prompt`) and the company's FAQ rows whose question text shares any
keyword with the user query. FAQ ranking is a simple keyword-overlap score
because FAQ entries are short and the corpus is bounded — vector search would
be overkill.
"""

from __future__ import annotations

import re

from services.conversation_engine.schemas import ContextChunk


_WORD_RE = re.compile(r"[a-zA-Z0-9']+")
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "i", "in", "is", "it", "its", "of", "on", "or",
    "that", "the", "this", "to", "was", "what", "when", "where", "why",
    "with", "you", "your", "do", "does", "how", "can", "we",
}


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD_RE.findall(text or "") if t.lower() not in _STOPWORDS and len(t) > 1}


class TemplatesAndFaqsRetriever:
    source_type = "faq"

    async def fetch(self, db, *, company_id: str, query: str, top_k: int = 4) -> list[ContextChunk]:
        if not company_id:
            return []
        query_tokens = _tokens(query)
        chunks: list[ContextChunk] = []

        chunks.extend(await self._fetch_active_template(db, company_id))

        try:
            faq_rows = await db.fetch(
                "SELECT id, question, answer, category "
                "FROM company_faqs WHERE company_id = $1 ORDER BY updated_at DESC NULLS LAST LIMIT 200",
                company_id,
            )
        except Exception:
            faq_rows = []

        scored: list[tuple[float, dict]] = []
        for row in faq_rows or []:
            record = dict(row)
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
        try:
            row = await db.fetchrow(
                "SELECT id, name, style_prompt FROM response_templates "
                "WHERE company_id = $1 AND is_default = TRUE LIMIT 1",
                company_id,
            )
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
