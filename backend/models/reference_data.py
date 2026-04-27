"""models/reference_data.py — PostgreSQL bootstrap for reference tables."""

from core.utils import make_id, normalize_reference_key, now_ts
from typing import Optional

ROLES = [
    ("super_admin", "Platform-wide administrator", "platform", True),
    ("admin", "Tenant administrator", "company", False),
    ("company_agent", "Company agent", "company", False),
]
LEAD_STATUSES = [
    ("new", "Freshly captured lead", 1),
    ("contacted", "Initial contact", 2),
    ("qualified", "Qualified for sales", 3),
    ("proposal", "Proposal sent", 4),
    ("negotiation", "Negotiation in progress", 5),
    ("converted", "Converted to customer", 6),
    ("won", "Won (legacy, same as converted)", 7),
    ("lost", "Lost", 8),
]
SOURCES = [
    ("web_chat", "channel", "web_chat"),
    ("whatsapp", "channel", "whatsapp"),
    ("instagram", "channel", "instagram"),
    ("facebook", "channel", "facebook"),
    ("referral", "offline", "referral"),
    ("organic", "organic", "organic"),
]
CHANNELS = [
    ("WhatsApp Business", "messaging", "whatsapp"),
    ("Instagram", "social", "instagram"),
    ("Facebook Messenger", "social", "facebook"),
    ("Web Chat Widget", "web", "web_chat"),
]
TICKET_STATUSES = [
    ("open", "#10b981"),
    ("in_progress", "#3b82f6"),
    ("escalated", "#ef4444"),
    ("resolved", "#64748b"),
    ("closed", "#94a3b8"),
]


async def ensure_global_roles(db):
    for name, desc, scope, all_perms in ROLES:
        n = normalize_reference_key(name)
        await db.execute(
            "INSERT INTO roles(id,role_name,description,perm_scope,perm_all,created_at,updated_at)"
            " VALUES($1,$2,$3,$4,$5,NOW(),NOW()) ON CONFLICT(role_name) DO NOTHING",
            make_id(),
            n,
            desc,
            scope,
            all_perms,
        )


async def ensure_company_reference_data(db, company_id: str):
    if not company_id:
        return
    for name, desc, idx in LEAD_STATUSES:
        n = normalize_reference_key(name)
        await db.execute(
            "INSERT INTO lead_statuses(id,company_id,status_name,description,order_index,created_at,updated_at)"
            " VALUES($1,$2,$3,$4,$5,NOW(),NOW()) ON CONFLICT(company_id,status_name) DO NOTHING",
            make_id(),
            company_id,
            n,
            desc,
            idx,
        )
    for name, stype, platform in SOURCES:
        n = normalize_reference_key(name)
        await db.execute(
            "INSERT INTO sources(id,company_id,source_name,source_type,platform,created_at,updated_at)"
            " VALUES($1,$2,$3,$4,$5,NOW(),NOW()) ON CONFLICT(company_id,source_name) DO NOTHING",
            make_id(),
            company_id,
            n,
            stype,
            platform,
        )
    for cname, ctype, platform in CHANNELS:
        p = normalize_reference_key(platform)
        await db.execute(
            "INSERT INTO channels(id,company_id,channel_name,channel_type,platform,created_at,updated_at)"
            " VALUES($1,$2,$3,$4,$5,NOW(),NOW()) ON CONFLICT(company_id,platform) DO NOTHING",
            make_id(),
            company_id,
            cname,
            ctype,
            p,
        )
    for name, color in TICKET_STATUSES:
        n = normalize_reference_key(name)
        await db.execute(
            "INSERT INTO ticket_statuses(id,company_id,status_name,color_code,created_at,updated_at)"
            " VALUES($1,$2,$3,$4,NOW(),NOW()) ON CONFLICT(company_id,status_name) DO NOTHING",
            make_id(),
            company_id,
            n,
            color,
        )


async def resolve_role_id(db, role_name: str) -> str:
    n = normalize_reference_key(role_name)
    if not n:
        return ""
    await ensure_global_roles(db)
    row = await db.fetchrow("SELECT id FROM roles WHERE role_name=$1", n)
    return dict(row).get("id", "") if row else ""


async def resolve_company_reference_id(
    db,
    company_id: str,
    table: str,
    lookup_field: str,
    value: str,
    defaults: Optional[dict] = None,
) -> str:
    n = normalize_reference_key(value)
    if not company_id or not n:
        return ""
    await ensure_company_reference_data(db, company_id)
    row = await db.fetchrow(
        f"SELECT id FROM {table} WHERE company_id=$1 AND {lookup_field}=$2 LIMIT 1",
        company_id,
        n,
    )
    if row:
        return dict(row).get("id", "")
    new_id = make_id()
    cols = ["id", "company_id", lookup_field, "created_at", "updated_at"]
    vals = [new_id, company_id, n, now_ts(), now_ts()]
    if defaults:
        for k, v in defaults.items():
            cols.append(k)
            vals.append(v)
    ph = ",".join(f"${i + 1}" for i in range(len(cols)))
    await db.execute(
        f"INSERT INTO {table}({','.join(cols)}) VALUES({ph}) ON CONFLICT DO NOTHING",
        *vals,
    )
    row2 = await db.fetchrow(f"SELECT id FROM {table} WHERE company_id=$1 AND {lookup_field}=$2 LIMIT 1", company_id, n)
    return dict(row2).get("id", "") if row2 else new_id
