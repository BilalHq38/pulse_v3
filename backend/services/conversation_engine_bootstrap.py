"""Bootstrap seeds for the conversation engine.

Run on app startup via `bootstrap_ai_runtime`. Seeds the four default response
templates (Friendly / Professional / Sales-Oriented / Support-Focused) for
every existing company, with Friendly marked as the default. Re-runs are
safe — `INSERT ... ON CONFLICT DO NOTHING` keeps the operation idempotent.

RLS note: response_templates has Row Level Security. All writes use
platform_admin_context so they are never blocked by the tenant policy.
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
    endpoint — only fires inserts for missing templates.
    Uses platform_admin_context to bypass Row Level Security.
    """
    if not company_id or db is None:
        return 0

    try:
        from shared.database import platform_admin_context
    except ImportError:
        logger.warning("platform_admin_context unavailable; skipping template seed")
        return 0

    try:
        async with platform_admin_context(db) as conn:
            existing_rows = await conn.fetch(
                "SELECT name FROM response_templates WHERE company_id = $1",
                company_id,
            )
            existing = {str(row["name"]) for row in (existing_rows or [])}
            inserted = 0
            for name, style_prompt, is_default in _DEFAULT_TEMPLATES:
                if name in existing:
                    continue
                try:
                    await conn.execute(
                        "INSERT INTO response_templates "
                        "(id, company_id, name, style_prompt, is_default) "
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
    except Exception as exc:
        logger.warning(
            "ensure_company_response_templates failed company_id=%s error=%s",
            company_id, exc,
        )
        return 0


async def ensure_default_response_templates(db) -> None:
    """Seed the default templates for every company at startup."""
    if db is None:
        return
    try:
        from shared.database import platform_admin_context

        async with platform_admin_context(db) as conn:
            companies = await conn.fetch("SELECT id FROM companies")
    except Exception as exc:
        logger.info("response_templates_bootstrap_skipped reason=%s", exc)
        return
    for company in companies or []:
        company_id = company.get("id") if isinstance(company, dict) else company["id"]
        await ensure_company_response_templates(db, company_id)
