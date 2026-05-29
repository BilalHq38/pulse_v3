"""routers/settings.py — PostgreSQL."""

import logging
import os
import secrets
from typing import Optional

import httpx
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.utils import make_id, now_ts, parse_dt
from services.db_helpers import get_company_id, get_current_user_flexible, r, rs
from services.media_storage import serve_stored_media, store_image_bytes
from services.meta_service import sync_channel_settings_meta_config

logger = logging.getLogger(__name__)
router = APIRouter()

# Default channel rows for every company (used to fill gaps for legacy tenants).
_CHANNEL_DEFAULT_ROWS = (
    ("whatsapp", False, "WhatsApp"),
    ("instagram", False, "Instagram"),
    ("facebook", False, "Facebook Messenger"),
    ("email", False, "Email"),
    ("web_chat", True, "Web Chat Widget"),
)
_META_VERIFY_CHANNELS = ("whatsapp", "instagram", "facebook")
_CHANNEL_DISPLAY_NAMES = {
    "whatsapp": "WhatsApp",
    "instagram": "Instagram",
    "facebook": "Facebook Messenger",
}


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


async def _ensure_meta_verify_tokens(db, company_id: str) -> str:
    """Guarantee a stable verify token across Meta-backed channels."""
    company_id = (company_id or "").strip()
    if not company_id:
        return ""

    await _ensure_default_channel_rows(db, company_id)
    rows = rs(
        await db.fetch(
            "SELECT id,channel,display_name,verify_token FROM channel_settings "
            "WHERE company_id=$1 AND channel = ANY($2::text[])",
            company_id,
            list(_META_VERIFY_CHANNELS),
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
        expected_name = _CHANNEL_DISPLAY_NAMES.get(channel_key, "")
        if expected_name and str(row.get("display_name") or "").strip() != expected_name:
            updates["display_name"] = expected_name
        if not str(row.get("verify_token") or "").strip():
            updates["verify_token"] = token
        if updates:
            updates["updated_at"] = now_ts()
            set_parts, values = _build_set(updates)
            await db.execute(
                f"UPDATE channel_settings SET {set_parts} WHERE id=$1",
                row["id"],
                *values,
            )
    return token


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
    "default_phone_region",
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
    "ai_static_fallback_message",
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
    tagline: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=5000)
    logo_url: Optional[str] = Field(default=None, max_length=5000)
    phone: Optional[str] = Field(default=None, max_length=64)
    default_phone_region: Optional[str] = Field(default=None, max_length=2)
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
    ai_static_fallback_message: Optional[str] = Field(default=None, max_length=1000)
    auto_assign: Optional[bool] = None
    active_llm_engine_id: Optional[str] = Field(default=None, max_length=255)
    social_links: Optional[SocialLinks] = None
    business_hours: Optional[BusinessHours] = None

    @field_validator("company_name", "industry", "timezone", "language", "preferred_language", mode="before")
    @classmethod
    def _blank_strings_to_none(cls, value):
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @field_validator("default_phone_region", mode="before")
    @classmethod
    def _normalize_default_phone_region(cls, value):
        if value is None:
            return None
        cleaned = str(value).strip().upper()
        if not cleaned:
            return None
        if len(cleaned) != 2 or not cleaned.isalpha():
            raise ValueError("Default phone region must be a 2-letter country code, for example US or PK.")
        return cleaned

    @field_validator("support_email", mode="before")
    @classmethod
    def _validate_support_email(cls, value):
        if value is None:
            return None
        cleaned = str(value).strip()
        if not cleaned:
            return None
        if "@" not in cleaned or "." not in cleaned.rsplit("@", 1)[-1]:
            raise ValueError("Support email must be a valid email address.")
        return cleaned

    @model_validator(mode="after")
    def _validate_required_profile_fields(self):
        missing: list[str] = []
        if self.company_name is not None and not self.company_name.strip():
            missing.append("Company name")
        if self.industry is not None and not self.industry.strip():
            missing.append("Industry")
        if self.timezone is not None and not self.timezone.strip():
            missing.append("Timezone")
        language = self.language if self.language is not None else self.preferred_language
        if language is not None and not str(language or "").strip():
            missing.append("Language")
        if missing:
            raise ValueError(f"Required company profile field missing: {', '.join(missing)}.")
        return self


class ChannelSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    display_name: Optional[str] = Field(default=None, max_length=100)
    enabled: Optional[bool] = None
    api_key: Optional[str] = Field(default=None, max_length=500)
    api_secret: Optional[str] = Field(default=None, max_length=500)
    phone_number: Optional[str] = Field(default=None, max_length=64)
    phone_number_id: Optional[str] = Field(default=None, max_length=255)
    page_id: Optional[str] = Field(default=None, max_length=255)
    access_token: Optional[str] = Field(default=None, max_length=4000)
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


@router.get("/settings")
async def get_settings_overview(request: Request):
    return {
        "personal": await get_personal_settings(request),
        "company": await get_company_settings(request),
        "channels": await get_channel_settings(request),
    }


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
    current_profile = await _fetch_company_settings_profile(db, cid) or {}
    effective_required = {
        "Company name": company_name or str(current_profile.get("company_name") or "").strip(),
        "Industry": str(payload.get("industry") or current_profile.get("industry") or "").strip(),
        "Timezone": str(payload.get("timezone") or current_profile.get("timezone") or "").strip(),
        "Language": str(
            payload.get("language")
            or payload.get("preferred_language")
            or current_profile.get("language")
            or ""
        ).strip(),
    }
    missing_required = [label for label, value in effective_required.items() if not value]
    if missing_required:
        logger.info(
            "company settings validation failed company_id=%s missing_fields=%s",
            cid,
            ",".join(missing_required),
        )
        raise HTTPException(
            400,
            {
                "message": f"Fill required company profile fields: {', '.join(missing_required)}.",
                "fields": missing_required,
            },
        )
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
    if "default_phone_region" in payload and payload["default_phone_region"]:
        payload["default_phone_region"] = str(payload["default_phone_region"]).upper()

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


@router.post("/settings/company/logo/upload")
async def upload_company_logo(request: Request, file: UploadFile = File(...)):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu) or ""
    if not cid:
        raise HTTPException(400, "Company context is required")
    raw = await file.read()
    upload = store_image_bytes(
        raw,
        mime_type=file.content_type or "",
        category="company-logos",
        company_id=cid,
        public_url_prefix="/api/settings/company/logo/media",
        original_filename=file.filename or "",
    )
    settings_id = await _ensure_company_settings_exists(db, cid)
    if not settings_id:
        raise HTTPException(404, "Company settings not found")
    await db.execute(
        "UPDATE company_settings SET logo_url=$1,updated_at=NOW() WHERE id=$2",
        upload["url"],
        settings_id,
    )
    return {"url": upload["url"], "company": await get_company_settings(request)}


