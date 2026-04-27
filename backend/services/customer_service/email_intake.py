"""
Background IMAP polling: ingest tenant email into conversations via webhooks pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import os

from shared.database import Database

logger = logging.getLogger(__name__)


async def _email_intake_tick(db: Database) -> None:
    from channel_layer.adapters.email import EmailAdapter
    from routers.webhooks import _process_unified_incoming_message

    rows = await db.fetch(
        "SELECT DISTINCT company_id FROM channel_settings "
        "WHERE channel='email' AND enabled=TRUE "
        "AND COALESCE(email_receive_enabled, TRUE)=TRUE "
        "AND BTRIM(COALESCE(imap_host,'')) <> '' "
        "AND (BTRIM(COALESCE(imap_user,'')) <> '' OR BTRIM(COALESCE(email_address,'')) <> '') "
        "AND BTRIM(COALESCE(imap_pass_enc,'')) <> ''"
    )
    adapter = EmailAdapter()
    for row in rows:
        company_id = str(row["company_id"] or "").strip()
        if not company_id:
            continue
        try:
            messages = await adapter.poll_inbox(db, company_id, limit=25, mark_as_read=True)
        except Exception as exc:
            logger.warning("email poll failed company_id=%s: %s", company_id, exc)
            continue
        for unified in messages:
            try:
                await _process_unified_incoming_message(db, unified)
            except Exception as exc:
                logger.warning(
                    "email ingest failed company_id=%s message_id=%s: %s",
                    company_id,
                    getattr(unified, "message_id", ""),
                    exc,
                )


async def bootstrap_email_imap_intake(db: Database) -> None:
    """Start periodic IMAP intake (customer-service only)."""
    raw = os.environ.get("EMAIL_INTAKE_ENABLED", "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        logger.info("EMAIL_INTAKE_ENABLED is off; skipping IMAP intake loop")
        return

    try:
        interval = int(os.environ.get("EMAIL_IMAP_POLL_SECONDS", "60") or "60")
    except ValueError:
        interval = 60
    interval = max(15, interval)

    async def _loop() -> None:
        await asyncio.sleep(8)
        while True:
            try:
                await _email_intake_tick(db)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("email IMAP intake tick crashed")
            await asyncio.sleep(interval)

    asyncio.create_task(_loop(), name="email-imap-intake")
    logger.info("Started email IMAP intake loop (interval=%ss)", interval)
