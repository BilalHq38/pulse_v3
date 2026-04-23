"""
email_campaign_service.service — recipient resolution + async batch sender.

Design goals:
- Reuse ``services.email_service.send_email_async`` (Brevo or SMTP).
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
import logging
from typing import Any

from core.utils import make_id
from services.email_service import send_email_async
from shared.database import create_detached_task

logger = logging.getLogger(__name__)

# Keep concurrency modest; Brevo/SMTP both rate-limit and we share the loop
# with the rest of the API. Override via CAMPAIGN_SEND_CONCURRENCY env if needed.
DEFAULT_BATCH_CONCURRENCY = 10
RECIPIENT_HARD_CAP = 50_000


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
    audience = str(filters.get("audience") or "both").lower()
    if audience not in {"customers", "leads", "both"}:
        audience = "both"

    rows: dict[str, dict] = {}

    # ---- customers side --------------------------------------------------- #
    if audience in {"customers", "both"}:
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


# --------------------------------------------------------------------------- #
# Campaign lifecycle
# --------------------------------------------------------------------------- #


async def create_campaign(
    db,
    *,
    company_id: str,
    name: str,
    subject: str,
    body: str,
    html_body: str = "",
    filters: dict | None = None,
    created_by: str = "",
) -> dict:
    """Insert a draft campaign + its resolved recipients. Returns the campaign row."""
    if not (company_id and subject and (body or html_body)):
        raise ValueError("company_id, subject, and body (or html_body) are required")

    filters = dict(filters or {})
    recipients = await resolve_recipients(db, company_id=company_id, filters=filters)

    campaign_id = make_id()
    import json as _json

    await db.execute(
        "INSERT INTO email_campaigns(id,company_id,name,subject,body,html_body,filters,status,"
        "total_recipients,sent_count,failed_count,created_by,created_at) "
        "VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,'draft',$8,0,0,$9,NOW())",
        campaign_id,
        company_id,
        (name or subject)[:240],
        subject[:500],
        body or "",
        html_body or "",
        _json.dumps(filters),
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
            "INSERT INTO email_campaign_recipients(id,campaign_id,company_id,customer_id,lead_id,email,status,created_at) "  # noqa: E501
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


async def _send_single(
    recipient: dict,
    *,
    subject: str,
    body: str,
    html_body: str,
) -> tuple[bool, str]:
    try:
        await send_email_async(
            to_email=recipient["email"],
            subject=subject,
            body=body,
            html_body=html_body,
        )
        return True, ""
    except Exception as exc:
        return False, str(exc)[:500]


async def _dispatch_campaign(
    db_factory,
    campaign_id: str,
    company_id: str,
) -> None:
    """Run a campaign end-to-end. Safe to call as a detached task.

    ``db_factory`` must be a zero-arg callable returning the app DB connection
    pool (typically ``lambda: app.state.db``) because the detached task clears
    the request-bound DB context.
    """
    concurrency = DEFAULT_BATCH_CONCURRENCY
    try:
        import os as _os

        concurrency = max(1, int(_os.environ.get("CAMPAIGN_SEND_CONCURRENCY", str(DEFAULT_BATCH_CONCURRENCY))))
    except Exception:
        pass

    db = db_factory()
    if db is None:
        logger.error("Campaign dispatch: no DB available for campaign %s", campaign_id)
        return

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

    await db.execute(
        "UPDATE email_campaigns SET status='sending',started_at=NOW() WHERE id=$1",
        campaign_id,
    )

    subject = str(campaign.get("subject") or "")
    body = str(campaign.get("body") or "")
    html_body = str(campaign.get("html_body") or "")

    pending = await db.fetch(
        "SELECT id,email,name FROM email_campaign_recipients "
        "WHERE campaign_id=$1 AND status='pending' ORDER BY created_at",
        campaign_id,
    )
    pending = [dict(p) for p in pending]
    if not pending:
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
                subject=subject,
                body=body,
                html_body=html_body,
            )
            try:
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

    We rely on ``create_detached_task`` (already used elsewhere in the backend)
    which also routes through the background queue when one is configured.
    """

    def _factory():
        return getattr(app.state, "db", None)

    create_detached_task(
        _dispatch_campaign(_factory, campaign_id, company_id),
        name=f"email_campaign:{campaign_id}",
        job_id=f"campaign:{campaign_id}",
    )
