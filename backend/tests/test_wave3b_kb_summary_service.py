"""Wave 3b — KB summary service tests."""

from __future__ import annotations

import asyncio

import pytest

from services.ai_service import kb_summary_service


def test_extract_facts_picks_up_product_names_and_amounts():
    facts = kb_summary_service._extract_facts(
        "Our Pulse Engine Pro plan costs $49.99 with a 14 day trial."
    )
    # The regex matches Pulse Engine Pro, the dollar amount, and the day phrase.
    text = " ".join(facts)
    assert "Pulse Engine Pro" in text
    assert any("$49.99" in fact or "49.99" in fact for fact in facts)
    assert any("14 day" in fact or "14days" in fact.replace(" ", "") for fact in facts)


def test_preservation_guard_accepts_paraphrase_that_keeps_key_facts():
    source = "Pulse Engine Pro costs $49.99 with a 14 day trial. Contact sales for enterprise."
    summary = "Pulse Engine Pro: $49.99, 14 day trial available."
    assert kb_summary_service._summary_preserves_facts(source, summary)


def test_preservation_guard_rejects_summary_that_drops_the_product_name():
    source = "Pulse Engine Pro costs $49.99 with a 14 day trial."
    summary = "Our paid plan starts at a low monthly price with a trial period."
    assert not kb_summary_service._summary_preserves_facts(source, summary)


def test_preservation_guard_passes_for_empty_source_facts():
    assert kb_summary_service._summary_preserves_facts("hello world", "hi everyone")


class _FakeDb:
    def __init__(self, content: str):
        self._content = content
        self.executed: list[tuple] = []

    async def fetchrow(self, sql, *args):
        return {"id": args[1], "content": self._content}

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


def test_summarise_kb_row_persists_summary_when_facts_preserved(monkeypatch):
    long_content = (
        "Pulse Engine Pro is our premium plan. It costs $49.99 per month "
        "with a 14 day free trial. Cancel anytime. " * 30
    )
    db = _FakeDb(content=long_content)

    async def fake_call_gemini(prompt, *_args, **_kwargs):
        return "Pulse Engine Pro: $49.99 monthly with a 14 day trial."

    monkeypatch.setattr(kb_summary_service, "call_gemini", fake_call_gemini)
    ok = asyncio.run(kb_summary_service.summarise_kb_row(db, company_id="co1", kb_id="kb1"))
    assert ok is True
    # Persist statement was issued with the summary text.
    persist_sql = [s for s, _ in db.executed if "SET summary = $1" in s]
    assert persist_sql, db.executed


def test_summarise_kb_row_short_row_is_skipped_but_timestamped(monkeypatch):
    db = _FakeDb(content="too short to summarise")

    async def fake_call_gemini(prompt, *_args, **_kwargs):
        raise AssertionError("should not be called for short rows")

    monkeypatch.setattr(kb_summary_service, "call_gemini", fake_call_gemini)
    ok = asyncio.run(kb_summary_service.summarise_kb_row(db, company_id="co1", kb_id="kb1"))
    assert ok is False
    # Even short rows get a watermark so the backfill query won't repick them.
    watermark_sql = [s for s, _ in db.executed if "summary_generated_at = NOW()" in s and "summary = ''" in s]
    assert watermark_sql, db.executed


def test_summarise_kb_row_drops_summary_that_fails_fact_guard(monkeypatch):
    long_content = "Pulse Engine Pro costs $49.99. " * 50
    db = _FakeDb(content=long_content)

    async def fake_call_gemini(prompt, *_args, **_kwargs):
        return "Our paid plan offers great value to customers."

    monkeypatch.setattr(kb_summary_service, "call_gemini", fake_call_gemini)
    ok = asyncio.run(kb_summary_service.summarise_kb_row(db, company_id="co1", kb_id="kb1"))
    assert ok is False
    # We still write an empty summary + timestamp so the row isn't retried forever.
    persisted_empty = [s for s, args in db.executed if "summary = ''" in s]
    assert persisted_empty, db.executed
