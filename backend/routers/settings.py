"""routers/settings.py — PostgreSQL."""

import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from core.utils import make_id, now_ts, parse_dt
from services.db_helpers import get_company_id, get_current_user_flexible, r, rs

logger = logging.getLogger(__name__)
router = APIRouter()

# Default channel rows for every company (used to fill gaps for legacy tenants).
_CHANNEL_DEFAULT_ROWS = (
    ("whatsapp", False, "WhatsApp Business"),
    ("instagram", False, "Instagram"),
    ("facebook", False, "Facebook Messenger"),
    ("email", False, "Email"),
    ("web_chat", True, "Web Chat Widget"),
)


async def _ensure_default_channel_rows(db, company_id: str) -> None:
    """Insert any missing channel_settings rows for known channels (ON CONFLICT no-op)."""
    company_id = (company_id or "").strip()
    if not company_id:
        return
    for ch, enabled, display in _CHANNEL_DEFAULT_ROWS:
        await db.execute(
            "INSERT INTO channel_settings(id,company_id,channel,display_name,enabled,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,NOW(),NOW()) ON CONFLICT (company_id, channel) DO NOTHING",
            make_id(),
            company_id,
            ch,
            display,
            enabled,
        )


def _db(req: Request):
    return req.app.state.db


async def _ensure_company_settings_exists(db, company_id: str) -> Optional[str]:
    company_id = (company_id or "").strip()
    if not company_id:
        return None
    row = r(await db.fetchrow("SELECT id FROM company_settings WHERE company_id=$1 LIMIT 1", company_id))
    if row:
        return row["id"]
    settings_id = make_id()
    await db.execute(
        "INSERT INTO company_settings(id,company_id,ai_enabled,ai_confidence_threshold,auto_assign,active_llm_engine_id,created_at,updated_at) "  # noqa: E501
        "VALUES($1,$2,TRUE,0.70,TRUE,'',NOW(),NOW()) "
        "ON CONFLICT (company_id) DO NOTHING",
        settings_id,
        company_id,
    )
    row = r(await db.fetchrow("SELECT id FROM company_settings WHERE company_id=$1 LIMIT 1", company_id))
    return (row or {}).get("id")


async def _fetch_company_settings_profile(db, company_id: str) -> Optional[dict]:
    company_id = (company_id or "").strip()
    if not company_id:
        return None
    return r(
        await db.fetchrow(
            "SELECT cs.*, c.name AS company_name "
            "FROM companies c "
            "LEFT JOIN company_settings cs ON cs.company_id = c.id "
            "WHERE c.id=$1 LIMIT 1",
            company_id,
        )
    )


# ---------------------------------------------------------------------------
# Allowlists — only these columns may be written via dynamic SET clauses
# ---------------------------------------------------------------------------

ALLOWED_USER_FIELDS = {
    "name",
    "email",
    "phone",
    "avatar",
    "updated_at",
}

ALLOWED_COMPANY_SETTINGS_FIELDS = {
    "industry",
    "tagline",
    "description",
    "logo_url",
    "phone",
    "support_email",
    "website_address",
    "address_line1",
    "address_info",
    "city",
    "state",
    "country",
    "postal_code",
    "social_linkedin",
    "social_twitter",
    "social_facebook",
    "social_instagram",
    "timezone",
    "language",
    "locale_information",
    "date_format",
    "currency",
    "bh_start",
    "bh_end",
    "bh_days",
    "ai_enabled",
    "ai_confidence_threshold",
    "auto_assign",
    "active_llm_engine_id",
    "updated_at",
}

ALLOWED_CHANNEL_FIELDS = {
    "display_name",
    "enabled",
    "api_key",
    "api_secret",
    "phone_number",
    "phone_number_id",
    "page_id",
    "access_token",
    "webhook_url",
    "widget_color",
    "welcome_message",
    "verify_token",
    "imap_host",
    "imap_port",
    "imap_user",
    "imap_pass_enc",
    "smtp_host",
    "smtp_port",
    "smtp_user",
    "smtp_pass_enc",
    "email_address",
    "email_provider",
    "email_send_enabled",
    "email_receive_enabled",
    "updated_at",
}

# Columns that must be datetime objects when passed to asyncpg
TIMESTAMP_FIELDS = {"updated_at", "created_at", "trial_ends_at", "subscription_started_at"}


# ---------------------------------------------------------------------------
# Type coercion helpers
# ---------------------------------------------------------------------------


