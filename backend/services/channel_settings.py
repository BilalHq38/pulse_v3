"""Shared channel settings helpers.

Keeps default channel rows and Meta webhook verify-token behavior consistent
across Settings, Webhook info, and any future channel-aware routes.
"""

from __future__ import annotations

import secrets

from core.utils import make_id, now_ts
from services.db_helpers import rs

CHANNEL_DEFAULT_ROWS = (
    ("whatsapp", False, "WhatsApp"),
    ("instagram", False, "Instagram"),
    ("facebook", False, "Facebook Messenger"),
    ("email", False, "Email"),
    ("web_chat", True, "Web Chat Widget"),
)
META_VERIFY_CHANNELS = ("whatsapp", "instagram", "facebook")
CHANNEL_DISPLAY_NAMES = {
    "whatsapp": "WhatsApp",
    "instagram": "Instagram",
    "facebook": "Facebook Messenger",
}


def build_set_clause(updates: dict) -> tuple[str, list]:
    values = [v for _, v in updates.items()]
    return ",".join(f"{k}=${i + 2}" for i, k in enumerate(updates)), values


async def ensure_default_channel_rows(db, company_id: str) -> None:
    company_id = (company_id or "").strip()
    if not company_id:
        return
    for channel, enabled, display_name in CHANNEL_DEFAULT_ROWS:
        await db.execute(
            "INSERT INTO channel_settings(id,company_id,channel,display_name,enabled,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,NOW(),NOW()) ON CONFLICT (company_id, channel) DO NOTHING",
            make_id(),
            company_id,
            channel,
            display_name,
            enabled,
        )


async def ensure_meta_verify_tokens(db, company_id: str) -> str:
    """Guarantee one stable verify token across all Meta-backed channels."""
    company_id = (company_id or "").strip()
    if not company_id:
        return ""

    await ensure_default_channel_rows(db, company_id)
    rows = rs(
        await db.fetch(
            "SELECT id,channel,display_name,verify_token FROM channel_settings "
            "WHERE company_id=$1 AND channel = ANY($2::text[])",
            company_id,
            list(META_VERIFY_CHANNELS),
        )
    )
    token = next(
        (
            str(row.get("verify_token") or "").strip()
            for row in rows
            if str(row.get("verify_token") or "").strip()
        ),
        "",
    )
    if not token:
        token = f"pe-{secrets.token_urlsafe(18)}"

    for row in rows:
        updates: dict[str, str] = {}
        channel_key = str(row.get("channel") or "").strip().lower()
        expected_name = CHANNEL_DISPLAY_NAMES.get(channel_key, "")
        if expected_name and str(row.get("display_name") or "").strip() != expected_name:
            updates["display_name"] = expected_name
        if not str(row.get("verify_token") or "").strip():
            updates["verify_token"] = token
        if updates:
            updates["updated_at"] = now_ts()
            set_parts, values = build_set_clause(updates)
            await db.execute(
                f"UPDATE channel_settings SET {set_parts} WHERE id=$1",
                row["id"],
                *values,
            )
    return token
