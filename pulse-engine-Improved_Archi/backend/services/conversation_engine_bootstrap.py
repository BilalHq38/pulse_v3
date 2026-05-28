"""Bootstrap seeds for the conversation engine.

Run on app startup via `bootstrap_ai_runtime`. Today this only seeds the three
default response templates (Friendly / Professional / Sales-Oriented) for
every existing company, with Friendly marked as the default. Re-runs are
safe — `INSERT ... ON CONFLICT DO NOTHING` keeps the operation idempotent.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


_DEFAULT_TEMPLATES: tuple[tuple[str, str, bool], ...] = (
    (
        "Friendly",
        (
            "Adopt a warm, conversational tone. Use second-person ('you'), greet the customer, "
            "and offer to help further at the end of each answer. Keep replies short — two to "
            "four sentences for simple questions."
        ),
        True,
    ),
    (
        "Professional",
        (
            "Adopt a clear, neutral, business-formal tone. Avoid contractions and slang. Lead "
            "each answer with the factual answer first, then add one sentence of context."
        ),
        False,
    ),
    (
        "Sales-Oriented",
        (
            "Adopt a confident, helpful tone focused on resolving the customer's purchase intent. "
            "When the question is about a product, surface the key value proposition and the "
            "next step (price, availability, link) without exaggerating or inventing details. "
            "Do not pressure the customer."
        ),
        False,
    ),
    (
        "Support-Focused",
        (
            "Adopt a patient, empathetic problem-solving tone. Acknowledge the customer's "
            "situation, restate the problem in one sentence so they know you understood, then "
            "give the next concrete step. Escalate to a human teammate when the context "
            "doesn't have a clear resolution."
        ),
        False,
    ),
)


def _new_template_id(seed: str) -> str:
    return f"rt_{int(time.time() * 1000):x}_{abs(hash(seed)) & 0xFFFFFFFF:x}"


async def ensure_company_response_templates(db, company_id: str) -> int:
    """Idempotently seed the default templates for one company.

    Returns the number of newly inserted rows. Safe to call from a hot GET
    endpoint: the lookup + insert is cheap (one short SELECT + up to four
    INSERTs that no-op on conflict), and only fires the inserts when the
    company has fewer than the expected number of templates.
    """
    if not company_id or db is None:
        return 0
    try:
        existing_rows = await db.fetch(
            "SELECT name FROM response_templates WHERE company_id = $1",
            company_id,
        )
    except Exception as exc:
        logger.warning("response_templates_lookup_failed company_id=%s error=%s", company_id, exc)
        return 0
    existing = {str(row.get("name") or "") for row in existing_rows or []}
    inserted = 0
    for name, style_prompt, is_default in _DEFAULT_TEMPLATES:
        if name in existing:
            continue
        try:
            await db.execute(
                "INSERT INTO response_templates (id, company_id, name, style_prompt, is_default) "
                "VALUES ($1, $2, $3, $4, $5) "
                "ON CONFLICT (company_id, name) DO NOTHING",
                _new_template_id(f"{company_id}:{name}"),
                company_id,
                name,
                style_prompt,
                is_default,
            )
            inserted += 1
        except Exception as exc:
            logger.warning(
                "response_template_seed_failed company_id=%s name=%s error=%s",
                company_id, name, exc,
            )
    return inserted


async def ensure_default_response_templates(db) -> None:
    """Seed the default templates for every company at startup."""
    if db is None:
        return
    try:
        companies = await db.fetch("SELECT id FROM companies")
    except Exception as exc:
        logger.info("response_templates_bootstrap_skipped reason=%s", exc)
        return
    for company in companies or []:
        company_id = company.get("id") if isinstance(company, dict) else company["id"]
        await ensure_company_response_templates(db, company_id)