@router.get("/settings/company/logo/media/{company_id}/{filename}")
async def get_company_logo_media(company_id: str, filename: str):
    return serve_stored_media(category="company-logos", company_id=company_id, filename=filename)


@router.get("/settings/channels")
async def get_channel_settings(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")

    await _ensure_default_channel_rows(db, cid)
    meta_verify_token = await _ensure_meta_verify_tokens(db, cid)
    rows = rs(await db.fetch("SELECT * FROM channel_settings WHERE company_id=$1", cid))

    normalized: list[dict] = []
    for row in rows:
        d = dict(row)
        ch = str(d.get("channel") or "").strip().lower()
        if ch == "twitter":
            continue
        d["channel"] = ch
        if ch in _CHANNEL_DISPLAY_NAMES:
            d["display_name"] = _CHANNEL_DISPLAY_NAMES[ch]
        if ch in _META_VERIFY_CHANNELS and not str(d.get("verify_token") or "").strip():
            d["verify_token"] = meta_verify_token
        normalized.append(d)
    return normalized


@router.get("/settings/channels/whatsapp/bridge-qr")
async def get_whatsapp_bridge_qr(request: Request):
    """
    Proxy QR / session state from the Node whatsapp-web.js bridge.
    Uses WHATSAPP_BRIDGE_URL + WHATSAPP_BRIDGE_SECRET server-side only.
    """
    cu = await get_current_user_flexible(request)
    bridge_url = os.environ.get("WHATSAPP_BRIDGE_URL", "http://localhost:3001").rstrip("/")
    secret = (os.environ.get("WHATSAPP_BRIDGE_SECRET") or os.environ.get("BRIDGE_SECRET") or "").strip()
    company_id = (get_company_id(cu) or "").strip()
    user_id = str(cu.get("sub") or "").strip()
    if not secret:
        return {
            "bridge_status": "not_configured",
            "qr_data_url": "",
            "qr_png_base64": "",
            "detail": "WhatsApp bridge secret is not configured (WHATSAPP_BRIDGE_SECRET).",
        }
    headers = {
        "X-Bridge-Secret": secret,
        "X-Bridge-Company-Id": company_id,
        "X-Bridge-User-Id": user_id,
    }
    logger.info(
        "whatsapp.qr.status_requested company_id=%s user_id=%s bridge_url=%s",
        company_id,
        user_id,
        bridge_url,
    )
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

    session_state = ""
    if isinstance(session_data, dict):
        session_state = str(session_data.get("state") or session_data.get("status") or "").strip()
    qr_state = ""
    qr_has_image = False
    if isinstance(qr_data, dict):
        qr_state = str(qr_data.get("state") or qr_data.get("bridge_status") or qr_data.get("status") or "").strip()
        qr_has_image = bool(qr_data.get("qr_data_url") or qr_data.get("qr_png_base64"))
    bridge_status = qr_state if (qr_has_image or qr_state) else session_state
    legacy_progress = {
        "idle": 0,
        "qr_required": 20,
        "need_qr": 20,
        "qr_scanned": 45,
        "authenticated": 65,
        "initializing": 80,
        "ready": 100,
        "reconnecting": 55,
        "failed": 0,
        "disconnected": 0,
    }
    status_source = qr_data if isinstance(qr_data, dict) and qr_data else session_data if isinstance(session_data, dict) else {}
    logger.info(
        "whatsapp.qr.proxy_response company_id=%s user_id=%s scope=%s state=%s qr_present=%s qr_data_url_present=%s",
        company_id,
        user_id,
        str(status_source.get("scope") or ""),
        bridge_status,
        bool(status_source.get("qr_present") or status_source.get("qr")),
        bool(isinstance(qr_data, dict) and qr_data.get("qr_data_url")),
    )
    out: dict = {
        "bridge_status": bridge_status,
        "status": bridge_status,
        "state": bridge_status,
        "connected": bool(status_source.get("connected") or bridge_status == "ready"),
        "progress": int(status_source.get("progress") or legacy_progress.get(bridge_status, 0)),
        "message": str(status_source.get("message") or ""),
        "phone": str(status_source.get("phone") or ""),
        "qr": str(status_source.get("qr") or ""),
        "retrying": bool(status_source.get("retrying")),
        "last_error": str(status_source.get("last_error") or ""),
        "detail": str(status_source.get("detail") or ""),
        "updated_at": str(status_source.get("updated_at") or ""),
        "scope": str(status_source.get("scope") or ""),
        "company_id": str(status_source.get("company_id") or company_id),
        "user_id": str(status_source.get("user_id") or user_id),
        "qr_data_url": "",
        "qr_png_base64": "",
    }
    if isinstance(qr_data, dict):
        qr_data_url = str(qr_data.get("qr_data_url") or "")
        if qr_data_url.startswith("data:image/"):
            out["qr_data_url"] = qr_data_url
        elif qr_data.get("qr_png_base64"):
            out["qr_png_base64"] = str(qr_data["qr_png_base64"])
    logger.info(
        "whatsapp.qr.frontend_payload_ready company_id=%s user_id=%s scope=%s state=%s qr_present=%s qr_data_url_present=%s",
        company_id,
        user_id,
        out.get("scope", ""),
        out.get("state", ""),
        bool(out.get("qr") or out.get("qr_data_url") or out.get("qr_png_base64")),
        bool(out.get("qr_data_url")),
    )
    return out


@router.post("/settings/channels/whatsapp/bridge-disconnect")
async def disconnect_whatsapp_bridge(request: Request):
    """Disconnect the linked WhatsApp Web session through the bridge."""
    cu = await get_current_user_flexible(request)
    bridge_url = os.environ.get("WHATSAPP_BRIDGE_URL", "http://localhost:3001").rstrip("/")
    secret = (os.environ.get("WHATSAPP_BRIDGE_SECRET") or os.environ.get("BRIDGE_SECRET") or "").strip()
    if not secret:
        raise HTTPException(400, "WhatsApp bridge secret is not configured.")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=3.0)) as client:
            resp = await client.post(
                f"{bridge_url}/disconnect",
                headers={
                    "X-Bridge-Secret": secret,
                    "X-Bridge-Company-Id": (get_company_id(cu) or "").strip(),
                    "X-Bridge-User-Id": str(cu.get("sub") or "").strip(),
                },
            )
    except httpx.ConnectError as exc:
        logger.warning("WhatsApp bridge disconnect unreachable at %s: %s", bridge_url, exc)
        raise HTTPException(503, "WhatsApp bridge is not reachable.") from exc
    except httpx.HTTPError as exc:
        logger.warning("WhatsApp bridge disconnect HTTP error: %s", exc)
        raise HTTPException(502, "WhatsApp bridge disconnect failed.") from exc
    data = resp.json() if resp.content else {}
    if resp.status_code >= 400:
        detail = data.get("error") if isinstance(data, dict) else resp.text
        raise HTTPException(resp.status_code, str(detail or "WhatsApp bridge disconnect failed."))
    return {"bridge_status": str(data.get("status") or "disconnected"), "success": True}


