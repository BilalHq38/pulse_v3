"""
email_campaign_service.service — recipient resolution + async batch sender.

Design goals:
- Reuse tenant email channel delivery for campaign sends.
- Reuse identity fields already present on ``leads`` / ``customers`` tables
  (email, lifecycle_stage, source, tags). Never duplicate CRM logic.
- Filtering is declarative (lifecycle_stage, tags, source, channel), so the
  same resolver can be reused from the UI preview call and the async sender.
- The sender runs as a detached asyncio task with per-batch concurrency and
  updates the ``email_campaigns`` / ``email_campaign_recipients`` rows so the
  frontend can poll progress.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from core.utils import make_id
from services.db_helpers import get_ai_static_fallback_message, runtime_schema_ready
from services.ai_service.llm_client import call_model_json, get_active_llm_engine
from services.email_service import send_tenant_email_async
from shared.database import company_context
from shared.webhook_task_runner import create_safe_detached_task

logger = logging.getLogger(__name__)

# Keep concurrency modest; Brevo/SMTP both rate-limit and we share the loop
# with the rest of the API. Override via CAMPAIGN_SEND_CONCURRENCY env if needed.
DEFAULT_BATCH_CONCURRENCY = 10
RECIPIENT_HARD_CAP = 50_000
CAMPAIGN_COPY_MIN_OUTPUT_TOKENS = 1200
HTML_BODY_MIN_OUTPUT_TOKENS = 1000
CAMPAIGN_AI_OUTPUT_TOKEN_CEILING = 2048


class CampaignCopyDraft(BaseModel):
    subject: str = Field(min_length=1, max_length=220)
    body: str = Field(min_length=1, max_length=6000)
    html_body: str = Field(default="", max_length=12000)


class HtmlEmailBodyDraft(BaseModel):
    html_body: str = Field(min_length=1, max_length=12000)


class CampaignAIUnavailableError(RuntimeError):
    """Raised when campaign AI generation cannot safely produce content."""


class CampaignAIQuotaExceededError(CampaignAIUnavailableError):
    """Raised when the provider has clearly exhausted quota/budget."""


_UNSAFE_HTML_RE = re.compile(r"<\s*/?\s*(script|style|iframe|object|embed|form|input|button|link|meta)[^>]*>", re.I)
_UNSAFE_ATTR_RE = re.compile(r"\s+on[a-z]+\s*=\s*(['\"]).*?\1", re.I | re.S)
_JAVASCRIPT_URL_RE = re.compile(r"(href|src)\s*=\s*(['\"])\s*javascript:.*?\2", re.I | re.S)


def _sanitize_email_html(value: str) -> str:
    cleaned = str(value or "").strip()
    cleaned = _UNSAFE_HTML_RE.sub("", cleaned)
    cleaned = _UNSAFE_ATTR_RE.sub("", cleaned)
    cleaned = _JAVASCRIPT_URL_RE.sub("", cleaned)
    return cleaned[:12000]


def _is_quota_exhausted_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    markers = (
        "quota",
        "resource_exhausted",
        "insufficient_quota",
        "billing hard limit",
        "credit balance is too low",
        "all ai providers exhausted",
        "rate limit",
        "rate_limited",
        "429",
    )
    return any(marker in text for marker in markers)


def _campaign_ai_unavailable_message(kind: str = "copy") -> str:
    target = "campaign copy" if kind == "copy" else "HTML email body"
    return f"AI/API issue: {target} could not be generated. Please write or paste the response manually."


def campaign_ai_quota_message() -> str:
    return "AI auto-response is paused because the AI provider is unavailable. Please respond manually."


async def _campaign_static_fallback(db, company_id: str) -> str:
    return await get_ai_static_fallback_message(db, company_id)


# --------------------------------------------------------------------------- #
# Recipient resolution
# --------------------------------------------------------------------------- #


def _coerce_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _body_to_html(body: str) -> str:
    paragraphs = [segment.strip() for segment in str(body or "").split("\n\n") if segment.strip()]
    if not paragraphs:
        return ""
    return "".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs)


def _html_to_text(html_body: str) -> str:
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", str(html_body or ""))
    text = re.sub(r"(?i)</\s*p\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(re.sub(r"\n{3,}", "\n\n", text)).strip()


def _finalize_email_html(html_body: str, plain_body: str = "") -> str:
    inner = _sanitize_email_html(html_body or _body_to_html(plain_body))
    if not inner:
        return ""
    if re.search(r"<\s*html[\s>]", inner, re.I):
        return inner[:12000]
    return _sanitize_email_html(
        "<!doctype html><html><body style=\"margin:0;padding:0;background:#f6f7f9;\">"
        "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" "
        "style=\"border-collapse:collapse;background:#f6f7f9;\"><tr><td align=\"center\" "
        "style=\"padding:24px 12px;\"><table role=\"presentation\" width=\"100%\" cellspacing=\"0\" "
        "cellpadding=\"0\" style=\"max-width:640px;border-collapse:collapse;background:#ffffff;\">"
        "<tr><td style=\"font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.55;"
        "color:#111827;padding:28px;\">"
        f"{inner}"
        "</td></tr></table></td></tr></table></body></html>"
    )


def _campaign_ai_engine(engine: dict | None, *, min_output_tokens: int) -> dict:
    tuned = dict(engine or {})
    try:
        configured_max = int(tuned.get("max_tokens") or 0)
    except (TypeError, ValueError):
        configured_max = 0
    tuned["max_tokens"] = min(
        max(configured_max, int(min_output_tokens)),
        CAMPAIGN_AI_OUTPUT_TOKEN_CEILING,
    )
    return tuned


async def resolve_recipients(
    db,
    *,
    company_id: str,
    filters: dict,
    limit: int = RECIPIENT_HARD_CAP,
) -> list[dict]:
    """
    Resolve a filter dict to a de-duplicated list of ``{email, name, customer_id,
    lead_id}`` rows for the given tenant.

    Supported filters (all optional, combined as AND):
      - lifecycle_stage: str | list[str]  (matches customers.lifecycle_stage)
      - tags: str | list[str]             (matches customer_tags.tag OR lead_tags.tag)
      - source: str | list[str]           (matches leads.source e.g. facebook/instagram/web_chat)
      - channel: str | list[str]          (matches customer_channels.channel / lead_channels.channel)
      - audience: "customers" | "leads" | "both"  (default both)
    """
    if not company_id:
        return []

    lifecycle = _coerce_list(filters.get("lifecycle_stage"))
    tags = [t.lower() for t in _coerce_list(filters.get("tags"))]
    sources = _coerce_list(filters.get("source"))
    channels = _coerce_list(filters.get("channel"))
    customer_ids = _coerce_list(filters.get("customer_ids") or filters.get("selected_customer_ids"))
    lead_ids = _coerce_list(filters.get("lead_ids") or filters.get("selected_lead_ids"))
    audience = str(filters.get("audience") or "both").lower()
    if audience not in {"customers", "leads", "both"}:
        audience = "both"
    has_explicit_selection = bool(customer_ids or lead_ids)
    has_filter_selection = bool(lifecycle or tags or sources or channels)
    include_filter_matches = has_filter_selection or not has_explicit_selection

    rows: dict[str, dict] = {}

    # ---- customers side --------------------------------------------------- #
    if audience in {"customers", "both"}:
        if customer_ids:
            try:
                selected_customers = await db.fetch(
                    "SELECT id,name,email FROM customers "
                    "WHERE company_id=$1 AND id = ANY($2::text[]) AND email <> '' "
                    "ORDER BY created_at DESC LIMIT $3",
                    company_id,
                    customer_ids,
                    int(limit),
                )
            except Exception as exc:
                logger.warning("Selected customer recipient query failed: %s", exc)
                selected_customers = []
            for rec in selected_customers:
                email = str(rec.get("email") or "").strip().lower()
                if not email or "@" not in email:
                    continue
                rows.setdefault(
                    email,
                    {
                        "email": email,
                        "name": str(rec.get("name") or "").strip(),
                        "customer_id": str(rec.get("id") or ""),
                        "lead_id": "",
                    },
                )
        if include_filter_matches:
            sql = (
                "SELECT DISTINCT c.id, c.name, c.email FROM customers c "
                "LEFT JOIN customer_tags ct ON ct.customer_id = c.id "
                "LEFT JOIN customer_channels cc ON cc.customer_id = c.id "
                "WHERE c.company_id=$1 AND c.email <> ''"
            )
            args: list = [company_id]
            if lifecycle:
                args.append(lifecycle)
                sql += f" AND c.lifecycle_stage = ANY(${len(args)}::text[])"
            if tags:
                args.append(tags)
                sql += f" AND LOWER(ct.tag) = ANY(${len(args)}::text[])"
            if channels:
                args.append(channels)
                sql += f" AND cc.channel = ANY(${len(args)}::text[])"
            args.append(int(limit))
            sql += f" ORDER BY c.id LIMIT ${len(args)}"
            try:
                records = await db.fetch(sql, *args)
            except Exception as exc:
                logger.warning("Customer recipient query failed: %s", exc)
                records = []
            for rec in records:
                email = str(rec.get("email") or "").strip().lower()
                if not email or "@" not in email:
                    continue
                rows.setdefault(
                    email,
                    {
                        "email": email,
                        "name": str(rec.get("name") or "").strip(),
                        "customer_id": str(rec.get("id") or ""),
                        "lead_id": "",
                    },
                )

    # ---- leads side ------------------------------------------------------- #
    if audience in {"leads", "both"}:
        if lead_ids:
            try:
                selected_leads = await db.fetch(
                    "SELECT id,name,email FROM leads "
                    "WHERE company_id=$1 AND id = ANY($2::text[]) AND email <> '' "
                    "ORDER BY created_at DESC LIMIT $3",
                    company_id,
                    lead_ids,
                    int(limit),
                )
            except Exception as exc:
                logger.warning("Selected lead recipient query failed: %s", exc)
                selected_leads = []
            for rec in selected_leads:
                email = str(rec.get("email") or "").strip().lower()
                if not email or "@" not in email:
                    continue
                existing = rows.get(email)
                if existing:
                    if not existing.get("lead_id"):
                        existing["lead_id"] = str(rec.get("id") or "")
                    if not existing.get("name"):
                        existing["name"] = str(rec.get("name") or "").strip()
                else:
                    rows[email] = {
                        "email": email,
                        "name": str(rec.get("name") or "").strip(),
                        "customer_id": "",
                        "lead_id": str(rec.get("id") or ""),
                    }
        if include_filter_matches:
            sql = (
                "SELECT DISTINCT l.id, l.name, l.email FROM leads l "
                "LEFT JOIN lead_tags lt ON lt.lead_id = l.id "
                "LEFT JOIN lead_channels lc ON lc.lead_id = l.id "
                "WHERE l.company_id=$1 AND l.email <> ''"
            )
            args = [company_id]
            if sources:
                args.append(sources)
                sql += f" AND l.source = ANY(${len(args)}::text[])"
            if tags:
                args.append(tags)
                sql += f" AND LOWER(lt.tag) = ANY(${len(args)}::text[])"
            if channels:
                args.append(channels)
                sql += f" AND lc.channel = ANY(${len(args)}::text[])"
            args.append(int(limit))
            sql += f" ORDER BY l.id LIMIT ${len(args)}"
            try:
                records = await db.fetch(sql, *args)
            except Exception as exc:
                logger.warning("Lead recipient query failed: %s", exc)
                records = []
            for rec in records:
                email = str(rec.get("email") or "").strip().lower()
                if not email or "@" not in email:
                    continue
                existing = rows.get(email)
                if existing:
                    if not existing.get("lead_id"):
                        existing["lead_id"] = str(rec.get("id") or "")
                    if not existing.get("name"):
                        existing["name"] = str(rec.get("name") or "").strip()
                else:
                    rows[email] = {
                        "email": email,
                        "name": str(rec.get("name") or "").strip(),
                        "customer_id": "",
                        "lead_id": str(rec.get("id") or ""),
                    }

    resolved = list(rows.values())[:limit]
    return resolved


async def _load_campaign_product(db, *, company_id: str, product_id: str) -> dict | None:
    product_id = str(product_id or "").strip()
    if not product_id:
        return None
    product = await db.fetchrow(
        "SELECT * FROM company_products WHERE id=$1 AND company_id=$2 LIMIT 1",
        product_id,
        company_id,
    )
    if not product:
        return None
    result = dict(product)
    result["features"] = [
        str(row["feature"])
        for row in await db.fetch(
            "SELECT feature FROM product_features WHERE product_id=$1 ORDER BY sort_order",
            product_id,
        )
        if row.get("feature")
    ]
    result["images"] = [
        str(row["image_url"])
        for row in await db.fetch(
            "SELECT image_url FROM product_images WHERE product_id=$1 ORDER BY sort_order",
            product_id,
        )
        if row.get("image_url")
    ]
    return result


async def generate_campaign_copy(
    db,
    *,
    company_id: str,
    product_id: str,
    campaign_goal: str,
    audience_description: str,
    tone: str,
    call_to_action: str,
    offer_details: str = "",
    extra_context: str = "",
) -> dict:
    if not company_id:
        raise ValueError("company_id is required")

    async with company_context(db, company_id):
        product = await _load_campaign_product(db, company_id=company_id, product_id=product_id)
        engine = await get_active_llm_engine(db, company_id)
    if not product:
        raise ValueError("Selected product was not found")

    required_fields = {
        "campaign_goal": campaign_goal,
        "audience_description": audience_description,
        "tone": tone,
        "call_to_action": call_to_action,
    }
    missing = [label.replace("_", " ") for label, value in required_fields.items() if not str(value or "").strip()]
    if missing:
        raise ValueError(f"Missing campaign details: {', '.join(missing)}")

    feature_lines = (
        "\n".join(f"- {feature}" for feature in product.get("features") or [])
        or "- No structured features stored"
    )
    image_lines = "\n".join(f"- {image}" for image in product.get("images") or []) or "- No product images stored"
    # Build the prompt for the campaign copy generator.  The model must return valid JSON
    # containing only the keys "subject", "body" and "html_body".  We specify
    # explicit formatting rules to avoid malformed JSON (e.g. unquoted keys) that can
    # break JSON parsing.  The instructions below emphasize valid JSON syntax: use
    # double quotes around keys and string values, no markdown code fences, no
    # comments and no trailing commas.  See llm_client._extract_json_object for
    # additional parsing safeguards.
    prompt = (
        "You are creating outbound email campaign copy for a business.\n"
        "Return a single JSON object with exactly three keys: \"subject\", \"body\", and \"html_body\".\n"
        "Rules for the JSON response:\n"
        "- Use only double-quoted keys and double-quoted string values.\n"
        "- Do not output any markdown, code fences or comments.\n"
        "- Do not include trailing commas after the last key/value pair.\n"
        "- Return a complete object in one response; the last character must be }.\n"
        "- Do not invent discounts, deadlines, guarantees or other details not provided.\n"
        "- Do not include any additional keys.\n"
        "Constraints for content:\n"
        "- subject: 4–10 words, specific, non-spammy.\n"
        "- body: 3 short paragraphs, plain text, warm but concise, with one clear call to action.\n"
        "- html_body: clean HTML matching the body using simple <p> tags.\n"
        "- Mention only product details provided below.\n\n"
        f"Campaign goal: {campaign_goal.strip()}\n"
        f"Audience: {audience_description.strip()}\n"
        f"Tone: {tone.strip()}\n"
        f"Call to action: {call_to_action.strip()}\n"
        f"Offer details: {str(offer_details or '').strip() or 'None'}\n"
        f"Extra context: {str(extra_context or '').strip() or 'None'}\n\n"
        f"Product name: {product.get('name') or product.get('product_title') or 'Product'}\n"
        f"Product title/code: {product.get('product_title') or 'N/A'}\n"
        f"Product description: {product.get('description') or 'No description provided'}\n"
        f"Product price: {product.get('price') or 'N/A'} {product.get('price_currency') or ''}\n"
        f"Product category: {product.get('category') or 'general'}\n"
        f"Product type: {product.get('product_type') or 'general'}\n"
        f"Features:\n{feature_lines}\n"
        f"Images:\n{image_lines}\n"
    )

    try:
        tuned_engine = _campaign_ai_engine(engine, min_output_tokens=CAMPAIGN_COPY_MIN_OUTPUT_TOKENS)
        generated = CampaignCopyDraft.model_validate(
            await call_model_json(prompt, CampaignCopyDraft, engine=tuned_engine, call_purpose="email_campaign_copy")
        ).model_dump()
        if not generated.get("html_body"):
            generated["html_body"] = _body_to_html(generated.get("body", ""))
        generated["html_body"] = _finalize_email_html(generated.get("html_body", ""), generated.get("body", ""))
        return generated
    except Exception as exc:
        logger.exception(
            "Campaign copy AI generation failed function=generate_campaign_copy company_id=%s product_id=%s error=%s",
            company_id,
            product_id,
            exc,
        )
        fallback = await _campaign_static_fallback(db, company_id)
        return {
            "subject": "A quick update",
            "body": fallback,
            "html_body": _finalize_email_html("", fallback),
            "fallback_used": True,
            "error_type": "quota_exhausted" if _is_quota_exhausted_error(exc) else "provider_error",
        }


async def generate_html_email_body(
    db,
    *,
    company_id: str,
    description: str,
    product_id: str = "",
    current_body: str = "",
) -> dict:
    if not company_id:
        raise ValueError("company_id is required")

    description = str(description or "").strip()
    if not description:
        raise ValueError("Describe what the HTML email body should say.")

    async with company_context(db, company_id):
        product = await _load_campaign_product(db, company_id=company_id, product_id=product_id) if product_id else None
        engine = await get_active_llm_engine(db, company_id)
    product_context = "No product selected."
    if product:
        feature_lines = (
            "\n".join(f"- {feature}" for feature in product.get("features") or [])
            or "- No structured features stored"
        )
        product_context = (
            f"Product name: {product.get('name') or product.get('product_title') or 'Product'}\n"
            f"Product description: {product.get('description') or 'No description provided'}\n"
            f"Product price: {product.get('price') or 'N/A'} {product.get('price_currency') or ''}\n"
            f"Features:\n{feature_lines}"
        )

    prompt = (
        "Generate a production-ready HTML email body for a CRM campaign.\n"
        "Return JSON with key html_body only.\n"
        "Return a complete object in one response; the last character must be }.\n"
        "Constraints:\n"
        "- Use safe email HTML with simple headings, paragraphs, bullet lists, and links only if requested.\n"
        "- Do not include script, style, forms, tracking pixels, or external assets.\n"
        "- Keep it concise and readable.\n"
        "- Mention only facts supplied in the context.\n\n"
        f"User description/context:\n{description}\n\n"
        f"Current plain text/body draft:\n{str(current_body or '').strip() or 'None'}\n\n"
        f"Product context:\n{product_context}\n"
    )

    try:
        tuned_engine = _campaign_ai_engine(engine, min_output_tokens=HTML_BODY_MIN_OUTPUT_TOKENS)
        generated = HtmlEmailBodyDraft.model_validate(
            await call_model_json(prompt, HtmlEmailBodyDraft, engine=tuned_engine, call_purpose="email_html_body")
        ).model_dump()
        html_body = _finalize_email_html(generated.get("html_body", ""), current_body)
        if not html_body:
            raise CampaignAIUnavailableError(_campaign_ai_unavailable_message("html"))
        return {"html_body": html_body}
    except CampaignAIUnavailableError:
        raise
    except Exception as exc:
        logger.exception(
            "HTML email body AI generation failed function=generate_html_email_body "
            "company_id=%s product_id=%s error=%s",
            company_id,
            product_id,
            exc,
        )
        fallback = await _campaign_static_fallback(db, company_id)
        return {
            "html_body": _finalize_email_html("", fallback),
            "fallback_used": True,
            "error_type": "quota_exhausted" if _is_quota_exhausted_error(exc) else "provider_error",
        }


def _campaign_product_snapshot(product: dict | None) -> dict | None:
    if not product:
        return None
    return {
        "id": str(product.get("id") or "").strip(),
        "name": str(product.get("name") or product.get("product_title") or "").strip(),
        "product_title": str(product.get("product_title") or "").strip(),
        "description": str(product.get("description") or "").strip(),
        "price": str(product.get("price") or "").strip(),
        "price_currency": str(product.get("price_currency") or "").strip(),
        "category": str(product.get("category") or "").strip(),
        "product_type": str(product.get("product_type") or "").strip(),
        "features": list(product.get("features") or []),
        "images": list(product.get("images") or []),
    }


# --------------------------------------------------------------------------- #
# Campaign lifecycle
# --------------------------------------------------------------------------- #


async def bootstrap_email_campaign_schema(db) -> None:
    await runtime_schema_ready(
        db,
        "email_campaigns",
        required_columns=(
            ("email_campaigns", "updated_at"),
            ("email_campaigns", "attachments"),
            ("email_campaigns", "metadata"),
        ),
        raise_on_missing=True,
    )


async def create_campaign(
    db,
    *,
    company_id: str,
    name: str,
    subject: str,
    body: str,
    html_body: str = "",
    filters: dict | None = None,
    attachments: list | None = None,
    metadata: dict | None = None,
    created_by: str = "",
) -> dict:
    """Insert a draft campaign + its resolved recipients. Returns the campaign row."""
    if not (company_id and subject and (body or html_body)):
        raise ValueError("company_id, subject, and body (or html_body) are required")

    filters = dict(filters or {})
    final_html = _finalize_email_html(html_body, body)
    final_body = str(body or "").strip() or _html_to_text(final_html)
    metadata_payload = dict(metadata or {})
    product_id = str(filters.get("product_id") or "").strip()
    if product_id:
        product = await _load_campaign_product(db, company_id=company_id, product_id=product_id)
        if not product:
            raise ValueError("Selected product was not found")
        filters["product_snapshot"] = _campaign_product_snapshot(product)
    recipients = await resolve_recipients(db, company_id=company_id, filters=filters)

    campaign_id = make_id()
    import json as _json

    await db.execute(
        "INSERT INTO email_campaigns(id,company_id,name,subject,body,html_body,filters,attachments,metadata,status,"
        "total_recipients,sent_count,failed_count,created_by,created_at) "
        "VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9::jsonb,'draft',$10,0,0,$11,NOW())",
        campaign_id,
        company_id,
        (name or subject)[:240],
        subject[:500],
        final_body,
        final_html,
        _json.dumps(filters),
        _json.dumps(list(attachments or [])),
        _json.dumps(metadata_payload),
        len(recipients),
        (created_by or "")[:120],
    )

    if recipients:
        # Bulk insert in one statement.
        values_sql = []
        flat: list = []
        for idx, rec in enumerate(recipients):
            base = idx * 6
            values_sql.append(
                f"(${base + 1},${base + 2},${base + 3},${base + 4},${base + 5},${base + 6},'pending',NOW())"
            )
            flat.extend(
                [
                    make_id(),
                    campaign_id,
                    company_id,
                    rec.get("customer_id", ""),
                    rec.get("lead_id", ""),
                    rec["email"],
                ]
            )
        await db.execute(
            "INSERT INTO email_campaign_recipients("
            "id,campaign_id,company_id,customer_id,lead_id,email,status,created_at"
            ") "
            f"VALUES {','.join(values_sql)}",
            *flat,
        )
        # Populate name column in a second pass (keeps the bulk INSERT above
        # positional-arg friendly).
        for rec in recipients:
            if rec.get("name"):
                await db.execute(
                    "UPDATE email_campaign_recipients SET name=$1 WHERE campaign_id=$2 AND email=$3 AND name=''",
                    rec["name"],
                    campaign_id,
                    rec["email"],
                )

    row = await db.fetchrow("SELECT * FROM email_campaigns WHERE id=$1", campaign_id)
    return dict(row) if row else {"id": campaign_id}


async def update_campaign(
    db,
    *,
    campaign_id: str,
    company_id: str,
    name: str,
    subject: str,
    body: str,
    html_body: str = "",
    filters: dict | None = None,
    attachments: list | None = None,
    metadata: dict | None = None,
) -> dict:
    if not (campaign_id and company_id and subject and (body or html_body)):
        raise ValueError("campaign_id, company_id, subject, and body (or html_body) are required")

    existing = await db.fetchrow(
        "SELECT * FROM email_campaigns WHERE id=$1 AND company_id=$2 LIMIT 1",
        campaign_id,
        company_id,
    )
    if not existing:
        raise ValueError("Campaign not found")
    existing = dict(existing)
    if str(existing.get("status") or "").strip().lower() != "draft":
        raise ValueError("Only draft campaigns can be edited")

    filters = dict(filters or {})
    final_html = _finalize_email_html(html_body, body)
    final_body = str(body or "").strip() or _html_to_text(final_html)
    metadata_payload = dict(metadata or {})
    product_id = str(filters.get("product_id") or "").strip()
    if product_id:
        product = await _load_campaign_product(db, company_id=company_id, product_id=product_id)
        if not product:
            raise ValueError("Selected product was not found")
        filters["product_snapshot"] = _campaign_product_snapshot(product)
    recipients = await resolve_recipients(db, company_id=company_id, filters=filters)

    import json as _json

    await db.execute(
        "UPDATE email_campaigns SET name=$1,subject=$2,body=$3,html_body=$4,filters=$5::jsonb,"
        "attachments=$6::jsonb,metadata=$7::jsonb,total_recipients=$8,sent_count=0,failed_count=0,"
        "last_error='',updated_at=NOW() WHERE id=$9 AND company_id=$10",
        (name or subject)[:240],
        subject[:500],
        final_body,
        final_html,
        _json.dumps(filters),
        _json.dumps(list(attachments or [])),
        _json.dumps(metadata_payload),
        len(recipients),
        campaign_id,
        company_id,
    )
    await db.execute(
        "DELETE FROM email_campaign_recipients WHERE campaign_id=$1 AND company_id=$2",
        campaign_id,
        company_id,
    )

    if recipients:
        values_sql = []
        flat: list = []
        for idx, rec in enumerate(recipients):
            base = idx * 6
            values_sql.append(
                f"(${base + 1},${base + 2},${base + 3},${base + 4},${base + 5},${base + 6},'pending',NOW())"
            )
            flat.extend(
                [
                    make_id(),
                    campaign_id,
                    company_id,
                    rec.get("customer_id", ""),
                    rec.get("lead_id", ""),
                    rec["email"],
                ]
            )
        await db.execute(
            "INSERT INTO email_campaign_recipients("
            "id,campaign_id,company_id,customer_id,lead_id,email,status,created_at"
            ") "
            f"VALUES {','.join(values_sql)}",
            *flat,
        )
        for rec in recipients:
            if rec.get("name"):
                await db.execute(
                    "UPDATE email_campaign_recipients SET name=$1 WHERE campaign_id=$2 AND email=$3 AND name=''",
                    rec["name"],
                    campaign_id,
                    rec["email"],
                )

    row = await db.fetchrow("SELECT * FROM email_campaigns WHERE id=$1", campaign_id)
    return dict(row) if row else {"id": campaign_id}


async def _send_single(
    recipient: dict,
    *,
    db,
    company_id: str,
    subject: str,
    body: str,
    html_body: str,
) -> tuple[bool, str]:
    try:
        await send_tenant_email_async(
            db,
            company_id,
            to_email=recipient["email"],
            subject=subject,
            body=body,
            html_body=html_body,
        )
        return True, ""
    except Exception as exc:
        return False, str(exc)[:500]


async def _dispatch_campaign(
    db,
    campaign_id: str,
    company_id: str,
) -> None:
    """Run a campaign end-to-end. Safe to call as a detached task.

    ``db`` must be the shared database handle from ``app.state.db``.
    """
    concurrency = DEFAULT_BATCH_CONCURRENCY
    try:
        import os as _os

        concurrency = max(1, int(_os.environ.get("CAMPAIGN_SEND_CONCURRENCY", str(DEFAULT_BATCH_CONCURRENCY))))
    except Exception:
        pass

    if db is None:
        logger.error("Campaign dispatch: no DB available for campaign %s", campaign_id)
        return

    async with company_context(db, company_id):
        campaign = await db.fetchrow(
            "SELECT * FROM email_campaigns WHERE id=$1 AND company_id=$2",
            campaign_id,
            company_id,
        )
    if not campaign:
        logger.warning("Campaign dispatch: missing campaign %s", campaign_id)
        return
    campaign = dict(campaign)
    if campaign.get("status") in {"sending", "completed"}:
        return

    async with company_context(db, company_id):
        await db.execute(
            "UPDATE email_campaigns SET status='sending',started_at=NOW() WHERE id=$1",
            campaign_id,
        )

    subject = str(campaign.get("subject") or "")
    html_body = _finalize_email_html(str(campaign.get("html_body") or ""), str(campaign.get("body") or ""))
    body = str(campaign.get("body") or "").strip() or _html_to_text(html_body)

    async with company_context(db, company_id):
        pending = await db.fetch(
            "SELECT id,email,name FROM email_campaign_recipients "
            "WHERE campaign_id=$1 AND status='pending' ORDER BY created_at",
            campaign_id,
        )
    pending = [dict(p) for p in pending]
    if not pending:
        async with company_context(db, company_id):
            await db.execute(
                "UPDATE email_campaigns SET status='completed',completed_at=NOW() WHERE id=$1",
                campaign_id,
            )
        return

    sem = asyncio.Semaphore(concurrency)
    sent_total = 0
    failed_total = 0

    async def _process(rec: dict) -> None:
        nonlocal sent_total, failed_total
        async with sem:
            ok, err = await _send_single(
                rec,
                db=db,
                company_id=company_id,
                subject=subject,
                body=body,
                html_body=html_body,
            )
            try:
                async with company_context(db, company_id):
                    if ok:
                        await db.execute(
                            "UPDATE email_campaign_recipients SET status='sent',sent_at=NOW(),error='' WHERE id=$1",
                            rec["id"],
                        )
                        sent_total += 1
                    else:
                        await db.execute(
                            "UPDATE email_campaign_recipients SET status='failed',error=$2 WHERE id=$1",
                            rec["id"],
                            err,
                        )
                        failed_total += 1
            except Exception as track_exc:
                logger.warning(
                    "Campaign %s recipient %s status update failed: %s",
                    campaign_id,
                    rec.get("id"),
                    track_exc,
                )

    try:
        await asyncio.gather(*[_process(r) for r in pending])
    except Exception as exc:
        logger.exception("Campaign %s send loop failed: %s", campaign_id, exc)
        async with company_context(db, company_id):
            await db.execute(
                "UPDATE email_campaigns SET status='failed',last_error=$2,"
                "sent_count=$3,failed_count=$4,completed_at=NOW() WHERE id=$1",
                campaign_id,
                str(exc)[:500],
                sent_total,
                failed_total,
            )
        return

    final_status = "completed" if failed_total == 0 else ("completed" if sent_total > 0 else "failed")
    async with company_context(db, company_id):
        await db.execute(
            "UPDATE email_campaigns SET status=$2,sent_count=$3,failed_count=$4,completed_at=NOW() WHERE id=$1",
            campaign_id,
            final_status,
            sent_total,
            failed_total,
        )
    logger.info(
        "Campaign %s dispatched: sent=%d failed=%d",
        campaign_id,
        sent_total,
        failed_total,
    )


def schedule_campaign_send(app, campaign_id: str, company_id: str) -> None:
    """Enqueue a campaign dispatch as a detached asyncio task.

    We route through the shared safe detached-task wrapper, which keeps the
    existing background queue behavior and adds failure observability.
    """

    db = getattr(app.state, "db", None)
    create_safe_detached_task(
        db,
        _dispatch_campaign(db, campaign_id, company_id),
        name=f"email_campaign:{campaign_id}",
        job_id=f"campaign:{campaign_id}",
        company_id=company_id,
        channel="email_campaign",
        event_id=campaign_id,
        payload={"campaign_id": campaign_id},
    )
