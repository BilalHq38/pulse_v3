"""Audit and optionally repair corrupted channel identity fields.

Dry-run by default:
    python backend/scripts/audit_repair_channel_identities.py

Apply conservative fixes:
    python backend/scripts/audit_repair_channel_identities.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from channel_layer.channel_identity import normalize_email, normalize_whatsapp_phone  # noqa: E402


def _database_url() -> str:
    return (
        os.environ.get("DATABASE_URL")
        or os.environ.get("POSTGRES_DSN")
        or os.environ.get("POSTGRES_URL")
        or ""
    ).strip()


def _looks_like_provider_id(value: str) -> bool:
    raw = str(value or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if raw.startswith(("wamid.", "mid.", "m_")):
        return True
    if len(digits) > 15:
        return True
    identity = normalize_whatsapp_phone(raw)
    return bool(digits and not identity.is_valid)


async def _audit_customers(conn, apply: bool) -> list[dict]:
    rows = await conn.fetch(
        "SELECT id,company_id,name,phone,email FROM customers "
        "WHERE BTRIM(phone) <> '' OR BTRIM(email) <> '' "
        "ORDER BY updated_at DESC"
    )
    findings: list[dict] = []
    for row in rows:
        item = dict(row)
        phone = str(item.get("phone") or "").strip()
        email = str(item.get("email") or "").strip()
        invalid_phone = bool(phone and not normalize_whatsapp_phone(phone).is_valid)
        invalid_email = bool(email and not normalize_email(email).is_valid)
        if not invalid_phone and not invalid_email:
            continue
        finding = {
            "table": "customers",
            "id": item["id"],
            "company_id": item["company_id"],
            "phone": phone,
            "email": email,
            "actions": [],
        }
        if invalid_phone:
            finding["actions"].append("clear_customers.phone")
        if invalid_email:
            finding["actions"].append("clear_customers.email")
        findings.append(finding)
        if apply:
            await conn.execute(
                "UPDATE customers SET "
                "phone=CASE WHEN $1 THEN '' ELSE phone END,"
                "email=CASE WHEN $2 THEN '' ELSE email END,"
                "updated_at=NOW() "
                "WHERE id=$3 AND company_id=$4",
                invalid_phone,
                invalid_email,
                item["id"],
                item["company_id"],
            )
    return findings


async def _audit_conversations(conn, apply: bool) -> list[dict]:
    rows = await conn.fetch(
        "SELECT c.id,c.company_id,c.customer_id,c.channel,c.channel_id,c.session_id,cu.phone,cu.email "
        "FROM conversations c JOIN customers cu ON cu.id=c.customer_id AND cu.company_id=c.company_id "
        "WHERE c.channel IN ('whatsapp','facebook','instagram','email') "
        "ORDER BY c.updated_at DESC"
    )
    findings: list[dict] = []
    for row in rows:
        item = dict(row)
        channel = str(item.get("channel") or "").strip().lower()
        channel_id = str(item.get("channel_id") or "").strip()
        customer_phone = str(item.get("phone") or "").strip()
        customer_email = str(item.get("email") or "").strip()
        replacement = ""
        reason = ""
        if channel == "whatsapp":
            cid_identity = normalize_whatsapp_phone(channel_id)
            phone_identity = normalize_whatsapp_phone(customer_phone)
            if not cid_identity.is_valid and phone_identity.is_valid:
                replacement = phone_identity.canonical_value
                reason = "replace_whatsapp_channel_id_from_customer_phone"
            elif channel_id and not cid_identity.is_valid:
                replacement = ""
                reason = "clear_invalid_whatsapp_channel_id"
        elif channel == "email":
            cid_identity = normalize_email(channel_id)
            email_identity = normalize_email(customer_email)
            if not cid_identity.is_valid and email_identity.is_valid:
                replacement = email_identity.canonical_value
                reason = "replace_email_channel_id_from_customer_email"
        elif channel in {"facebook", "instagram"} and _looks_like_provider_id(customer_phone):
            reason = "social_customer_phone_looks_like_provider_id"
        if not reason:
            continue
        finding = {
            "table": "conversations",
            "id": item["id"],
            "company_id": item["company_id"],
            "channel": channel,
            "channel_id": channel_id,
            "customer_phone": customer_phone,
            "action": reason,
            "replacement": replacement,
        }
        findings.append(finding)
        if apply and reason.startswith(("replace_", "clear_")):
            await conn.execute(
                "UPDATE conversations SET channel_id=$1,updated_at=NOW() WHERE id=$2 AND company_id=$3",
                replacement,
                item["id"],
                item["company_id"],
            )
    return findings


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="apply conservative repairs")
    args = parser.parse_args()
    dsn = _database_url()
    if not dsn:
        print("DATABASE_URL, POSTGRES_DSN, or POSTGRES_URL is required", file=sys.stderr)
        return 2
    conn = await asyncpg.connect(dsn)
    try:
        customer_findings = await _audit_customers(conn, args.apply)
        conversation_findings = await _audit_conversations(conn, args.apply)
    finally:
        await conn.close()
    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "customer_findings": len(customer_findings),
            "conversation_findings": len(conversation_findings),
            "customers": customer_findings[:50],
            "conversations": conversation_findings[:50],
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