def _coerce_value(key: str, value):
    """Convert JSON-native types to Python types asyncpg can bind."""
    if key in TIMESTAMP_FIELDS:
        # parse_dt handles str → datetime, passthrough for datetime, None for invalid
        return parse_dt(value)
    if isinstance(value, str) and value.lower() in ("true", "false"):
        # Only coerce booleans in known bool contexts — leave other strings alone
        return value.lower() == "true"
    return value


def _safe_fields(body: dict, allowlist: set) -> dict:
    """Keep only allowed keys and coerce their values."""
    return {k: _coerce_value(k, v) for k, v in body.items() if k in allowlist}


def _build_set(fields: dict, start_index: int = 2) -> tuple[str, list]:
    """Return (SET clause string, ordered values list) for asyncpg."""
    columns = list(fields.keys())
    set_parts = ", ".join(f"{col}=${i + start_index}" for i, col in enumerate(columns))
    values = [fields[col] for col in columns]
    return set_parts, values


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------


class PersonalSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: Optional[str] = Field(default=None, max_length=255)
    email: Optional[str] = Field(default=None, max_length=255)
    avatar: Optional[str] = Field(default=None, max_length=2000000)
    mobile_number: Optional[str] = Field(default=None, max_length=64)
    website_address: Optional[str] = Field(default=None, max_length=500)
    address_info: Optional[str] = Field(default=None, max_length=500)
    locale_information: Optional[str] = Field(default=None, max_length=50)
    timezone: Optional[str] = Field(default=None, max_length=100)
    preferred_language: Optional[str] = Field(default=None, max_length=50)
    date_format: Optional[str] = Field(default=None, max_length=50)
    currency: Optional[str] = Field(default=None, max_length=10)


class SocialLinks(BaseModel):
    model_config = ConfigDict(extra="ignore")
    linkedin: str = Field(default="", max_length=500)
    twitter: str = Field(default="", max_length=500)
    facebook: str = Field(default="", max_length=500)
    instagram: str = Field(default="", max_length=500)


class BusinessHours(BaseModel):
    model_config = ConfigDict(extra="ignore")
    start: str = Field(default="09:00", max_length=10)
    end: str = Field(default="18:00", max_length=10)
    days: list[str] = Field(default_factory=lambda: ["Mon", "Tue", "Wed", "Thu", "Fri"])


class CompanySettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    company_name: Optional[str] = Field(default=None, max_length=255)
    industry: Optional[str] = Field(default=None, max_length=255)
    tagline: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)
    logo_url: Optional[str] = Field(default=None, max_length=5000)
    phone: Optional[str] = Field(default=None, max_length=64)
    support_email: Optional[str] = Field(default=None, max_length=255)
    website_address: Optional[str] = Field(default=None, max_length=500)
    address_line1: Optional[str] = Field(default=None, max_length=500)
    address_info: Optional[str] = Field(default=None, max_length=500)
    city: Optional[str] = Field(default=None, max_length=100)
    state: Optional[str] = Field(default=None, max_length=100)
    country: Optional[str] = Field(default=None, max_length=100)
    postal_code: Optional[str] = Field(default=None, max_length=30)
    locale_information: Optional[str] = Field(default=None, max_length=50)
    timezone: Optional[str] = Field(default=None, max_length=100)
    language: Optional[str] = Field(default=None, max_length=50)
    preferred_language: Optional[str] = Field(default=None, max_length=50)
    date_format: Optional[str] = Field(default=None, max_length=50)
    currency: Optional[str] = Field(default=None, max_length=10)
    ai_enabled: Optional[bool] = None
    ai_confidence_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    auto_assign: Optional[bool] = None
    active_llm_engine_id: Optional[str] = Field(default=None, max_length=255)
    social_links: Optional[SocialLinks] = None
    business_hours: Optional[BusinessHours] = None


class ChannelSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    display_name: Optional[str] = Field(default=None, max_length=100)
    enabled: Optional[bool] = None
    api_key: Optional[str] = Field(default=None, max_length=500)
    api_secret: Optional[str] = Field(default=None, max_length=500)
    phone_number: Optional[str] = Field(default=None, max_length=64)
    phone_number_id: Optional[str] = Field(default=None, max_length=255)
    page_id: Optional[str] = Field(default=None, max_length=255)
    access_token: Optional[str] = Field(default=None, max_length=1000)
    webhook_url: Optional[str] = Field(default=None, max_length=500)
    widget_color: Optional[str] = Field(default=None, max_length=20)
    welcome_message: Optional[str] = Field(default=None, max_length=1000)
    verify_token: Optional[str] = Field(default=None, max_length=255)
    imap_host: Optional[str] = Field(default=None, max_length=500)
    imap_port: Optional[int] = Field(default=None, ge=1, le=65535)
    imap_user: Optional[str] = Field(default=None, max_length=500)
    imap_pass_enc: Optional[str] = Field(default=None, max_length=2000)
    smtp_host: Optional[str] = Field(default=None, max_length=500)
    smtp_port: Optional[int] = Field(default=None, ge=1, le=65535)
    smtp_user: Optional[str] = Field(default=None, max_length=500)
    smtp_pass_enc: Optional[str] = Field(default=None, max_length=2000)
    email_address: Optional[str] = Field(default=None, max_length=500)
    email_provider: Optional[str] = Field(default=None, max_length=64)
    email_send_enabled: Optional[bool] = None
    email_receive_enabled: Optional[bool] = None


class TemplateCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1, max_length=10_000)
    category: str = Field(default="general", max_length=50)
    channel: str = Field(default="all", max_length=32)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/settings/personal")
async def get_personal_settings(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)

    user = r(await db.fetchrow("SELECT * FROM users WHERE id=$1 LIMIT 1", cu["sub"]))
    if not user:
        raise HTTPException(404, "User not found")

    # Always scope company_settings to current user's company
    if cid:
        cs = await _fetch_company_settings_profile(db, cid) or {}
    else:
        cs = {}

    return {
        "name": user.get("name", ""),
        "company_name": "Pulse Engine" if user.get("role") == "super_admin" else cs.get("company_name", ""),
        "email": user.get("email", ""),
        "mobile_number": user.get("phone", ""),
        "role": user.get("role", ""),
        "avatar": user.get("avatar", ""),
        "website_address": cs.get("website_address") or "",
        "address_info": cs.get("address_info") or "",
        "locale_information": cs.get("locale_information") or "en",
        "timezone": cs.get("timezone") or "UTC",
        "preferred_language": cs.get("language") or "en",
        "date_format": cs.get("date_format") or "YYYY-MM-DD",
        "currency": cs.get("currency") or "USD",
    }


@router.put("/settings/personal")
async def update_personal_settings(body: PersonalSettingsUpdate, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)

    user_upd: dict = {}
    cs_upd: dict = {}

    if body.name is not None:
        user_upd["name"] = body.name.strip()
    if body.avatar is not None:
        user_upd["avatar"] = body.avatar
    if body.mobile_number is not None:
        user_upd["phone"] = body.mobile_number.strip()
    if body.email is not None:
        new_email = body.email.strip().lower()
        if not new_email:
            raise HTTPException(400, "Email is required")
        if await db.fetchval(
            "SELECT id FROM users WHERE email=$1 AND company_id=$2 AND id!=$3",
            new_email,
            cid,
            cu["sub"],
        ):
            raise HTTPException(400, "Email already in use")
        user_upd["email"] = new_email

    # Company settings fields
    field_map = {
        "website_address": "website_address",
        "address_info": "address_info",
        "locale_information": "locale_information",
        "timezone": "timezone",
        "preferred_language": "language",
        "date_format": "date_format",
        "currency": "currency",
    }
    for body_field, db_col in field_map.items():
        value = getattr(body, body_field, None)
        if value is not None:
            cs_upd[db_col] = value.strip()

    if user_upd:
        user_upd["updated_at"] = now_ts()
        safe = _safe_fields(user_upd, ALLOWED_USER_FIELDS)
        if safe:
            set_parts, values = _build_set(safe)
            await db.execute(f"UPDATE users SET {set_parts} WHERE id=$1", cu["sub"], *values)

    if cs_upd:
        cs_upd["updated_at"] = now_ts()
        safe = _safe_fields(cs_upd, ALLOWED_COMPANY_SETTINGS_FIELDS)
        if safe and cid:
            settings_id = await _ensure_company_settings_exists(db, cid)
            if settings_id:
                set_parts, values = _build_set(safe)
                await db.execute(f"UPDATE company_settings SET {set_parts} WHERE id=$1", settings_id, *values)

    return await get_personal_settings(request)