@router.put("/settings/channels/{channel}")
async def update_channel_settings(channel: str, body: ChannelSettingsUpdate, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")

    channel_key = (channel or "").strip().lower()
    if not channel_key:
        raise HTTPException(400, "Channel is required")

    payload: dict = body.model_dump(exclude_none=True)
    if channel_key in _META_VERIFY_CHANNELS:
        payload["display_name"] = _CHANNEL_DISPLAY_NAMES.get(
            channel_key,
            str(payload.get("display_name") or "").strip(),
        )
        verify_token = str(payload.get("verify_token") or "").strip()
        if not verify_token:
            payload["verify_token"] = await _ensure_meta_verify_tokens(db, cid)
    payload["updated_at"] = now_ts()

    # Auto-enable channel when sufficient credentials are supplied.
    if "enabled" not in payload:
        _api_key = str(payload.get("api_key") or "").strip()
        _email_address = str(payload.get("email_address") or "").strip()
        _smtp_host = str(payload.get("smtp_host") or "").strip()
        _access_token = str(payload.get("access_token") or "").strip()
        _page_id = str(payload.get("page_id") or "").strip()
        _phone_number_id = str(payload.get("phone_number_id") or "").strip()
        _email_provider = str(payload.get("email_provider") or "").strip().lower()

        if channel_key == "email":
            if _email_provider == "brevo" and _api_key and _email_address:
                payload["enabled"] = True
            elif _smtp_host and _email_address:
                payload["enabled"] = True
        elif channel_key in ("facebook", "instagram"):
            if _access_token and _page_id:
                payload["enabled"] = True
        elif channel_key == "whatsapp":
            if _access_token and _phone_number_id:
                payload["enabled"] = True

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
        saved = r(await db.fetchrow("SELECT * FROM channel_settings WHERE id=$1", row["id"]))
        if channel_key in _META_VERIFY_CHANNELS:
            await sync_channel_settings_meta_config(db, cid, saved)
        return saved
    safe["id"] = make_id()
    safe["company_id"] = cid
    safe["channel"] = channel_key
    safe["created_at"] = now_ts()
    cols = ",".join(safe.keys())
    placeholders = ",".join(f"${i + 1}" for i in range(len(safe)))
    await db.execute(f"INSERT INTO channel_settings({cols}) VALUES({placeholders})", *safe.values())
    saved = r(await db.fetchrow("SELECT * FROM channel_settings WHERE id=$1", safe["id"]))
    if channel_key in _META_VERIFY_CHANNELS:
        await sync_channel_settings_meta_config(db, cid, saved)
    return saved


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