@router.get("/settings/company")
async def get_company_settings(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu) or ""
    if not cid:
        raise HTTPException(400, "Company context is required")

    row = await _fetch_company_settings_profile(db, cid)
    if not row or not row.get("company_id"):
        await _ensure_company_settings_exists(db, cid)
        row = await _fetch_company_settings_profile(db, cid)
    if not row:
        raise HTTPException(404, "Company settings not found")

    if row:
        row["social_links"] = {
            "linkedin": row.pop("social_linkedin", "") or "",
            "twitter": row.pop("social_twitter", "") or "",
            "facebook": row.pop("social_facebook", "") or "",
            "instagram": row.pop("social_instagram", "") or "",
        }
        row["business_hours"] = {
            "start": row.pop("bh_start", "09:00") or "09:00",
            "end": row.pop("bh_end", "18:00") or "18:00",
            "days": (row.pop("bh_days", "Mon,Tue,Wed,Thu,Fri") or "Mon,Tue,Wed,Thu,Fri").split(","),
        }

    return row


@router.put("/settings/company")
async def update_company_settings(body: CompanySettingsUpdate, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu) or ""
    if not cid:
        raise HTTPException(400, "Company context is required")

    # Build flat dict from validated Pydantic model
    payload: dict = body.model_dump(exclude_none=True)
    company_name = (payload.pop("company_name", "") or "").strip()
    if company_name:
        await db.execute(
            "UPDATE companies SET name=$1,updated_at=NOW() WHERE id=$2",
            company_name,
            cid,
        )

    # Flatten nested objects into DB columns
    if "social_links" in payload:
        sl = payload.pop("social_links")
        payload["social_linkedin"] = sl.get("linkedin", "")
        payload["social_twitter"] = sl.get("twitter", "")
        payload["social_facebook"] = sl.get("facebook", "")
        payload["social_instagram"] = sl.get("instagram", "")

    if "business_hours" in payload:
        bh = payload.pop("business_hours")
        payload["bh_start"] = bh.get("start", "09:00")
        payload["bh_end"] = bh.get("end", "18:00")
        days = bh.get("days", [])
        payload["bh_days"] = ",".join(days) if isinstance(days, list) else str(days)

    # Map preferred_language → language column
    if "preferred_language" in payload:
        payload["language"] = payload.pop("preferred_language")

    # Apply allowlist + type coercion (guards against any Pydantic extra="ignore" leakage)
    safe = _safe_fields(payload, ALLOWED_COMPANY_SETTINGS_FIELDS)
    if not safe and not company_name:
        raise HTTPException(400, "No valid company settings fields provided")
    if safe:
        safe["updated_at"] = now_ts()
        settings_id = await _ensure_company_settings_exists(db, cid)
        if not settings_id:
            raise HTTPException(404, "Company settings not found")
        set_parts, values = _build_set(safe)
        await db.execute(f"UPDATE company_settings SET {set_parts} WHERE id=$1", settings_id, *values)

    return await get_company_settings(request)


@router.get("/settings/channels")
async def get_channel_settings(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")

    await _ensure_default_channel_rows(db, cid)
    rows = rs(await db.fetch("SELECT * FROM channel_settings WHERE company_id=$1", cid))

    normalized: list[dict] = []
    for row in rows:
        d = dict(row)
        ch = str(d.get("channel") or "").strip().lower()
        if ch == "twitter":
            continue
        d["channel"] = ch
        normalized.append(d)
    return normalized


@router.get("/settings/channels/whatsapp/bridge-qr")
async def get_whatsapp_bridge_qr(request: Request):
    """
    Proxy QR / session state from the Node whatsapp-web.js bridge.
    Uses WHATSAPP_BRIDGE_URL + WHATSAPP_BRIDGE_SECRET server-side only.
    """
    await get_current_user_flexible(request)
    bridge_url = os.environ.get("WHATSAPP_BRIDGE_URL", "http://localhost:3001").rstrip("/")
    secret = (os.environ.get("WHATSAPP_BRIDGE_SECRET") or os.environ.get("BRIDGE_SECRET") or "").strip()
    if not secret:
        return {
            "bridge_status": "not_configured",
            "qr_data_url": "",
            "qr_png_base64": "",
            "detail": "WhatsApp bridge secret is not configured (WHATSAPP_BRIDGE_SECRET).",
        }
    headers = {"X-Bridge-Secret": secret}
    timeout = httpx.Timeout(12.0, connect=3.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            session_resp = await client.get(f"{bridge_url}/session", headers=headers)
            qr_resp = await client.get(f"{bridge_url}/qr", headers=headers)
    except httpx.ConnectError as exc:
        logger.warning("WhatsApp bridge unreachable at %s: %s", bridge_url, exc)
        return {
            "bridge_status": "unreachable",
            "qr_data_url": "",
            "qr_png_base64": "",
            "detail": f"WhatsApp bridge is not reachable at {bridge_url}. Start the bridge (node bridge.js) or set WHATSAPP_BRIDGE_URL.",
        }
    except httpx.HTTPError as exc:
        logger.warning("WhatsApp bridge HTTP error: %s", exc)
        return {
            "bridge_status": "request_failed",
            "qr_data_url": "",
            "qr_png_base64": "",
            "detail": "WhatsApp bridge request failed.",
        }

    if session_resp.status_code >= 400:
        session_data = {}
    else:
        session_data = session_resp.json() if session_resp.content else {}
    qr_data = qr_resp.json() if qr_resp.content else {}
    if qr_resp.status_code >= 400:
        detail = qr_data.get("error") if isinstance(qr_data, dict) else qr_resp.text
        return {
            "bridge_status": str((session_data or {}).get("status") or "bridge_error"),
            "qr_data_url": "",
            "qr_png_base64": "",
            "detail": str(detail or "Bridge /qr error"),
        }

    bridge_status = ""
    if isinstance(session_data, dict):
        bridge_status = str(session_data.get("status") or "").strip()
    if isinstance(qr_data, dict) and not bridge_status:
        bridge_status = str(qr_data.get("bridge_status") or "").strip()
    out: dict = {"bridge_status": bridge_status, "qr_data_url": "", "qr_png_base64": ""}
    if isinstance(qr_data, dict):
        if qr_data.get("qr_data_url"):
            out["qr_data_url"] = str(qr_data["qr_data_url"])
        elif qr_data.get("qr_png_base64"):
            out["qr_png_base64"] = str(qr_data["qr_png_base64"])
    return out


@router.put("/settings/channels/{channel}")
async def update_channel_settings(channel: str, body: ChannelSettingsUpdate, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")

    channel_key = (channel or "").strip().lower()
    if not channel_key:
        raise HTTPException(400, "Channel is required")

    payload: dict = body.model_dump(exclude_none=True)
    payload["updated_at"] = now_ts()

    safe = _safe_fields(payload, ALLOWED_CHANNEL_FIELDS)
    if not safe:
        raise HTTPException(400, "No valid channel settings fields provided")

    row = r(
        await db.fetchrow(
            "SELECT id FROM channel_settings WHERE company_id=$2 AND lower(trim(channel))=$1 LIMIT 1",
            channel_key,
            cid,
        )
    )
    if row:
        set_parts, values = _build_set(safe)
        await db.execute(f"UPDATE channel_settings SET {set_parts} WHERE id=$1", row["id"], *values)
        await db.execute(
            "UPDATE channel_settings SET channel=$1 WHERE id=$2",
            channel_key,
            row["id"],
        )
        return r(await db.fetchrow("SELECT * FROM channel_settings WHERE id=$1", row["id"]))
    safe["id"] = make_id()
    safe["company_id"] = cid
    safe["channel"] = channel_key
    safe["created_at"] = now_ts()
    cols = ",".join(safe.keys())
    placeholders = ",".join(f"${i + 1}" for i in range(len(safe)))
    await db.execute(f"INSERT INTO channel_settings({cols}) VALUES({placeholders})", *safe.values())
    return r(await db.fetchrow("SELECT * FROM channel_settings WHERE id=$1", safe["id"]))


@router.get("/settings/templates")
async def list_templates(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)

    # Scope to company — previously returned templates from ALL companies
    if cid:
        return rs(await db.fetch("SELECT * FROM templates WHERE company_id=$1 ORDER BY created_at DESC LIMIT 200", cid))
    return rs(await db.fetch("SELECT * FROM templates ORDER BY created_at DESC LIMIT 200"))


@router.post("/settings/templates")
async def create_template(body: TemplateCreate, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu) or ""
    tmpl_id = make_id()

    await db.execute(
        "INSERT INTO templates(id,company_id,name,content,category,channel,created_by,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,NOW(),NOW())",
        tmpl_id,
        cid,
        body.name.strip(),
        body.content.strip(),
        body.category,
        body.channel,
        cu["sub"],
    )
    return r(await db.fetchrow("SELECT * FROM templates WHERE id=$1", tmpl_id))


@router.delete("/settings/templates/{template_id}")
async def delete_template(template_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu) or ""

    # Ownership check — previously any authenticated user could delete any template
    if cid:
        deleted = await db.fetchval(
            "DELETE FROM templates WHERE id=$1 AND company_id=$2 RETURNING id",
            template_id,
            cid,
        )
    else:
        deleted = await db.fetchval("DELETE FROM templates WHERE id=$1 RETURNING id", template_id)

    if not deleted:
        raise HTTPException(404, "Template not found")

    return {"status": "deleted", "template_id": template_id}
