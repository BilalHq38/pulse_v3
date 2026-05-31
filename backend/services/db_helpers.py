"""services/db_helpers.py — Complete PostgreSQL rewrite. Zero MongoDB. Zero JSON files."""

from __future__ import annotations
import asyncio
import base64
import hashlib
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import BackgroundTasks, HTTPException, Request
from shared.auth.jwt import (
    REFRESH_TOKEN_EXPIRE_DAYS,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    is_valid_company_id,
)
from core.config import (
    EMAIL_VERIFICATION_TTL_MINUTES,
    OAUTH_SESSION_TTL_DAYS,
    ONBOARDING_REMINDER_DELAY_MINUTES,
    VERIFICATION_RESEND_COOLDOWN_SECONDS,
    VERIFICATION_RESEND_LOCK_MINUTES,
    VERIFICATION_RESEND_MAX_ATTEMPTS,
)
from core.request_helpers import get_client_ip, resolve_frontend_base_url
from core.phone_normalization import (
    normalize_to_e164_digits,
    phone_lookup_candidates,
)
from channel_layer.channel_identity import normalize_whatsapp_phone
from core.utils import make_id, parse_dt
from models.reference_data import ensure_company_reference_data, resolve_role_id
from services.email_service import render_platform_email_html, send_email_async
from services.ai_service.model_catalog import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_PROVIDER_KEYS,
    capability_labels,
    model_capabilities as catalog_model_capabilities,
    model_supports as catalog_model_supports,
    supported_model_names,
)
from services.media_storage import store_media_data_url
from shared.database import company_context
from shared.config import is_production
from shared.auth.dependencies import forbidden_exception, resolve_request_user, unauthorized_exception

logger = logging.getLogger(__name__)
_auth_security_ready = False
_auth_security_lock = asyncio.Lock()
_embedding_vector_ready = False
_embedding_vector_lock = asyncio.Lock()
_conversation_ai_pause_schema_ready = False
_conversation_ai_pause_schema_lock = asyncio.Lock()
_message_attachment_schema_ready = False
_message_attachment_schema_lock = asyncio.Lock()
_message_reaction_schema_ready: set[str] = set()
_message_reaction_schema_lock = asyncio.Lock()
_SUPER_ADMIN_COMPANY_ID_FALLBACK = "00000000-0000-0000-0000-000000000001"
AI_STATIC_FALLBACK_DEFAULT = (
    os.environ.get("AI_STATIC_FALLBACK_MESSAGE", "").strip()
    or "Thanks for your message. A team member will respond shortly."
)
DEPLOYMENT_SCHEMA_MIGRATION = "backend/sql_migrations/011_deployment_runtime_schema_hardening.sql"
ACCOUNT_PENDING_APPROVAL_MESSAGE = (
    "Your account is pending admin approval. You will get access to Pulse Engine after Super Admin verification."
)
ACCOUNT_REJECTED_MESSAGE = (
    "Your account request was not approved. Please contact the administrator for more information."
)
ACCOUNT_BLOCKED_MESSAGE = "Your account has been blocked. Please contact your administrator to restore access."
ACCOUNT_PAUSED_MESSAGE = "Your account has been paused. Please contact your administrator."
ACCOUNT_INACTIVE_MESSAGE = "Your account is inactive. Please contact your administrator."
ACCOUNT_RESTRICTED_STATUSES = {"pending_approval", "rejected", "blocked", "paused", "inactive"}
ACCOUNT_LOGIN_BLOCKED_STATUSES = {"blocked", "paused", "inactive", "pending_approval", "rejected"}
ACCOUNT_REFRESH_BLOCKED_STATUSES = {"blocked", "paused", "inactive", "pending_approval", "rejected"}


def normalize_account_status(status: str | None) -> str:
    return str(status or "active").strip().lower() or "active"


def account_status_code(status: str | None) -> str:
    normalized = normalize_account_status(status)
    return {
        "pending_approval": "ACCOUNT_PENDING_APPROVAL",
        "rejected": "ACCOUNT_REJECTED",
        "blocked": "ACCOUNT_BLOCKED",
        "paused": "ACCOUNT_PAUSED",
        "inactive": "ACCOUNT_INACTIVE",
    }.get(normalized, "")


def account_status_message(status: str | None) -> str:
    normalized = normalize_account_status(status)
    return {
        "pending_approval": ACCOUNT_PENDING_APPROVAL_MESSAGE,
        "rejected": ACCOUNT_REJECTED_MESSAGE,
        "blocked": ACCOUNT_BLOCKED_MESSAGE,
        "paused": ACCOUNT_PAUSED_MESSAGE,
        "inactive": ACCOUNT_INACTIVE_MESSAGE,
    }.get(normalized, "")


def account_status_error_detail(status: str | None) -> dict:
    normalized = normalize_account_status(status)
    return {
        "code": account_status_code(normalized) or "ACCOUNT_RESTRICTED",
        "status": normalized,
        "message": account_status_message(normalized) or "Your account cannot access this resource.",
    }


async def runtime_schema_ready(
    db,
    area: str,
    *,
    required_relations: tuple[str, ...] = (),
    required_columns: tuple[tuple[str, str], ...] = (),
    required_indexes: tuple[str, ...] = (),
    raise_on_missing: bool = False,
) -> bool:
    missing: list[str] = []
    try:
        for relation in required_relations:
            exists = bool(await db.fetchval("SELECT to_regclass($1) IS NOT NULL", relation))
            if not exists:
                missing.append(f"relation:{relation}")
        for table, column in required_columns:
            exists = bool(
                await db.fetchval(
                    "SELECT EXISTS("
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=$1 AND column_name=$2"
                    ")",
                    table,
                    column,
                )
            )
            if not exists:
                missing.append(f"column:{table}.{column}")
        for index in required_indexes:
            exists = bool(await db.fetchval("SELECT to_regclass($1) IS NOT NULL", index))
            if not exists:
                missing.append(f"index:{index}")
    except Exception as exc:
        logger.warning("runtime_schema_check_failed area=%s migration=%s error=%s", area, DEPLOYMENT_SCHEMA_MIGRATION, exc)
        if raise_on_missing:
            raise RuntimeError(f"Database schema check failed for {area}. Run {DEPLOYMENT_SCHEMA_MIGRATION}.") from exc
        return False

    if missing:
        logger.error(
            "runtime_schema_migration_required area=%s migration=%s missing=%s",
            area,
            DEPLOYMENT_SCHEMA_MIGRATION,
            ",".join(missing),
        )
        if raise_on_missing:
            raise RuntimeError(f"Database schema is missing required objects for {area}. Run {DEPLOYMENT_SCHEMA_MIGRATION}.")
        return False
    return True


def _normalize_lookup_email(value: str | None) -> str:
    email = str(value or "").strip().lower()
    if not email:
        return ""
    if "@" not in email:
        return email
    local, _, domain = email.partition("@")
    local = local.split("+")[0]
    if domain in {"gmail.com", "googlemail.com"}:
        local = local.replace(".", "")
    return f"{local}@{domain}" if local else email


async def _company_default_phone_region(db, company_id: str) -> str:
    scoped_company_id = str(company_id or "").strip()
    if not db or not scoped_company_id:
        return ""
    try:
        row = await db.fetchrow(
            "SELECT default_phone_region FROM company_settings WHERE company_id=$1 LIMIT 1",
            scoped_company_id,
        )
    except Exception as exc:
        logger.debug("Failed to load company phone region company_id=%s: %s", scoped_company_id, exc)
        return ""
    if not row:
        return ""
    region = str(dict(row).get("default_phone_region") or "").strip().upper()
    if len(region) == 2 and region.isalpha():
        return region
    return ""


async def normalize_customer_contact_phone(db, company_id: str, raw_phone: str) -> str:
    raw = str(raw_phone or "").strip()
    if not raw:
        return ""
    fallback_region = await _company_default_phone_region(db, company_id)
    identity = normalize_whatsapp_phone(raw, default_region=fallback_region)
    if identity.is_valid:
        return identity.canonical_value
    logger.warning(
        "Rejected invalid customer phone before write company_id=%s raw_phone=%s reason=%s",
        company_id,
        raw,
        identity.reason,
    )
    return ""


def _unique_ordered(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _should_replace_customer_phone(existing_phone: str, candidate_phone: str) -> bool:
    current = re.sub(r"\D", "", str(existing_phone or "").strip())
    incoming = re.sub(r"\D", "", str(candidate_phone or "").strip())
    if not incoming:
        return False
    if not current:
        return True
    if current == incoming:
        return str(existing_phone or "").strip() != str(candidate_phone or "").strip()
    if len(current) < len(incoming) and incoming.endswith(current):
        return True
    return False


async def upsert_customer_social_profile(db, customer_id: str, platform: str, profile_id: str) -> None:
    normalized_platform = str(platform or "").strip().lower()
    normalized_profile_id = str(profile_id or "").strip()
    if normalized_platform not in {"facebook", "instagram"} or not normalized_profile_id:
        return
    await db.execute(
        "INSERT INTO customer_social_profiles(customer_id,platform,profile_id) VALUES($1,$2,$3) "
        "ON CONFLICT (customer_id,platform) DO UPDATE SET profile_id=EXCLUDED.profile_id",
        customer_id,
        normalized_platform,
        normalized_profile_id,
    )


async def resolve_customer_by_contact(
    db,
    company_id: str,
    *,
    phone: str = "",
    email: str = "",
    channel: str = "",
    channel_profile_id: str = "",
    explicit_customer_id: str = "",
) -> Optional[dict]:
    scoped_company_id = str(company_id or "").strip()
    if not scoped_company_id:
        return None

    customer_id = str(explicit_customer_id or "").strip()
    if customer_id:
        row = await db.fetchrow(
            "SELECT * FROM customers WHERE id=$1 AND company_id=$2 LIMIT 1",
            customer_id,
            scoped_company_id,
        )
        if row:
            return dict(row)

    normalized_channel = str(channel or "").strip().lower()
    normalized_channel_profile_id = str(channel_profile_id or "").strip()
    if normalized_channel in {"facebook", "instagram"} and normalized_channel_profile_id:
        row = await db.fetchrow(
            "SELECT c.* FROM customers c "
            "JOIN customer_social_profiles csp ON csp.customer_id=c.id "
            "WHERE c.company_id=$1 AND csp.platform=$2 AND csp.profile_id=$3 "
            "ORDER BY c.updated_at DESC LIMIT 1",
            scoped_company_id,
            normalized_channel,
            normalized_channel_profile_id,
        )
        if row:
            return dict(row)

    normalized_email = _normalize_lookup_email(email)
    for email_candidate in _unique_ordered([str(email or "").strip().lower(), normalized_email]):
        row = await db.fetchrow(
            "SELECT * FROM customers WHERE company_id=$1 AND LOWER(email)=$2 "
            "ORDER BY updated_at DESC LIMIT 1",
            scoped_company_id,
            email_candidate,
        )
        if row:
            return dict(row)

    raw_phone = str(phone or "").strip()
    if not raw_phone:
        return None

    fallback_region = await _company_default_phone_region(db, scoped_company_id)
    if normalized_channel == "whatsapp":
        phone_identity = normalize_whatsapp_phone(raw_phone, default_region=fallback_region)
        if not phone_identity.is_valid:
            logger.warning(
                "Skipping invalid WhatsApp phone during customer lookup company_id=%s raw_phone=%s reason=%s",
                scoped_company_id,
                raw_phone,
                phone_identity.reason,
            )
            return None
    phone_candidates = phone_lookup_candidates(raw_phone, fallback_region=fallback_region)
    normalized_phone = normalize_to_e164_digits(raw_phone, fallback_region=fallback_region)
    phone_candidates = _unique_ordered(phone_candidates + [normalized_phone])
    for phone_candidate in phone_candidates:
        row = await db.fetchrow(
            "SELECT * FROM customers WHERE company_id=$1 AND phone=$2 "
            "ORDER BY updated_at DESC LIMIT 1",
            scoped_company_id,
            phone_candidate,
        )
        if row:
            return dict(row)

    digit_candidates = _unique_ordered([re.sub(r"\D", "", candidate) for candidate in phone_candidates])
    for digits in digit_candidates:
        row = await db.fetchrow(
            "SELECT * FROM customers WHERE company_id=$1 "
            "AND regexp_replace(phone, '\\D', '', 'g')=$2 "
            "ORDER BY updated_at DESC LIMIT 1",
            scoped_company_id,
            digits,
        )
        if row:
            return dict(row)

    last10 = normalized_phone[-10:] if len(normalized_phone) >= 10 else re.sub(r"\D", "", raw_phone)[-10:]
    if last10:
        rows = await db.fetch(
            "SELECT * FROM customers WHERE company_id=$1 "
            "AND regexp_replace(phone, '\\D', '', 'g') LIKE $2 "
            "ORDER BY updated_at DESC LIMIT 2",
            scoped_company_id,
            f"%{last10}",
        )
        if len(rows or []) == 1:
            return dict(rows[0])
    return None


def build_user_payload(user: dict) -> dict:
    company_id = str(user.get("company_id") or "").strip()
    if not company_id or not is_valid_company_id(company_id):
        raise HTTPException(500, "User is missing a valid tenant assignment")
    bs = str(user.get("billing_status") or "unpaid").strip().lower() or "unpaid"
    if bs not in ("trial", "active", "unpaid"):
        bs = "unpaid"
    plan_code = str(user.get("subscription_plan_code") or "free").strip().lower() or "free"
    company_name = str(user.get("company_name") or "").strip()
    subscription_status = str(user.get("subscription_status") or "inactive").strip().lower() or "inactive"
    company_billing_status = str(user.get("company_billing_status") or "inactive").strip().lower() or "inactive"
    stripe_subscription_id = str(user.get("stripe_subscription_id") or "").strip()
    oauth_providers = user.get("oauth_providers")
    if not isinstance(oauth_providers, list):
        oauth_providers = []
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user.get("name", ""),
        "role": user.get("role", "admin"),
        "avatar": user.get("avatar", ""),
        "company_id": company_id,
        "onboarding_completed": bool(user.get("onboarding_completed")),
        "onboarding_required": not bool(user.get("onboarding_completed")),
        "plan_selected": bool(user.get("plan_selected", True)),
        "billing_status": bs,
        "status": normalize_account_status(user.get("status")),
        "account_status": normalize_account_status(user.get("status")),
        "auth_provider": user.get("auth_provider", "email"),
        "email_verified": bool(user.get("email_verified")),
        "subscription_plan_code": plan_code,
        "subscription_status": subscription_status,
        "company_billing_status": company_billing_status,
        "stripe_subscription_id": stripe_subscription_id,
        "company_name": company_name,
        "company": {
            "id": company_id,
            "name": company_name,
            "plan": plan_code,
            "subscription_status": subscription_status,
            "billing_status": company_billing_status,
            "stripe_subscription_id": stripe_subscription_id,
        },
        "enterprise_invite_gate_pending": bool(user.get("enterprise_invite_gate_pending")),
        "oauth_providers": oauth_providers,
    }


async def ensure_user_company_assignment(db, user: dict) -> dict:
    if not user or not user.get("id"):
        raise HTTPException(401, "Authentication failed")
    company_id = str(user.get("company_id") or "").strip()
    if company_id and is_valid_company_id(company_id):
        return user
    email = (user.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(500, "Authentication failed")
    company_id = await ensure_company_defaults_for_oauth(db, email)
    await ensure_company_reference_data(db, company_id)
    await set_public_auth_context(db, email=email)
    await db.execute(
        "UPDATE users SET company_id=$1,updated_at=NOW() WHERE id=$2",
        company_id,
        user["id"],
    )
    refreshed = r(await db.fetchrow("SELECT * FROM users WHERE id=$1 LIMIT 1", user["id"]))
    if not refreshed:
        raise HTTPException(404, "User not found")
    logger.info(
        "auth company_assigned user_id=%s company_id=%s",
        refreshed["id"],
        company_id,
    )
    return refreshed


def r(row) -> Optional[dict]:
    return dict(row) if row else None


async def enrich_user_session_fields(db, user: dict) -> dict:
    """Attach plan + enterprise gate flags for API payloads and JWT-backed session claims."""
    u = dict(user)
    role = str(u.get("role") or "").strip().lower()
    company_id = str(u.get("company_id") or "").strip()
    u["email_verified"] = bool(u.get("email_verified"))
    u["subscription_plan_code"] = "free"
    u["subscription_status"] = "inactive"
    u["company_billing_status"] = "inactive"
    u["stripe_subscription_id"] = ""
    u["company_name"] = ""
    u["enterprise_invite_gate_pending"] = False
    u["enterprise_invite_satisfied"] = True
    uid = str(u.get("id") or "").strip()
    u["oauth_providers"] = []
    if uid and company_id and is_valid_company_id(company_id):
        try:
            await set_company_context(db, company_id)
            oauth_rows = await db.fetch(
                "SELECT provider, provider_id FROM user_oauth_providers WHERE user_id=$1 ORDER BY provider ASC",
                uid,
            )
            u["oauth_providers"] = [
                {"provider": str(r["provider"] or ""), "provider_id": str(r["provider_id"] or "")}
                for r in oauth_rows
            ]
        except Exception:
            u["oauth_providers"] = []
    if role == "super_admin" or not company_id:
        return u
    try:
        from services.billing_helpers import relaxed_billing_env

        if relaxed_billing_env():
            return u
    except Exception:
        pass
    row = await db.fetchrow(
        "SELECT COALESCE(s.plan_code, NULLIF(c.plan, ''), 'free') AS plan_code, "
        "COALESCE(s.status, NULLIF(c.subscription_status, ''), 'inactive') AS subscription_status, "
        "COALESCE(bc.payment_status, NULLIF(c.billing_status, ''), 'inactive') AS company_billing_status, "
        "COALESCE(s.stripe_subscription_id, NULLIF(c.stripe_subscription_id, ''), '') AS stripe_subscription_id, "
        "COALESCE(c.name, '') AS company_name, "
        "COALESCE(c.enterprise_team_gate_met, FALSE) AS enterprise_team_gate_met "
        "FROM companies c "
        "LEFT JOIN subscriptions s ON s.company_id = c.id "
        "LEFT JOIN billing_customers bc ON bc.company_id = c.id "
        "WHERE c.id = $1 "
        "LIMIT 1",
        company_id,
    )
    if not row:
        return u
    plan_code = str(row.get("plan_code") or "free").strip().lower() or "free"
    subscription_status = str(row.get("subscription_status") or "inactive").strip().lower() or "inactive"
    company_billing_status = str(row.get("company_billing_status") or "inactive").strip().lower() or "inactive"
    gate_met = bool(row.get("enterprise_team_gate_met"))
    u["subscription_plan_code"] = plan_code
    u["subscription_status"] = subscription_status
    u["company_billing_status"] = company_billing_status
    u["stripe_subscription_id"] = str(row.get("stripe_subscription_id") or "").strip()
    u["company_name"] = str(row.get("company_name") or "").strip()
    pending = (
        plan_code == "enterprise"
        and role == "admin"
        and bool(u.get("onboarding_completed"))
        and bool(u.get("plan_selected", True))
        and not gate_met
    )
    u["enterprise_invite_gate_pending"] = pending
    u["enterprise_invite_satisfied"] = not pending
    return u


def rs(rows) -> List[dict]:
    return [dict(row) for row in rows] if rows else []


def hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def token_candidates(token: str) -> tuple[str, str]:
    raw = (token or "").strip()
    return raw, hash_token(raw)


def get_company_id(u: dict) -> Optional[str]:
    if u.get("role") == "super_admin":
        return None
    company_id = (u.get("company_id") or "").strip()
    if not company_id:
        return None
    if not is_valid_company_id(company_id):
        raise HTTPException(401, "Invalid tenant context")
    return company_id


async def clear_public_auth_context(db) -> None:
    await db.execute("SELECT set_config('app.public_auth_mode', '', false)")
    await db.execute("SELECT set_config('app.auth_email', '', false)")
    await db.execute("SELECT set_config('app.auth_session_token', '', false)")
    await db.execute("SELECT set_config('app.auth_token', '', false)")


async def set_public_auth_context(db, *, email: str = "", session_token: str = "", token: str = "") -> None:
    await db.execute("SELECT set_config('app.public_auth_mode', 'on', false)")
    await db.execute(
        "SELECT set_config('app.auth_email', $1, false)",
        (email or "").strip().lower(),
    )
    await db.execute(
        "SELECT set_config('app.auth_session_token', $1, false)",
        (session_token or "").strip(),
    )
    await db.execute(
        "SELECT set_config('app.auth_token', $1, false)",
        (token or "").strip(),
    )


async def set_company_context(db, company_id: str) -> str:
    company_id = (company_id or "").strip()
    if not company_id or not is_valid_company_id(company_id):
        raise HTTPException(401, "Invalid tenant context")
    await db.execute("SELECT set_config('app.current_company', $1, false)", company_id)
    await clear_public_auth_context(db)
    return company_id


async def get_current_user_flexible(request: Request) -> dict:
    try:
        return await resolve_request_user(request)
    except HTTPException:
        raise unauthorized_exception()


async def require_roles(request: Request, allowed: list) -> dict:
    user = await get_current_user_flexible(request)
    if user.get("role") not in allowed:
        raise forbidden_exception()
    return user


async def ensure_unique_company_role(db, current_user: dict, role: str, exclude_user_id: Optional[str] = None):
    # Pulse Engine now supports multiple users with the same role inside a tenant.
    # The legacy single-user-per-role restriction blocked normal team growth.
    _ = db
    _ = current_user
    _ = role
    _ = exclude_user_id
    return


async def ensure_auth_security_primitives(db) -> None:
    global _auth_security_ready
    if _auth_security_ready:
        return
    async with _auth_security_lock:
        if _auth_security_ready:
            return
        await runtime_schema_ready(
            db,
            "auth_security_primitives",
            required_relations=("refresh_tokens",),
            required_columns=(
                ("oauth_states", "link_user_id"),
                ("users", "token_version"),
                ("users", "plan_selected"),
                ("users", "billing_status"),
                ("company_settings", "preferred_channels"),
                ("company_settings", "ai_static_fallback_message"),
            ),
            required_indexes=("idx_refresh_tokens_user_active", "idx_refresh_tokens_company_active"),
            raise_on_missing=True,
        )
        _auth_security_ready = True


async def ensure_conversation_ai_pause_schema(db) -> None:
    global _conversation_ai_pause_schema_ready
    if _conversation_ai_pause_schema_ready or not db:
        return
    async with _conversation_ai_pause_schema_lock:
        if _conversation_ai_pause_schema_ready:
            return
        await runtime_schema_ready(
            db,
            "conversation_ai_pause",
            required_columns=(
                ("conversations", "agent_type"),
                ("conversations", "ai_auto_paused"),
                ("conversations", "ai_paused_at"),
                ("conversations", "ai_paused_reason"),
                ("conversations", "ai_paused_error_type"),
                ("conversations", "ai_paused_provider"),
                ("conversations", "ai_paused_model"),
                ("conversations", "ai_paused_scope"),
                ("conversations", "ai_disabled_until"),
                ("conversations", "ai_failure_count"),
            ),
            raise_on_missing=True,
        )
        _conversation_ai_pause_schema_ready = True


async def ensure_super_admin_user(db) -> Optional[dict]:
    email = (os.environ.get("SUPER_ADMIN_EMAIL") or os.environ.get("IDENTITY_ADMIN_EMAIL") or "").strip().lower()
    password = (os.environ.get("SUPER_ADMIN_PASSWORD") or os.environ.get("IDENTITY_ADMIN_PASSWORD") or "").strip()
    if not email or not password:
        logger.warning("super_admin bootstrap skipped: SUPER_ADMIN_EMAIL/SUPER_ADMIN_PASSWORD are not configured")
        return None

    company_id = (os.environ.get("SUPER_ADMIN_COMPANY_ID") or _SUPER_ADMIN_COMPANY_ID_FALLBACK).strip()
    if not is_valid_company_id(company_id):
        logger.warning("super_admin bootstrap skipped: SUPER_ADMIN_COMPANY_ID must be a valid UUID")
        return None

    display_name = (os.environ.get("SUPER_ADMIN_NAME") or "Platform Super Admin").strip()
    if not display_name:
        display_name = "Platform Super Admin"

    await db.execute(
        "INSERT INTO companies(id,name,is_active,created_at,updated_at) "
        "VALUES($1,$2,TRUE,NOW(),NOW()) "
        "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name,is_active=TRUE,updated_at=NOW()",
        company_id,
        "Pulse Engine Platform",
    )
    await db.execute(
        "INSERT INTO company_settings(id,company_id,ai_enabled,ai_use_conversation_engine,ai_confidence_threshold,auto_assign,active_llm_engine_id,created_at,updated_at) "  # noqa: E501
        "VALUES($1,$1,TRUE,TRUE,0.70,TRUE,'',NOW(),NOW()) "
        "ON CONFLICT (company_id) DO NOTHING",
        company_id,
    )

    role_id = await resolve_role_id(db, "super_admin")
    password_hash = hash_password(password)
    existing = r(
        await db.fetchrow(
            "SELECT * FROM users WHERE LOWER(email)=LOWER($1) LIMIT 1",
            email,
        )
    )
    if existing:
        await db.execute(
            "UPDATE users SET password_hash=$1,name=$2,role='super_admin',role_id=$3,sub_role='',status='active',"
            "company_id=$4,onboarding_completed=TRUE,plan_selected=TRUE,billing_status='active',auth_provider='email',email_verified=TRUE,updated_at=NOW() "  # noqa: E501
            "WHERE id=$5",
            password_hash,
            display_name,
            role_id,
            company_id,
            existing["id"],
        )
        return r(await db.fetchrow("SELECT * FROM users WHERE id=$1 LIMIT 1", existing["id"]))

    user_id = make_id()
    await db.execute(
        "INSERT INTO users(id,email,password_hash,name,role,role_id,sub_role,status,avatar,company_id,phone,onboarding_completed,plan_selected,billing_status,auth_provider,email_verified,created_at,updated_at) "  # noqa: E501
        "VALUES($1,$2,$3,$4,'super_admin',$5,'','active','',$6,'',TRUE,TRUE,'active','email',TRUE,NOW(),NOW())",
        user_id,
        email,
        password_hash,
        display_name,
        role_id,
        company_id,
    )
    return r(await db.fetchrow("SELECT * FROM users WHERE id=$1 LIMIT 1", user_id))


def _token_version(user: dict) -> int:
    try:
        return int(user.get("token_version", 0) or 0)
    except (TypeError, ValueError):
        return 0


def build_minimal_token_data(user: dict) -> dict:
    company_id = str(user.get("company_id") or "").strip()
    if not company_id or not is_valid_company_id(company_id):
        raise HTTPException(500, "User is missing a valid tenant assignment")
    role = str(user.get("role", "admin")).strip()
    base: dict = {
        "sub": str(user["id"]).strip(),
        "cid": company_id,
        "role": role,
        "tv": _token_version(user),
    }
    if role.lower() == "super_admin":
        base["onboarding_completed"] = True
        base["plan_selected"] = True
        base["billing_status"] = "active"
        base["email_verified"] = True
        base["enterprise_invite_satisfied"] = True
        return base
    base["onboarding_completed"] = bool(user.get("onboarding_completed"))
    base["plan_selected"] = bool(user.get("plan_selected", True))
    bs = str(user.get("billing_status") or "unpaid").strip().lower() or "unpaid"
    if bs not in ("trial", "active", "unpaid"):
        bs = "unpaid"
    base["billing_status"] = bs
    base["email_verified"] = bool(user.get("email_verified"))
    base["enterprise_invite_satisfied"] = bool(user.get("enterprise_invite_satisfied", True))
    return base


async def _issue_refresh_token(db, user: dict, request: Optional[Request], rotated_from: str = "") -> str:
    await ensure_auth_security_primitives(db)
    refresh_id = make_id()
    token = create_refresh_token(build_minimal_token_data(user), token_id=refresh_id)
    ttl = (
        timedelta(hours=1)
        if user.get("role") == "super_admin"
        else timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )
    expires_at = datetime.now(timezone.utc) + ttl
    token_hash = hash_token(token)
    await db.execute(
        "INSERT INTO refresh_tokens(id,company_id,user_id,token_hash,token_version,user_agent,ip_address,expires_at,created_at) "  # noqa: E501
        "VALUES($1,$2,$3,$4,$5,$6,$7,$8,NOW())",
        refresh_id,
        user.get("company_id", ""),
        user["id"],
        token_hash,
        _token_version(user),
        request.headers.get("user-agent", "") if request else "",
        get_client_ip(request) if request else "",
        expires_at,
    )
    if rotated_from:
        await db.execute(
            "UPDATE refresh_tokens SET revoked_at=NOW(),revoke_reason='rotated',rotated_to=$1,last_used_at=NOW() WHERE id=$2 AND revoked_at IS NULL",  # noqa: E501
            refresh_id,
            rotated_from,
        )
    return token


async def build_auth_payload(db, user: dict, request: Optional[Request] = None) -> dict:
    user = await ensure_user_company_assignment(db, user)
    user = await enrich_user_session_fields(db, user)
    user_payload = build_user_payload(user)
    access_token = create_access_token(build_minimal_token_data(user))
    logger.info(
        "auth token_created user_id=%s company_id=%s",
        user_payload["id"],
        user_payload["company_id"],
    )
    return {
        "token": access_token,
        "refresh_token": await _issue_refresh_token(db, user, request),
        "user": user_payload,
    }


async def resolve_refresh_token_rotation(db, refresh_token: str, request: Optional[Request] = None) -> dict:
    await ensure_auth_security_primitives(db)
    payload = decode_token(refresh_token)
    if not payload or payload.get("type") != "refresh" or not payload.get("sub"):
        raise unauthorized_exception()
    refresh_id = str(payload.get("jti") or "").strip()
    if not refresh_id:
        raise unauthorized_exception()
    user = r(await db.fetchrow("SELECT * FROM users WHERE id=$1 LIMIT 1", payload["sub"]))
    if not user:
        raise unauthorized_exception()
    user = await ensure_user_company_assignment(db, user)
    user = await enrich_user_session_fields(db, user)
    account_status = normalize_account_status(user.get("status"))
    if account_status in ACCOUNT_REFRESH_BLOCKED_STATUSES:
        raise unauthorized_exception()
    if _token_version(user) != int(payload.get("token_version", 0) or 0):
        raise unauthorized_exception()
    raw_token, token_hash = token_candidates(refresh_token)
    row = r(
        await db.fetchrow(
            "SELECT * FROM refresh_tokens WHERE id=$1 AND user_id=$2 AND (token_hash=$3 OR token_hash=$4) AND revoked_at IS NULL LIMIT 1",  # noqa: E501
            refresh_id,
            user["id"],
            token_hash,
            raw_token,
        )
    )
    if not row:
        raise unauthorized_exception()
    expires_at = row.get("expires_at")
    if isinstance(expires_at, datetime) and expires_at < datetime.now(timezone.utc):
        await db.execute(
            "UPDATE refresh_tokens SET revoked_at=NOW(),revoke_reason='expired' WHERE id=$1 AND revoked_at IS NULL",
            refresh_id,
        )
        raise unauthorized_exception()
    await db.execute(
        "UPDATE refresh_tokens SET last_used_at=NOW() WHERE id=$1",
        refresh_id,
    )
    auth_payload = {
        "token": create_access_token(build_minimal_token_data(user)),
        "refresh_token": await _issue_refresh_token(db, user, request, rotated_from=refresh_id),
        "user": build_user_payload(user),
    }
    return auth_payload


async def revoke_refresh_tokens_for_user(db, user_id: str, reason: str = "revoked") -> None:
    await ensure_auth_security_primitives(db)
    await db.execute(
        "UPDATE refresh_tokens SET revoked_at=NOW(),revoke_reason=$1 WHERE user_id=$2 AND revoked_at IS NULL",
        reason,
        user_id,
    )


async def bump_user_token_version(db, user_id: str) -> int:
    await ensure_auth_security_primitives(db)
    row = await db.fetchrow(
        "UPDATE users SET token_version=COALESCE(token_version, 0) + 1, updated_at=NOW() WHERE id=$1 RETURNING token_version",  # noqa: E501
        user_id,
    )
    if not row:
        raise HTTPException(404, "User not found")
    return int(row["token_version"])


async def create_user_session(db, user_id: str, request: Request) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=OAUTH_SESSION_TTL_DAYS)
    user = await db.fetchrow("SELECT id,company_id,email FROM users WHERE id=$1", user_id)
    if not user:
        raise HTTPException(404, "User not found")
    user_data = await ensure_user_company_assignment(db, dict(user))
    await _create_login_session(db, user_data, request, token, expires)
    return token


async def _create_login_session(db, user: dict, request: Optional[Request], token: str, expires_at: datetime):
    company_id = await set_company_context(db, user.get("company_id", ""))
    user_agent = request.headers.get("user-agent", "") if request else ""
    token_hash = hash_token(token)
    await db.execute(
        "INSERT INTO sessions(id,company_id,user_id,session_token,ip_address,user_agent,device,status,is_active,last_seen_at,expires_at,created_at,updated_at) "  # noqa: E501
        "VALUES($1,$2,$3,$4,$5,$6,$7,'active',TRUE,NOW(),$8,NOW(),NOW())",
        make_id(),
        company_id,
        user.get("id", ""),
        token_hash,
        get_client_ip(request) if request else "",
        user_agent,
        user_agent,
        expires_at,
    )


async def close_login_sessions(db, user_id: str):
    await db.execute(
        "UPDATE sessions SET is_active=FALSE,status='closed',logout_time=NOW(),updated_at=NOW() WHERE user_id=$1 AND is_active=TRUE",  # noqa: E501
        user_id,
    )


async def record_auth_event(
    db,
    user_id: str,
    event_type: str,
    request: Optional[Request],
    success: bool,
    email: str = "",
):
    company_id = ""
    resolved_user_id = (user_id or "").strip() or None
    if resolved_user_id:
        row = await db.fetchrow("SELECT id,company_id FROM users WHERE id=$1 LIMIT 1", resolved_user_id)
    elif email:
        rows = await db.fetch(
            "SELECT id,company_id FROM users WHERE email=$1 ORDER BY created_at ASC LIMIT 2",
            (email or "").strip().lower(),
        )
        row = rows[0] if len(rows) == 1 else None
    else:
        row = None
    if row:
        row_data = dict(row)
        resolved_user_id = row_data.get("id") or resolved_user_id
        company_id = row_data.get("company_id") or ""
    if not company_id:
        return
    await set_company_context(db, company_id)
    stored_event_type = "login_failed" if event_type == "login" and not success else event_type
    await db.execute(
        "INSERT INTO security_events(id,user_id,company_id,event_type,ip_address,device,success,email,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,NOW())",  # noqa: E501
        make_id(),
        resolved_user_id,
        company_id,
        stored_event_type,
        get_client_ip(request) if request else "",
        request.headers.get("user-agent", "") if request else "",
        success,
        email,
    )


async def record_system_log(
    db,
    current_user: Optional[dict],
    action: str,
    entity_type: str,
    entity_id: str = "",
    metadata: Optional[dict] = None,
):
    company_id = str((current_user or {}).get("company_id") or "").strip()
    if not company_id or not is_valid_company_id(company_id):
        return
    await set_company_context(db, company_id)
    log_id = make_id()
    await db.execute(
        "INSERT INTO system_logs(id,user_id,company_id,action,entity_type,entity_id,created_at) VALUES($1,$2,$3,$4,$5,$6,NOW())",  # noqa: E501
        log_id,
        (current_user or {}).get("sub", ""),
        company_id,
        action,
        entity_type,
        entity_id,
    )
    if metadata:
        await db.executemany(
            "INSERT INTO system_log_metadata(log_id,company_id,meta_key,meta_value) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",  # noqa: E501
            [(log_id, company_id, k, str(v)) for k, v in metadata.items()],
        )


async def ensure_default_llm_engine(db) -> dict:
    await repair_unsupported_gemini_llm_engines(db)
    await ensure_gemini_llm_engines(db)
    provider = "gemini"
    model_name = DEFAULT_GEMINI_MODEL
    row = await db.fetchrow(
        "SELECT * FROM llm_engines WHERE provider=$1 AND model_name=$2 AND company_id='' LIMIT 1",
        provider,
        model_name,
    )
    if row:
        await db.execute(
            "UPDATE llm_engines SET is_active=TRUE,updated_at=NOW(),last_updated=NOW() WHERE id=$1",
            row["id"],
        )
        return dict(row)
    new_id = make_id()
    await db.execute(
        "INSERT INTO llm_engines(id,company_id,model_name,provider,api_endpoint,temperature,max_tokens,is_active,version,last_updated,created_at,updated_at) VALUES($1,'',$2,$3,$4,$5,$6,TRUE,$7,NOW(),NOW(),NOW())",  # noqa: E501
        new_id,
        model_name,
        provider,
        os.environ.get("AI_API_ENDPOINT", "").strip(),
        float(os.environ.get("AI_TEMPERATURE", "0.7")),
        int(os.environ.get("AI_MAX_TOKENS", "2048")),
        "Gemini 2.5 Flash",
    )
    return dict(await db.fetchrow("SELECT * FROM llm_engines WHERE id=$1", new_id))


async def ensure_gemini_llm_engines(db) -> None:
    await db.execute(
        "DELETE FROM llm_engines WHERE company_id='' AND NOT (provider='gemini' AND model_name=$1)",
        DEFAULT_GEMINI_MODEL,
    )
    if await db.fetchval(
        "SELECT id FROM llm_engines WHERE company_id='' AND provider='gemini' AND model_name=$1 LIMIT 1",
        DEFAULT_GEMINI_MODEL,
    ):
        await db.execute(
            "UPDATE llm_engines SET is_active=TRUE,version=$2,updated_at=NOW(),last_updated=NOW() "
            "WHERE company_id='' AND provider='gemini' AND model_name=$1",
            DEFAULT_GEMINI_MODEL,
            "Gemini 2.5 Flash",
        )
        return
    await db.execute(
        "INSERT INTO llm_engines(id,company_id,model_name,provider,api_endpoint,temperature,max_tokens,is_active,version,last_updated,created_at,updated_at) "
        "VALUES($1,'',$2,'gemini','',$3,$4,TRUE,$5,NOW(),NOW(),NOW()) ON CONFLICT DO NOTHING",
        make_id(),
        DEFAULT_GEMINI_MODEL,
        float(os.environ.get("AI_TEMPERATURE", "0.7")),
        int(os.environ.get("AI_MAX_TOKENS", "2048")),
        "Gemini 2.5 Flash",
    )


async def repair_unsupported_gemini_llm_engines(db) -> None:
    if not db or not hasattr(db, "fetch"):
        return
    supported = sorted(supported_model_names("gemini"))
    if not supported:
        return
    try:
        invalid_rows = [
            dict(row)
            for row in await db.fetch(
                "SELECT * FROM llm_engines WHERE provider='gemini' AND NOT (model_name = ANY($1::text[]))",
                supported,
            )
        ]
    except Exception as exc:
        logger.debug("Unsupported Gemini engine repair skipped: %s", exc)
        return
    for row in invalid_rows:
        row_id = str(row.get("id") or "").strip()
        company_id = str(row.get("company_id") or "").strip()
        old_model = str(row.get("model_name") or "").strip()
        if not row_id:
            continue
        try:
            existing = await db.fetchrow(
                "SELECT id FROM llm_engines WHERE company_id=$1 AND provider='gemini' AND model_name=$2 LIMIT 1",
                company_id,
                DEFAULT_GEMINI_MODEL,
            )
            if existing and str(existing.get("id") or "") != row_id:
                replacement_id = str(existing.get("id") or "")
                await db.execute(
                    "UPDATE company_settings SET active_llm_engine_id=$1,updated_at=NOW() "
                    "WHERE company_id=$2 AND active_llm_engine_id=$3",
                    replacement_id,
                    company_id,
                    row_id,
                )
                await db.execute(
                    "UPDATE ai_agents SET llm_id=$1,updated_at=NOW() WHERE company_id=$2 AND llm_id=$3",
                    replacement_id,
                    company_id,
                    row_id,
                )
                await db.execute("DELETE FROM llm_engines WHERE id=$1", row_id)
            else:
                await db.execute(
                    "UPDATE llm_engines SET model_name=$1,version=$2,updated_at=NOW(),last_updated=NOW() WHERE id=$3",
                    DEFAULT_GEMINI_MODEL,
                    "Gemini 2.5 Flash",
                    row_id,
                )
            logger.warning(
                "llm_engine_invalid_model_repaired company_id=%s engine_id=%s old_model=%s new_model=%s",
                company_id,
                row_id,
                old_model,
                DEFAULT_GEMINI_MODEL,
            )
        except Exception as exc:
            logger.warning(
                "llm_engine_invalid_model_repair_failed company_id=%s engine_id=%s old_model=%s error=%s",
                company_id,
                row_id,
                old_model,
                exc,
            )


async def ensure_embedding_vector_optimizations(db) -> None:
    global _embedding_vector_ready
    if _embedding_vector_ready:
        return
    async with _embedding_vector_lock:
        if _embedding_vector_ready:
            return
        try:
            vector_extension_available = bool(
                await db.fetchval("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector')")
            )
            missing_indexes = []
            for index_name in (
                "uq_embeddings_company_source_chunk",
                "idx_embeddings_company_source_lookup",
                "idx_embeddings_vector_cosine" if vector_extension_available else "",
            ):
                if index_name and not bool(await db.fetchval("SELECT to_regclass($1) IS NOT NULL", index_name)):
                    missing_indexes.append(index_name)
            duplicate_groups = await db.fetchval(
                "SELECT COUNT(*) FROM ("
                "SELECT 1 FROM embeddings "
                "GROUP BY company_id, source_type, source_id, chunk_index "
                "HAVING COUNT(*) > 1"
                ") dupes"
            )
            if int(duplicate_groups or 0):
                logger.warning(
                    "embedding_runtime_schema_warning reason=duplicate_embedding_groups count=%s migration=%s",
                    duplicate_groups,
                    DEPLOYMENT_SCHEMA_MIGRATION,
                )
            if missing_indexes:
                logger.warning(
                    "runtime_schema_migration_required area=embedding_vector_optimizations migration=%s missing=%s",
                    DEPLOYMENT_SCHEMA_MIGRATION,
                    ",".join(f"index:{item}" for item in missing_indexes),
                )
            dedup_missing = not bool(await db.fetchval("SELECT to_regclass('context_memory_dedup') IS NOT NULL"))
            if dedup_missing:
                logger.warning(
                    "runtime_schema_migration_required area=context_memory_dedup migration=%s missing=relation:context_memory_dedup",
                    DEPLOYMENT_SCHEMA_MIGRATION,
                )
            if is_production() and (missing_indexes or dedup_missing):
                missing = [*(f"index:{item}" for item in missing_indexes)]
                if dedup_missing:
                    missing.append("relation:context_memory_dedup")
                raise RuntimeError(
                    "Database schema is missing required AI runtime objects. "
                    f"Run Alembic migrations before starting production services: {', '.join(missing)}"
                )
            _embedding_vector_ready = True
            logger.info("Embedding vector schema checked")
        except Exception as exc:
            if is_production():
                raise
            logger.warning("Embedding vector optimization skipped: %s", exc)


def provider_has_credentials(provider: str) -> bool:
    provider = (provider or "").strip().lower()
    vertex_env_ready = bool(
        os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        and os.environ.get("GOOGLE_CLOUD_LOCATION", "").strip()
    )
    vertex_auto_enabled = (
        os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in {"1", "true", "yes", "on"}
        or os.environ.get("VERTEX_AI_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    )
    if provider == "vertex_ai":
        return vertex_env_ready
    if provider == "gemini_api":
        return bool(os.environ.get("GEMINI_API_KEY", "").strip())
    if provider == "gemini":
        return (vertex_auto_enabled and vertex_env_ready) or bool(os.environ.get("GEMINI_API_KEY", "").strip())
    if provider == "openai":
        return bool(os.environ.get("OPENAI_API_KEY", "").strip())
    if provider == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    return False


def provider_configuration_status(provider: str, *, is_active: bool = True) -> tuple[str, str]:
    provider = (provider or "").strip().lower()
    if not is_active:
        return "disabled", "Engine is disabled"
    if provider not in {*GEMINI_PROVIDER_KEYS, "openai", "anthropic"}:
        return "unavailable", f"Unsupported provider: {provider or 'unknown'}"
    if provider_has_credentials(provider):
        return "configured", ""
    key_name = {
        "gemini": "GEMINI_API_KEY or Vertex AI environment",
        "gemini_api": "GEMINI_API_KEY",
        "vertex_ai": "GOOGLE_CLOUD_PROJECT/GOOGLE_CLOUD_LOCATION",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }[provider]
    return "missing_api_key", f"{key_name} is not configured"


def model_supports_vision(provider: str, model_name: str) -> bool:
    return catalog_model_supports(provider, model_name, "vision")


def model_supports_audio(provider: str, model_name: str) -> bool:
    return catalog_model_supports(provider, model_name, "audio")


def model_supports_capability(provider: str, model_name: str, capability: str) -> bool:
    return catalog_model_supports(provider, model_name, capability)


def model_capabilities(provider: str, model_name: str) -> dict:
    return catalog_model_capabilities(provider, model_name)


def enrich_llm_engine(engine: Optional[dict], selected_id: str = "") -> dict:
    data = dict(engine or {})
    provider = data.get("provider", "")
    model_name = data.get("model_name", "")
    is_active = bool(data.get("is_active", True))
    provider_status, provider_status_detail = provider_configuration_status(provider, is_active=is_active)
    data["provider_ready"] = provider_status == "configured"
    data["provider_status"] = provider_status
    data["provider_status_detail"] = provider_status_detail
    caps = model_capabilities(provider, model_name)
    data.update(caps)
    data["capabilities"] = capability_labels(provider, model_name)
    data["is_selected"] = bool(selected_id) and data.get("id", "") == selected_id
    data["scope"] = "global" if not str(data.get("company_id") or "").strip() else "tenant"
    return data


async def ensure_company_settings_row(db, company_id: str = "") -> dict:
    company_id = (company_id or "").strip()
    if not company_id:
        company_id = await db.fetchval("SELECT NULLIF(current_setting('app.current_company', true), '')") or ""
    if not company_id:
        raise HTTPException(400, "company_id is required")
    await set_company_context(db, company_id)
    row = await db.fetchrow("SELECT * FROM company_settings WHERE company_id=$1 LIMIT 1", company_id)
    if row:
        return dict(row)
    settings_id = make_id()
    await db.execute(
        "INSERT INTO company_settings(id,company_id,ai_enabled,ai_use_conversation_engine,ai_confidence_threshold,auto_assign,active_llm_engine_id,created_at,updated_at) "  # noqa: E501
        "VALUES($1,$2,TRUE,TRUE,0.70,TRUE,'',NOW(),NOW())",
        settings_id,
        company_id or "",
    )
    return dict(await db.fetchrow("SELECT * FROM company_settings WHERE id=$1", settings_id))


async def get_ai_static_fallback_message(db=None, company_id: str = "") -> str:
    fallback = AI_STATIC_FALLBACK_DEFAULT
    if not db or not str(company_id or "").strip():
        return fallback
    try:
        row_message = await db.fetchval(
            "SELECT NULLIF(BTRIM(ai_static_fallback_message), '') "
            "FROM company_settings WHERE company_id=$1 LIMIT 1",
            str(company_id or "").strip(),
        )
        return str(row_message or fallback).strip() or fallback
    except Exception as exc:
        logger.warning(
            "AI static fallback lookup failed company_id=%s error=%s",
            str(company_id or "").strip(),
            exc,
        )
        return fallback


async def resolve_active_llm_engine(db, company_id: str = "") -> dict:
    company_id = (company_id or "").strip()
    if not company_id:
        company_id = await db.fetchval("SELECT NULLIF(current_setting('app.current_company', true), '')") or ""
    if not company_id:
        raise HTTPException(400, "company_id is required")
    default_engine = await ensure_default_llm_engine(db)
    settings = await ensure_company_settings_row(db, company_id)
    selected_id = (settings.get("active_llm_engine_id") or "").strip()
    selected = None
    if selected_id:
        selected = await db.fetchrow(
            "SELECT * FROM llm_engines WHERE id=$1 AND (company_id='' OR company_id=$2) LIMIT 1",
            selected_id,
            company_id,
        )
    if selected and dict(selected).get("is_active", True):
        return enrich_llm_engine(dict(selected), selected_id=selected_id)
    selected_id = default_engine.get("id", "")
    await db.execute(
        "UPDATE company_settings SET active_llm_engine_id=$1,updated_at=NOW() WHERE id=$2",
        selected_id,
        settings["id"],
    )
    return enrich_llm_engine(default_engine, selected_id=selected_id)


def _preferred_agent_types(intent_name: str = "", channel: str = "") -> list[str]:
    intent = str(intent_name or "").strip().lower()
    channel_key = str(channel or "").strip().lower()
    if intent in {
        "product_recommendation",
        "purchase_inquiry",
        "pricing",
        "quote_request",
        "availability_check",
        "sales",
    }:
        return ["sales", "generic", "support"]
    if intent in {
        "onboarding",
        "setup_help",
        "implementation",
        "activation",
    }:
        return ["onboarding", "support", "generic"]
    if intent in {
        "complaint",
        "refund",
        "cancel_request",
        "billing_issue",
        "technical_support",
        "support",
    }:
        return ["support", "generic", "sales"]
    if channel_key == "email":
        return ["support", "sales", "generic"]
    return ["generic", "support", "sales", "onboarding"]


async def resolve_active_ai_agent(
    db,
    company_id: str = "",
    *,
    intent_name: str = "",
    channel: str = "",
) -> Optional[dict]:
    scoped_company_id = str(company_id or "").strip()
    if not (db and scoped_company_id):
        return None

    rows = await db.fetch(
        "SELECT * FROM ai_agents WHERE company_id=$1 AND is_active=TRUE "
        "ORDER BY registered_at DESC, created_at DESC",
        scoped_company_id,
    )
    agents = [dict(row) for row in rows]
    if not agents:
        return None

    preferred_types = _preferred_agent_types(intent_name=intent_name, channel=channel)
    for agent_type in preferred_types:
        for agent in agents:
            if str(agent.get("agent_type") or "").strip().lower() == agent_type:
                return agent
    return agents[0]


async def is_company_ai_enabled(db, company_id: str = "") -> bool:
    settings = await ensure_company_settings_row(db, company_id or "")
    return bool(settings.get("ai_enabled", True))


AI_API_EXHAUSTED_MANUAL_MESSAGE = (
    "AI auto-response is paused because the AI provider is unavailable. Please respond manually."
)

SERIOUS_AI_PROVIDER_ERROR_TYPES = {
    "permission_denied",
    "api_key_suspended",
    "quota_exhausted",
    "rate_limited",
    "provider_not_configured",
    "all_providers_exhausted",
    "safety_config_failure",
    "provider_5xx",
    "provider_timeout",
    "invalid_model",
    "unsupported_model",
    "unsupported_provider",
    "no_active_llm_engine",
    "no_usable_provider_fallback",
}


def classify_ai_provider_failure(payload: Optional[dict]) -> str:
    data = dict(payload or {})
    provider_error = data.get("provider_error") if isinstance(data.get("provider_error"), dict) else {}
    text = " ".join(
        str(value or "").lower()
        for value in (
            data.get("error_type"),
            data.get("error_reason"),
            data.get("message"),
            provider_error.get("status"),
            provider_error.get("error_type"),
            provider_error.get("error_reason"),
            provider_error.get("message"),
        )
    )
    if any(marker in text for marker in ("permission_denied", "permission denied", "403")):
        return "permission_denied"
    if "suspended" in text and ("api" in text or "key" in text):
        return "api_key_suspended"
    if any(marker in text for marker in ("quota", "resource_exhausted", "quota_exhausted")):
        return "quota_exhausted"
    if any(marker in text for marker in ("rate_limited", "rate limit", "429")):
        return "rate_limited"
    if any(marker in text for marker in ("not configured", "missing api key", "api_key", "provider_not_configured")):
        return "provider_not_configured"
    if any(marker in text for marker in ("all providers exhausted", "all ai providers exhausted")):
        return "all_providers_exhausted"
    if any(marker in text for marker in ("safety", "configuration failure", "config failure")):
        return "safety_config_failure"
    if any(marker in text for marker in ("timeout", "timed out", "deadline")):
        return "provider_timeout"
    if any(marker in text for marker in ("invalid model", "model not found", "is not found")) or (
        "generatecontent" in text and "not supported" in text
    ):
        return "invalid_model"
    if any(marker in text for marker in ("unsupported model", "does not support image", "does not support audio")):
        return "unsupported_model"
    if "unsupported provider" in text:
        return "unsupported_provider"
    if "no active llm engine" in text:
        return "no_active_llm_engine"
    if "no usable provider" in text or "no provider fallback" in text:
        return "no_usable_provider_fallback"
    if any(marker in text for marker in ("500", "502", "503", "504", "internal server")):
        return "provider_5xx"
    if data.get("api_error") or provider_error:
        return str(data.get("error_type") or provider_error.get("error_type") or "provider_error").strip().lower()
    return ""


def is_ai_api_exhaustion_payload(payload: Optional[dict]) -> bool:
    return classify_ai_provider_failure(payload) in SERIOUS_AI_PROVIDER_ERROR_TYPES


def is_ai_failure_fallback_payload(payload: Optional[dict]) -> bool:
    data = dict(payload or {})
    if not data:
        return False
    provider_failure = is_ai_api_exhaustion_payload(data)
    api_error = bool(data.get("api_error") or data.get("provider_error"))
    static_fallback = bool(data.get("static_fallback_served"))
    if static_fallback and (api_error or provider_failure):
        return True
    return bool(data.get("fallback_used") and api_error and provider_failure)


def conversation_ai_auto_paused(conversation: Optional[dict]) -> bool:
    convo = dict(conversation or {})
    if convo.get("ai_auto_paused") or (convo.get("ai_handled") is False and convo.get("ai_paused_reason")):
        return True
    disabled_until = convo.get("ai_disabled_until")
    if disabled_until:
        try:
            disabled_dt = parse_dt(disabled_until) if isinstance(disabled_until, str) else disabled_until
            if disabled_dt and disabled_dt.tzinfo is None:
                disabled_dt = disabled_dt.replace(tzinfo=timezone.utc)
            return bool(disabled_dt and disabled_dt > datetime.now(timezone.utc))
        except Exception:
            return False
    return False


def _ai_failure_payload_reason(payload: Optional[dict]) -> str:
    data = dict(payload or {})
    return str(
        data.get("review_reason")
        or data.get("error_reason")
        or data.get("fallback_reason")
        or data.get("error_type")
        or AI_API_EXHAUSTED_MANUAL_MESSAGE
    ).strip()


async def pause_ai_auto_response(
    db,
    company_id: str,
    conversation_id: str,
    *,
    reason: str = "",
    error_type: str = "",
    provider: str = "",
    model: str = "",
    scope: str = "conversation",
    status: str = "open",
    increment_failure_count: bool = False,
    disable_reason: str = "static_fallback",
    trigger_message_id: str = "",
    actor_type: str = "",
    trigger: str = "",
    message_type: str = "",
    media_type: str = "",
) -> None:
    if not company_id or not conversation_id:
        return
    clean_reason = str(reason or AI_API_EXHAUSTED_MANUAL_MESSAGE).strip()[:500]
    clean_error_type = str(error_type or "provider_error").strip()[:120]
    clean_provider = str(provider or "").strip()[:80]
    clean_model = str(model or "").strip()[:120]
    clean_scope = str(scope or "conversation").strip()[:40]
    next_status = "escalated" if status == "escalated" else "open"
    failure_count_sql = ",ai_failure_count=COALESCE(ai_failure_count,0)+1" if increment_failure_count else ""
    logger.warning(
        "ai_auto_disabled company_id=%s conversation_id=%s trigger=%s reason=%s trigger_message_id=%s actor_type=%s message_type=%s media_type=%s detail=%s error_type=%s provider=%s model=%s scope=%s status=%s",
        company_id,
        conversation_id,
        str(trigger or disable_reason or "static_fallback").strip()[:80],
        str(disable_reason or "static_fallback").strip()[:80],
        str(trigger_message_id or "").strip()[:120],
        str(actor_type or "").strip()[:80],
        str(message_type or "").strip()[:80],
        str(media_type or "").strip()[:80],
        clean_reason[:300],
        clean_error_type,
        clean_provider,
        clean_model,
        clean_scope,
        next_status,
    )
    await db.execute(
        "UPDATE conversations SET ai_handled=FALSE,ai_auto_paused=TRUE,ai_paused_at=NOW(),"
        "ai_paused_reason=$1,ai_paused_error_type=$2,ai_paused_provider=$3,ai_paused_model=$4,"
        f"ai_paused_scope=$5,ai_disabled_until=NULL{failure_count_sql},escalation_notice=$1,status=$6,updated_at=NOW() "
        "WHERE id=$7 AND company_id=$8",
        clean_reason,
        clean_error_type,
        clean_provider,
        clean_model,
        clean_scope,
        next_status,
        conversation_id,
        company_id,
    )
    logger.info(
        "Chat AI-disabled state changed company_id=%s conversation_id=%s ai_auto_paused=true status=%s reason=%s disable_reason=%s",
        company_id,
        conversation_id,
        next_status,
        clean_reason[:300],
        str(disable_reason or "static_fallback").strip()[:80],
    )


async def auto_disable_ai_after_failure_fallback(
    db,
    company_id: str,
    conversation_id: str,
    payload: Optional[dict],
    *,
    channel: str = "",
    trace_id: str = "",
) -> bool:
    if not db or not company_id or not conversation_id or not is_ai_failure_fallback_payload(payload):
        return False
    data = dict(payload or {})
    already_paused = False
    try:
        existing = r(
            await db.fetchrow(
                "SELECT * FROM conversations WHERE id=$1 AND company_id=$2 LIMIT 1",
                conversation_id,
                company_id,
            )
        )
        already_paused = conversation_ai_auto_paused(existing)
    except Exception as exc:
        logger.warning(
            "AI fallback pause state lookup failed company_id=%s conversation_id=%s channel=%s trace_id=%s error=%s",
            company_id,
            conversation_id,
            channel,
            trace_id,
            exc,
        )
    reason = _ai_failure_payload_reason(data) or AI_API_EXHAUSTED_MANUAL_MESSAGE
    error_type = str(data.get("error_type") or classify_ai_provider_failure(data) or "provider_failure")
    logger.warning(
        "Fallback escalation auto-disable company_id=%s conversation_id=%s channel=%s trace_id=%s already_paused=%s error_type=%s",
        company_id,
        conversation_id,
        channel,
        trace_id,
        already_paused,
        error_type,
    )
    await pause_ai_auto_response(
        db,
        company_id,
        conversation_id,
        reason=reason,
        error_type=error_type,
        provider=str(data.get("provider") or ""),
        model=str(data.get("model_name") or data.get("model") or ""),
        scope="conversation",
        status="open",
        increment_failure_count=not already_paused,
        disable_reason="static_fallback",
        trigger="static_fallback",
    )
    return True


async def disable_company_ai_after_api_exhaustion(
    db,
    company_id: str,
    *,
    conversation_id: str = "",
    reason: str = "",
    error_type: str = "",
    provider: str = "",
    model: str = "",
) -> None:
    if not company_id:
        return
    if conversation_id:
        await pause_ai_auto_response(
            db,
            company_id,
            conversation_id,
            reason=AI_API_EXHAUSTED_MANUAL_MESSAGE,
            error_type=error_type or classify_ai_provider_failure({"error_reason": reason}) or "provider_error",
            provider=provider,
            model=model,
            scope="conversation",
            status="open",
            increment_failure_count=True,
            disable_reason="static_fallback",
            trigger="static_fallback",
        )
    else:
        logger.warning(
            "AI provider failure without conversation context; company AI left unchanged company_id=%s reason=%s",
            company_id,
            str(reason or "")[:300],
        )
    logger.warning(
        "AI auto-response paused after provider failure company_id=%s conversation_id=%s reason=%s",
        company_id,
        conversation_id,
        str(reason or "")[:300],
    )


async def persist_ai_session_record(
    db,
    company_id: str,
    convo_id: str,
    prompt: str,
    response: str,
    meta: Optional[dict] = None,
):
    meta = meta or {}
    llm_id = str(meta.get("llm_id", "") or "").strip()
    llm = None
    if llm_id:
        llm = r(
            await db.fetchrow(
                "SELECT * FROM llm_engines WHERE id=$1 AND (company_id='' OR company_id=$2) LIMIT 1",
                llm_id,
                company_id,
            )
        )
    if not llm:
        llm = await resolve_active_llm_engine(db, company_id)
    agent_id = str(meta.get("agent_id", "") or "").strip()
    ag = None
    if agent_id:
        ag = await db.fetchrow(
            "SELECT id FROM ai_agents WHERE id=$1 AND company_id=$2 AND is_active=TRUE LIMIT 1",
            agent_id,
            company_id,
        )
    if not ag:
        agent = await resolve_active_ai_agent(
            db,
            company_id,
            intent_name=str(meta.get("intent_name") or ""),
            channel=str(meta.get("channel") or ""),
        )
        if agent:
            ag = {"id": agent.get("id", "")}
    await db.execute(
        "INSERT INTO ai_sessions(id,company_id,convo_id,llm_id,agent_id,prompt,response,confidence,source,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW())",  # noqa: E501
        make_id(),
        company_id,
        convo_id or "",
        llm.get("id", ""),
        (dict(ag).get("id", "") if ag else ""),
        prompt,
        response,
        float(meta.get("confidence", 0)),
        str(meta.get("source", "")),
    )


async def persist_sentiment_record(
    db,
    company_id: str,
    entity_id: str,
    entity_type: str,
    analyzed_text: str,
    sentiment: dict,
):
    ag = (
        await db.fetchrow(
            "SELECT id FROM ai_agents WHERE company_id=$1 AND is_active=TRUE LIMIT 1",
            company_id,
        )
        if company_id
        else None
    )
    eb = sentiment.get("emotion_breakdown", {})

    def _float_or_none(value):
        try:
            return float(value)
        except Exception:
            return None

    sentiment_score = _float_or_none(sentiment.get("score"))
    confidence_score = _float_or_none(sentiment.get("confidence"))
    await db.execute(
        "INSERT INTO sentiment_analyses(id,company_id,agent_id,entity_id,entity_type,analyzed_text,sentiment_score,sentiment_label,confidence_score,emotion_joy,emotion_anger,emotion_sadness,emotion_fear,emotion_surprise,analyzed_at,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,NOW(),NOW())",  # noqa: E501
        make_id(),
        company_id,
        (dict(ag).get("id", "") if ag else ""),
        entity_id,
        entity_type,
        analyzed_text,
        sentiment_score,
        str(sentiment.get("sentiment_label") or sentiment.get("emotion") or "neutral"),
        confidence_score,
        _float_or_none(eb.get("joy")),
        _float_or_none(eb.get("anger")),
        _float_or_none(eb.get("sadness")),
        _float_or_none(eb.get("fear")),
        _float_or_none(eb.get("surprise")),
    )


async def record_webhook_event(
    db,
    platform: str,
    raw_payload: str,
    processing_status: str = "received",
    handler_id: str = "",
    external_id: str = "",
    company_id: str = "",
):
    safe_company_id = str(company_id or "").strip()
    if not safe_company_id:
        logger.warning(
            "Skipping webhook_events insert due to missing company_id platform=%s external_id=%s",
            platform,
            external_id,
        )
        return None
    try:
        async with company_context(db, safe_company_id):
            await db.execute(
                "INSERT INTO webhook_events(id,handler_id,company_id,event_type,external_id,processing_status,raw_payload,received_at,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,NOW(),NOW())",  # noqa: E501
                make_id(),
                handler_id,
                safe_company_id,
                platform,
                external_id,
                processing_status,
                raw_payload,
            )
        try:
            from data_pipeline.ingestion.raw_store import capture_raw_event

            await capture_raw_event(
                db,
                company_id=safe_company_id,
                source=platform,
                payload=raw_payload,
                event_type=platform,
                event_id=external_id,
                external_id=external_id,
                metadata={
                    "handler_id": handler_id,
                    "processing_status": processing_status,
                },
            )
        except Exception as exc:
            logger.warning(
                "raw event capture failed platform=%s company_id=%s error=%s",
                platform,
                safe_company_id,
                exc,
            )
        return {"company_id": safe_company_id, "event_type": platform}
    except Exception as exc:
        logger.warning(
            "webhook_events insert failed platform=%s company_id=%s error=%s",
            platform,
            safe_company_id,
            exc,
        )
        return None


async def insert_chat_history_record(db, conversation: dict, message: dict):
    if not conversation or not message:
        return
    company_id = str(conversation.get("company_id") or message.get("company_id") or "").strip()
    if not company_id:
        return
    ca = message.get("created_at")
    ca_dt = parse_dt(ca) if isinstance(ca, str) else (ca if isinstance(ca, datetime) else datetime.now(timezone.utc))
    if hasattr(db, "_get_pool"):
        async with company_context(db, company_id):
            await db.execute(
                "INSERT INTO chat_histories(id,company_id,conversation_id,customer_id,customer_name,channel,sender_type,sender_name,content,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) ON CONFLICT DO NOTHING",  # noqa: E501
                make_id(),
                company_id,
                conversation.get("id", message.get("conversation_id", "")),
                conversation.get("customer_id", ""),
                conversation.get("customer_name", message.get("sender_name", "")),
                conversation.get("channel", "web_chat"),
                message.get("sender_type", "agent"),
                message.get("sender_name", ""),
                message.get("content", ""),
                ca_dt,
            )
        return
    await db.execute(
        "INSERT INTO chat_histories(id,company_id,conversation_id,customer_id,customer_name,channel,sender_type,sender_name,content,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) ON CONFLICT DO NOTHING",  # noqa: E501
        make_id(),
        company_id,
        conversation.get("id", message.get("conversation_id", "")),
        conversation.get("customer_id", ""),
        conversation.get("customer_name", message.get("sender_name", "")),
        conversation.get("channel", "web_chat"),
        message.get("sender_type", "agent"),
        message.get("sender_name", ""),
        message.get("content", ""),
        ca_dt,
    )


async def persist_chat_history(db, conversation: dict, message: dict):
    if not conversation or not message:
        return
    company_id = str(conversation.get("company_id") or message.get("company_id") or "").strip()
    if not company_id:
        return
    ca = message.get("created_at")
    ca_dt = parse_dt(ca) if isinstance(ca, str) else (ca if isinstance(ca, datetime) else datetime.now(timezone.utc))
    await insert_chat_history_record(db, conversation, message)
    try:
        from data_pipeline.ingestion.raw_store import capture_raw_message

        await capture_raw_message(
            db,
            conversation=conversation,
            message={
                **dict(message),
                "company_id": company_id,
                "created_at": ca_dt,
            },
            source=str(conversation.get("channel") or "message"),
        )
    except Exception as exc:
        logger.warning(
            "raw message capture failed company_id=%s conversation_id=%s message_id=%s error=%s",
            company_id,
            conversation.get("id", ""),
            message.get("id", ""),
            exc,
        )


def is_data_url_image(url: str) -> bool:
    if not isinstance(url, str) or not url.startswith("data:image/"):
        return False
    try:
        header, payload = url.split(",", 1)
        if ";base64" not in header:
            return False
        raw = base64.b64decode(payload, validate=True)
        return 0 < len(raw) <= 8 * 1024 * 1024
    except Exception:
        return False


def is_data_url_video(url: str) -> bool:
    if not isinstance(url, str) or not url.startswith("data:video/"):
        return False
    try:
        header, payload = url.split(",", 1)
        if ";base64" not in header:
            return False
        raw = base64.b64decode(payload, validate=True)
        return 0 < len(raw) <= 16 * 1024 * 1024
    except Exception:
        return False


def is_data_url_media(url: str) -> bool:
    return is_data_url_image(url) or is_data_url_video(url)


async def ensure_message_attachment_schema(db) -> None:
    global _message_attachment_schema_ready
    if _message_attachment_schema_ready or not db:
        return
    async with _message_attachment_schema_lock:
        if _message_attachment_schema_ready:
            return
        await runtime_schema_ready(
            db,
            "message_attachments",
            required_columns=(
                ("message_attachments", "conversation_id"),
                ("message_attachments", "customer_id"),
                ("message_attachments", "channel"),
                ("message_attachments", "original_filename"),
                ("message_attachments", "mime_type"),
                ("message_attachments", "storage_url"),
                ("message_attachments", "thumbnail_url"),
                ("message_attachments", "provider_media_id"),
                ("message_attachments", "raw_metadata"),
                ("message_attachments", "image_analysis_status"),
                ("message_attachments", "image_analysis_summary"),
                ("message_attachments", "image_detected_objects"),
                ("message_attachments", "image_ocr_text"),
                ("message_attachments", "image_analysis_model"),
                ("message_attachments", "image_analysis_error"),
            ),
            required_indexes=(
                "idx_message_attachments_conversation_id",
                "idx_message_attachments_customer_id",
                "idx_message_attachments_image_analysis_status",
            ),
            raise_on_missing=True,
        )
        _message_attachment_schema_ready = True


def normalize_attachment_payload(attachment: dict) -> Optional[dict]:
    if not isinstance(attachment, dict):
        return None
    url = str(attachment.get("url") or attachment.get("file_url") or "").strip()
    data_url = str(attachment.get("data_url") or "").strip()
    if not url and data_url:
        url = data_url
    if not url:
        return None
    atype = str(attachment.get("type") or attachment.get("file_type") or "").strip().lower() or "unknown"
    if is_data_url_image(url):
        atype = "image"
    elif is_data_url_video(url):
        atype = "video"
    size = attachment.get("size", attachment.get("file_size", 0))
    try:
        size = max(0, int(size or 0))
    except Exception:
        size = 0
    raw_metadata = attachment.get("raw_metadata") or attachment.get("metadata") or {}
    if not isinstance(raw_metadata, dict):
        raw_metadata = {"value": str(raw_metadata)}
    for key in (
        "product_id",
        "product_name",
        "product_title",
        "product_category",
        "image_index",
        "caption",
    ):
        value = attachment.get(key)
        if value not in (None, "") and key not in raw_metadata:
            raw_metadata[key] = value
    return {
        "type": atype,
        "url": url,
        "name": str(attachment.get("name") or attachment.get("file_name") or "").strip(),
        "size": size,
        "mime_type": str(attachment.get("mime_type") or attachment.get("mimeType") or "").strip(),
        "thumbnail_url": str(attachment.get("thumbnail_url") or attachment.get("thumbnailUrl") or "").strip(),
        "provider_media_id": str(
            attachment.get("provider_media_id")
            or attachment.get("providerMediaId")
            or attachment.get("media_id")
            or attachment.get("id")
            or ""
        ).strip(),
        "raw_metadata": raw_metadata,
    }


def normalize_attachment_row(row: dict) -> dict:
    data = dict(row)
    raw_metadata = data.get("raw_metadata") or {}
    if not isinstance(raw_metadata, dict):
        raw_metadata = {"value": str(raw_metadata)}
    detected_objects = data.get("image_detected_objects") or []
    if not isinstance(detected_objects, list):
        detected_objects = []
    return {
        "id": data.get("id", ""),
        "type": str(data.get("file_type") or "unknown"),
        "url": str(data.get("file_url") or ""),
        "name": str(data.get("file_name") or ""),
        "size": int(data.get("file_size") or 0),
        "conversation_id": str(data.get("conversation_id") or ""),
        "customer_id": str(data.get("customer_id") or ""),
        "channel": str(data.get("channel") or ""),
        "original_filename": str(data.get("original_filename") or data.get("file_name") or ""),
        "mime_type": str(data.get("mime_type") or ""),
        "storage_url": str(data.get("storage_url") or data.get("file_url") or ""),
        "thumbnail_url": str(data.get("thumbnail_url") or ""),
        "provider_media_id": str(data.get("provider_media_id") or ""),
        "raw_metadata": raw_metadata,
        "product_id": str(raw_metadata.get("product_id") or ""),
        "product_name": str(raw_metadata.get("product_name") or ""),
        "product_title": str(raw_metadata.get("product_title") or ""),
        "product_category": str(raw_metadata.get("product_category") or ""),
        "caption": str(raw_metadata.get("caption") or ""),
        "image_analysis_status": str(data.get("image_analysis_status") or "skipped"),
        "image_analysis_summary": str(data.get("image_analysis_summary") or ""),
        "image_detected_objects": detected_objects,
        "image_ocr_text": str(data.get("image_ocr_text") or ""),
        "image_analysis_model": str(data.get("image_analysis_model") or ""),
        "image_analysis_error": str(data.get("image_analysis_error") or ""),
    }


async def save_message_attachments(db, message_id: str, attachments: Optional[list]) -> List[dict]:
    saved: List[dict] = []
    if attachments:
        await ensure_message_attachment_schema(db)
    message_context = r(
        await db.fetchrow(
            "SELECT m.company_id,m.conversation_id,COALESCE(c.customer_id,'') AS customer_id,COALESCE(c.channel,'') AS channel "
            "FROM messages m LEFT JOIN conversations c ON c.id=m.conversation_id AND c.company_id=m.company_id "
            "WHERE m.id=$1 LIMIT 1",
            message_id,
        )
    )
    company_id = str((message_context or {}).get("company_id") or "")
    if attachments and not company_id:
        raise HTTPException(404, "Message not found")
    if company_id:
        await set_company_context(db, company_id)
    for item in attachments or []:
        normalized = normalize_attachment_payload(item)
        if not normalized:
            continue
        if is_data_url_media(normalized["url"]):
            stored = store_media_data_url(
                normalized["url"],
                category="message-attachments",
                company_id=company_id,
                public_url_prefix="/api/conversations/attachments/media",
                original_filename=normalized["name"],
            )
            normalized["url"] = stored["url"]
            normalized["name"] = normalized["name"] or stored["file_name"]
            normalized["size"] = stored["file_size"]
            normalized["mime_type"] = normalized["mime_type"] or stored["mime_type"]
            normalized["storage_url"] = stored["url"]
            normalized["raw_metadata"] = {**normalized["raw_metadata"], "storage_key": stored["storage_key"]}
        else:
            normalized["storage_url"] = normalized["url"]
        is_image = normalized["type"] == "image" or str(normalized.get("mime_type") or "").startswith("image/")
        image_analysis_status = str(normalized.get("image_analysis_status") or ("pending" if is_image else "skipped"))
        attachment_id = make_id()
        await db.execute(
            "INSERT INTO message_attachments("
            "id,company_id,message_id,conversation_id,customer_id,channel,file_type,file_url,file_name,file_size,"
            "original_filename,mime_type,storage_url,thumbnail_url,provider_media_id,raw_metadata,"
            "image_analysis_status,image_analysis_summary,image_detected_objects,image_ocr_text,image_analysis_model,"
            "image_analysis_error,created_at"
            ") VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,'','[]'::jsonb,'','','',NOW())",
            attachment_id,
            company_id,
            message_id,
            str((message_context or {}).get("conversation_id") or ""),
            str((message_context or {}).get("customer_id") or ""),
            str((message_context or {}).get("channel") or ""),
            normalized["type"],
            normalized["url"],
            normalized["name"],
            normalized["size"],
            normalized["name"],
            normalized["mime_type"],
            normalized["storage_url"],
            normalized["thumbnail_url"],
            normalized["provider_media_id"],
            normalized["raw_metadata"],
            image_analysis_status,
        )
        saved.append(
            {
                "id": attachment_id,
                **normalized,
                "conversation_id": str((message_context or {}).get("conversation_id") or ""),
                "customer_id": str((message_context or {}).get("customer_id") or ""),
                "channel": str((message_context or {}).get("channel") or ""),
                "original_filename": normalized["name"],
                "image_analysis_status": image_analysis_status,
                "image_analysis_summary": "",
                "image_detected_objects": [],
                "image_ocr_text": "",
                "image_analysis_model": "",
                "image_analysis_error": "",
            }
        )
    return saved


def _is_message_reaction_missing_error(exc: Exception) -> bool:
    message = str(exc or "").lower()
    return exc.__class__.__name__ == "UndefinedTableError" or (
        "message_reactions" in message and "does not exist" in message
    )


async def _message_reaction_schema_scope(db) -> str:
    fetchval = getattr(db, "fetchval", None)
    if not fetchval:
        return f"db:{id(db)}"
    try:
        schema = await fetchval("SELECT current_schema()")
        return str(schema or f"db:{id(db)}")
    except Exception:
        return f"db:{id(db)}"


async def _message_reaction_relation_exists(db) -> bool | None:
    fetchval = getattr(db, "fetchval", None)
    if not fetchval:
        return None
    try:
        return bool(await fetchval("SELECT to_regclass('message_reactions') IS NOT NULL"))
    except Exception:
        return None


async def ensure_message_reaction_schema(db, *, force: bool = False) -> None:
    if not db:
        return
    scope = await _message_reaction_schema_scope(db)
    if not force and scope in _message_reaction_schema_ready:
        exists = await _message_reaction_relation_exists(db)
        if exists is not False:
            return
        _message_reaction_schema_ready.discard(scope)
    async with _message_reaction_schema_lock:
        if not force and scope in _message_reaction_schema_ready:
            exists = await _message_reaction_relation_exists(db)
            if exists is not False:
                return
            _message_reaction_schema_ready.discard(scope)
        exists = await _message_reaction_relation_exists(db)
        if exists is True:
            _message_reaction_schema_ready.add(scope)
            return
        logger.warning(
            "runtime_schema_migration_required area=message_reactions migration=%s missing=relation:message_reactions schema=%s",
            DEPLOYMENT_SCHEMA_MIGRATION,
            scope,
        )


def normalize_reaction_row(row: dict) -> dict:
    data = dict(row or {})
    raw_payload = data.get("raw_payload") or {}
    if not isinstance(raw_payload, dict):
        raw_payload = {"value": str(raw_payload)}
    return {
        "id": str(data.get("id") or ""),
        "company_id": str(data.get("company_id") or ""),
        "conversation_id": str(data.get("conversation_id") or ""),
        "message_id": str(data.get("message_id") or ""),
        "provider_message_id": str(data.get("provider_message_id") or ""),
        "target_provider_message_id": str(data.get("target_provider_message_id") or ""),
        "channel": str(data.get("channel") or ""),
        "actor_type": str(data.get("actor_type") or "customer"),
        "actor_id": str(data.get("actor_id") or ""),
        "emoji": str(data.get("emoji") or ""),
        "action": str(data.get("action") or "added"),
        "raw_payload": raw_payload,
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


async def save_message_reaction(
    db,
    *,
    company_id: str,
    channel: str,
    provider_message_id: str = "",
    target_provider_message_id: str = "",
    message_id: str = "",
    conversation_id: str = "",
    actor_type: str = "customer",
    actor_id: str = "",
    emoji: str = "",
    action: str = "added",
    raw_payload: Optional[dict] = None,
) -> dict:
    if not db:
        return {}
    await ensure_message_reaction_schema(db)
    if await _message_reaction_relation_exists(db) is False:
        return {}
    scoped_company_id = str(company_id or "").strip()
    scoped_channel = str(channel or "").strip().lower()
    target_provider_id = str(target_provider_message_id or "").strip()
    scoped_message_id = str(message_id or "").strip()
    scoped_conversation_id = str(conversation_id or "").strip()
    scoped_provider_event_id = str(provider_message_id or "").strip()
    actor = str(actor_id or "").strip()
    reaction_action = str(action or "added").strip().lower()
    if reaction_action not in {"added", "updated", "removed"}:
        reaction_action = "added"
    reaction_emoji = "" if reaction_action == "removed" else str(emoji or "").strip()
    payload = raw_payload if isinstance(raw_payload, dict) else {}

    if not scoped_company_id or not scoped_channel:
        return {}

    if scoped_message_id:
        target = r(
            await db.fetchrow(
                "SELECT id,conversation_id,external_message_id FROM messages WHERE company_id=$1 AND id=$2 LIMIT 1",
                scoped_company_id,
                scoped_message_id,
            )
        )
    elif target_provider_id:
        target = r(
            await db.fetchrow(
                "SELECT id,conversation_id,external_message_id FROM messages "
                "WHERE company_id=$1 AND (external_message_id=$2 OR id=$2) "
                "ORDER BY created_at DESC LIMIT 1",
                scoped_company_id,
                target_provider_id,
            )
        )
    else:
        target = {}

    if target:
        scoped_message_id = str(target.get("id") or scoped_message_id)
        scoped_conversation_id = str(target.get("conversation_id") or scoped_conversation_id)
        target_provider_id = target_provider_id or str(target.get("external_message_id") or "")

    if not scoped_provider_event_id:
        digest = hashlib.sha256(
            f"{scoped_company_id}:{scoped_channel}:{target_provider_id}:{scoped_message_id}:{actor}:{reaction_emoji}:{reaction_action}".encode(
                "utf-8"
            )
        ).hexdigest()[:32]
        scoped_provider_event_id = f"reaction:{digest}"

    existing = r(
        await db.fetchrow(
            "SELECT * FROM message_reactions WHERE company_id=$1 AND channel=$2 AND provider_message_id=$3 LIMIT 1",
            scoped_company_id,
            scoped_channel,
            scoped_provider_event_id,
        )
    )
    if existing:
        row = await db.fetchrow(
            "UPDATE message_reactions SET conversation_id=$1,message_id=$2,target_provider_message_id=$3,"
            "actor_type=$4,actor_id=$5,emoji=$6,action=$7,raw_payload=$8,updated_at=NOW() "
            "WHERE id=$9 RETURNING *",
            scoped_conversation_id,
            scoped_message_id,
            target_provider_id,
            str(actor_type or "customer").strip() or "customer",
            actor,
            reaction_emoji,
            reaction_action,
            payload,
            existing["id"],
        )
        return normalize_reaction_row(dict(row))

    reaction_id = make_id()
    try:
        row = await db.fetchrow(
            "INSERT INTO message_reactions("
            "id,company_id,conversation_id,message_id,provider_message_id,target_provider_message_id,channel,"
            "actor_type,actor_id,emoji,action,raw_payload,created_at,updated_at"
            ") VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,NOW(),NOW()) RETURNING *",
            reaction_id,
            scoped_company_id,
            scoped_conversation_id,
            scoped_message_id,
            scoped_provider_event_id,
            target_provider_id,
            scoped_channel,
            str(actor_type or "customer").strip() or "customer",
            actor,
            reaction_emoji,
            reaction_action,
            payload,
        )
        return normalize_reaction_row(dict(row))
    except Exception:
        row = await db.fetchrow(
            "SELECT * FROM message_reactions WHERE company_id=$1 AND channel=$2 AND provider_message_id=$3 LIMIT 1",
            scoped_company_id,
            scoped_channel,
            scoped_provider_event_id,
        )
        return normalize_reaction_row(dict(row)) if row else {}


async def _linked_context_conversation_ids(
    db,
    *,
    convo_id: str,
    company_id: str = "",
    customer_id: str = "",
) -> list[str]:
    scoped_company_id = str(company_id or "").strip()
    scoped_customer_id = str(customer_id or "").strip()
    scoped_convo_id = str(convo_id or "").strip()
    if not scoped_company_id or not scoped_customer_id:
        return [scoped_convo_id] if scoped_convo_id else []
    try:
        profile_id = str(
            await db.fetchval(
                "SELECT metadata #>> '{identity_unification,profile_id}' "
                "FROM customers WHERE id=$1 AND company_id=$2 LIMIT 1",
                scoped_customer_id,
                scoped_company_id,
            )
            or ""
        ).strip()
        if not profile_id:
            return [scoped_convo_id] if scoped_convo_id else []
        profile_ids = [profile_id]
        try:
            merged_rows = await db.fetch(
                "SELECT source_customer_id::text AS source_customer_id, "
                "target_customer_id::text AS target_customer_id, unified_customer_id::text AS unified_customer_id "
                "FROM merged_profile_records WHERE tenant_id=$1 "
                "AND $2 = ANY(ARRAY[source_customer_id::text,target_customer_id::text,unified_customer_id::text]) "
                "ORDER BY created_at DESC LIMIT 20",
                scoped_company_id,
                profile_id,
            )
            for row in merged_rows or []:
                for key in ("source_customer_id", "target_customer_id", "unified_customer_id"):
                    value = str(dict(row).get(key) or "").strip()
                    if value and value not in profile_ids:
                        profile_ids.append(value)
        except Exception:
            pass
        rows = await db.fetch(
            "SELECT id FROM conversations WHERE company_id=$1 "
            "AND customer_id IN ("
            "  SELECT id FROM customers WHERE company_id=$1 "
            "  AND metadata #>> '{identity_unification,profile_id}' = ANY($2::text[])"
            ") "
            "ORDER BY last_message_at DESC LIMIT 20",
            scoped_company_id,
            profile_ids,
        )
        ids = [str(dict(row).get("id") or "").strip() for row in rows]
        ids = [item for item in ids if item]
        if scoped_convo_id and scoped_convo_id not in ids:
            ids.insert(0, scoped_convo_id)
        return ids or ([scoped_convo_id] if scoped_convo_id else [])
    except Exception as exc:
        logger.debug("linked context lookup skipped conversation_id=%s: %s", scoped_convo_id, exc)
        return [scoped_convo_id] if scoped_convo_id else []


async def fetch_messages_with_attachments(
    db,
    convo_id: str,
    limit: int = 500,
    *,
    since_days: int | None = None,
    company_id: str = "",
    customer_id: str = "",
    include_linked_profiles: bool = False,
) -> List[dict]:
    scoped_limit = max(1, int(limit or 500))
    conversation_ids = (
        await _linked_context_conversation_ids(
            db,
            convo_id=convo_id,
            company_id=company_id,
            customer_id=customer_id,
        )
        if include_linked_profiles
        else [str(convo_id or "").strip()]
    )
    conversation_ids = [item for item in conversation_ids if item]
    if not conversation_ids:
        return []
    if since_days and int(since_days) > 0:
        rows = await db.fetch(
            "SELECT * FROM ("
            "SELECT * FROM messages WHERE conversation_id = ANY($1::text[]) "
            "AND created_at >= NOW() - ($3::int * INTERVAL '1 day') "
            "ORDER BY created_at DESC LIMIT $2"
            ") recent ORDER BY created_at ASC",
            conversation_ids,
            scoped_limit,
            int(since_days),
        )
    else:
        rows = await db.fetch(
            "SELECT * FROM ("
            "SELECT * FROM messages WHERE conversation_id = ANY($1::text[]) "
            "ORDER BY created_at DESC LIMIT $2"
            ") recent ORDER BY created_at ASC",
            conversation_ids,
            scoped_limit,
        )
    messages = [dict(row) for row in rows]
    if not messages:
        return []
    message_ids = [msg["id"] for msg in messages]
    attachments = await db.fetch(
        "SELECT * FROM message_attachments WHERE message_id = ANY($1::text[]) ORDER BY created_at ASC",
        message_ids,
    )
    by_message: dict[str, list] = {}
    for row in attachments:
        item = normalize_attachment_row(dict(row))
        by_message.setdefault(str(row["message_id"]), []).append(item)
    await ensure_message_reaction_schema(db)
    if await _message_reaction_relation_exists(db) is False:
        reactions = []
    else:
        try:
            reactions = await db.fetch(
                "SELECT * FROM message_reactions WHERE message_id = ANY($1::text[]) AND action <> 'removed' "
                "ORDER BY created_at ASC",
                message_ids,
            )
        except Exception as exc:
            if not _is_message_reaction_missing_error(exc):
                raise
            logger.warning(
                "runtime_schema_migration_required area=message_reactions migration=%s missing=relation:message_reactions",
                DEPLOYMENT_SCHEMA_MIGRATION,
            )
            reactions = []
    reactions_by_message: dict[str, list] = {}
    for row in reactions:
        item = normalize_reaction_row(dict(row))
        reactions_by_message.setdefault(str(row["message_id"]), []).append(item)
    for msg in messages:
        msg["attachments"] = by_message.get(msg["id"], [])
        msg["reactions"] = reactions_by_message.get(msg["id"], [])
    return messages


async def persist_user_ai_memory(db, current_user: dict, content: str, role: str = "agent"):
    if not content:
        return
    await db.execute(
        "INSERT INTO user_ai_memories(id,user_id,company_id,role,content,created_at) VALUES($1,$2,$3,$4,$5,NOW())",
        make_id(),
        current_user.get("sub", ""),
        current_user.get("company_id", ""),
        role,
        content[:500],
    )


async def ensure_customer_profile(db, customer: dict):
    if not customer or not customer.get("id"):
        return
    exists = await db.fetchval("SELECT id FROM customer_profiles WHERE customer_id=$1", customer["id"])
    if exists:
        return
    await db.execute(
        "INSERT INTO customer_profiles(id,customer_id,company_id,engagement_level,last_interaction,created_at,updated_at) VALUES($1,$2,$3,$4,NOW(),NOW(),NOW()) ON CONFLICT DO NOTHING",  # noqa: E501
        make_id(),
        customer["id"],
        customer.get("company_id", ""),
        customer.get("segment", "general") or "general",
    )


async def create_notification(
    db,
    sio,
    connected_users,
    actor_user: dict,
    title: str,
    body: str = "",
    notification_type: str = "info",
    target_user_id: str = "",
    reminder_key: str = "",
    action_url: str = "",
) -> Optional[dict]:
    try:
        note_id = make_id()
        uid = target_user_id or (actor_user.get("sub", "") if actor_user else "")
        cid = actor_user.get("company_id", "") if actor_user else ""
        await db.execute(
            "INSERT INTO notifications(id,title,body,type,user_id,company_id,created_by,is_read,reminder_key,action_url,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,FALSE,$8,$9,NOW())",  # noqa: E501
            note_id,
            title,
            body,
            notification_type,
            uid,
            cid,
            actor_user.get("sub", "") if actor_user else "",
            reminder_key or None,
            action_url or None,
        )
        note = {
            "id": note_id,
            "title": title,
            "body": body,
            "type": notification_type,
            "user_id": uid,
            "company_id": cid,
            "is_read": False,
        }
        if sio and connected_users:
            sid = connected_users.get(uid)
            if sid:
                await sio.emit("notification", note, room=sid)
        return note
    except Exception:
        return None


async def _notify_agents_handoff(
    db,
    sio,
    connected_users,
    company_id: str,
    convo_id: str,
    customer_name: str,
    confidence: float,
):
    try:
        rows = await db.fetch(
            "SELECT id FROM users WHERE company_id=$1 AND role=ANY($2) AND status='active' LIMIT 50",
            company_id,
            ["admin", "company_agent"],
        )
        title = f"Conversation needs your reply — {customer_name or 'Customer'}"
        body = f"AI confidence was too low ({confidence:.0%}). Please review and reply."
        for row in rows:
            await create_notification(
                db,
                sio,
                connected_users,
                {"sub": "system", "company_id": company_id},
                title,
                body,
                "warning",
                target_user_id=row["id"],
                action_url=f"/inbox?conversation={convo_id}",
            )
    except Exception as e:
        logger.warning(f"Handoff notification failed: {e}")


async def escalate_conversation_to_human(
    db,
    convo_id: str,
    company_id: str,
    customer_name: str,
    channel: str,
    reason: str = "",
    agent_id: str = "",
    agent_name: str = "Human Agent",
    automatic: bool = False,
):
    escalation_notice = "Conversation escalated"
    pause_reason = str(reason or "Conversation escalated to human. AI auto-response is paused.").strip()
    logger.warning(
        "Fallback escalation company_id=%s conversation_id=%s channel=%s automatic=%s reason=%s",
        company_id,
        convo_id,
        channel,
        automatic,
        pause_reason[:300],
    )
    logger.warning(
        "ai_auto_disabled company_id=%s conversation_id=%s trigger=escalation reason=escalation trigger_message_id=%s actor_type=%s message_type=%s media_type=%s detail=%s",
        company_id,
        convo_id,
        "",
        "system" if automatic else "agent",
        "",
        "",
        pause_reason[:300],
    )
    await db.execute(
        "UPDATE conversations SET ai_handled=FALSE,ai_auto_paused=TRUE,ai_paused_at=COALESCE(ai_paused_at,NOW()),"
        "ai_paused_reason=$1,ai_paused_error_type=COALESCE(NULLIF(ai_paused_error_type,''),'conversation_escalated'),"
        "ai_paused_scope='conversation',"
        "ai_disabled_until=NULL,status='escalated',escalation_notice=$2,escalated_at=NOW(),escalated_to=$3,escalated_to_name=$4,updated_at=NOW() "
        "WHERE id=$5",
        pause_reason[:500],
        escalation_notice,
        agent_id or "",
        agent_name or "Human Agent",
        convo_id,
    )
    if automatic:
        detail = reason or "AI responses paused. Human review is required."
        system_text = f"Conversation escalated. {detail}"
    else:
        system_text = f"Conversation escalated. {agent_name or 'Human Agent'} is now handling this chat."
    msg_id = make_id()
    await db.execute(
        "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,read,created_at) "
        "VALUES($1,$2,$3,$4,'system','system','System',FALSE,NOW())",
        msg_id,
        company_id or "",
        convo_id,
        system_text,
    )
    message = r(await db.fetchrow("SELECT * FROM messages WHERE id=$1", msg_id)) or {
        "id": msg_id,
        "company_id": company_id or "",
        "conversation_id": convo_id,
        "content": system_text,
        "sender_type": "system",
        "sender_id": "system",
        "sender_name": "System",
        "read": False,
        "created_at": datetime.now(timezone.utc),
    }
    conversation = r(await db.fetchrow("SELECT * FROM conversations WHERE id=$1", convo_id)) or {}
    logger.info(
        "Chat AI-disabled state changed company_id=%s conversation_id=%s ai_auto_paused=true status=escalated message_id=%s",
        company_id,
        convo_id,
        msg_id,
    )
    return {
        "conversation": conversation,
        "message": message,
        "escalation_notice": escalation_notice,
    }


async def ensure_setup_reminder_notifications(db, sio, connected_users, current_user: dict):
    if current_user.get("role") != "admin":
        return
    user = r(await db.fetchrow("SELECT * FROM users WHERE id=$1", current_user["sub"]))
    if not user:
        return
    ca = parse_dt(user.get("created_at"))
    if not ca or ca > (datetime.now(timezone.utc) - timedelta(minutes=ONBOARDING_REMINDER_DELAY_MINUTES)):
        return
    existing = {
        row["reminder_key"]
        for row in await db.fetch(
            "SELECT reminder_key FROM notifications WHERE user_id=$1 AND reminder_key=ANY($2)",
            user["id"],
            ["complete_settings", "add_first_agent"],
        )
        if row["reminder_key"]
    }
    company_profile = (
        r(
            await db.fetchrow(
                "SELECT c.name AS company_name, cs.industry, cs.website_address "
                "FROM companies c "
                "LEFT JOIN company_settings cs ON cs.company_id = c.id "
                "WHERE c.id=$1 LIMIT 1",
                user.get("company_id", ""),
            )
        )
        or {}
    )
    if (
        company_profile.get("company_name", "") in ("", "My Company")
        or not company_profile.get("industry")
        or not company_profile.get("website_address")
    ) and "complete_settings" not in existing:
        await create_notification(
            db,
            sio,
            connected_users,
            current_user,
            "Complete your settings",
            "Finish your personal and company settings.",
            "info",
            user["id"],
            "complete_settings",
            "/settings?tab=personal",
        )
    cid = current_user.get("company_id", "")
    if (
        cid
        and (await db.fetchval("SELECT COUNT(*) FROM users WHERE company_id=$1", cid) or 0) <= 1
        and "add_first_agent" not in existing
    ):
        await create_notification(
            db,
            sio,
            connected_users,
            current_user,
            "Add your first company agent",
            "Invite one from Teams settings.",
            "info",
            user["id"],
            "add_first_agent",
            "/settings?tab=users",
        )


def generate_reset_token() -> str:
    return secrets.token_urlsafe(24)


def generate_verification_fingerprint(length: int = 8) -> str:
    return "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(length))


async def create_email_verification_record(db, user: dict, request: Request) -> dict:
    from urllib.parse import urlencode

    email = (user.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "Email is required")
    token = create_access_token(
        {
            "sub": user["id"],
            "email": email,
            "role": user.get("role", "admin"),
            "company_id": user.get("company_id", ""),
            "purpose": "email_verification",
        },
        expires_delta=timedelta(minutes=EMAIL_VERIFICATION_TTL_MINUTES),
    )
    expires = datetime.now(timezone.utc) + timedelta(minutes=EMAIL_VERIFICATION_TTL_MINUTES)
    token_hash = hash_token(token)
    await set_company_context(db, user.get("company_id", ""))
    await db.execute(
        "UPDATE email_verifications SET used=TRUE WHERE user_id=$1 AND used=FALSE",
        user["id"],
    )
    await db.execute(
        "INSERT INTO email_verifications(id,user_id,company_id,email,token,used,expires_at,created_at) VALUES($1,$2,$3,$4,$5,FALSE,$6,NOW())",  # noqa: E501
        make_id(),
        user["id"],
        user.get("company_id", ""),
        email,
        token_hash,
        expires,
    )
    link = f"{resolve_frontend_base_url(request)}/verify-email?{urlencode({'token': token})}"
    return {
        "token": token,
        "expires_at": expires.isoformat(),
        "verify_link": link,
        "email": email,
    }


async def get_email_verification_resend_control(db, user: dict) -> dict:
    await set_company_context(db, user.get("company_id", ""))
    row = r(
        await db.fetchrow(
            "SELECT * FROM email_verification_resend_controls WHERE user_id=$1",
            user["id"],
        )
    )
    if not row:
        return {}
    lu = row.get("lock_until")
    if (
        lu
        and isinstance(lu, datetime)
        and lu.replace(tzinfo=timezone.utc if lu.tzinfo is None else lu.tzinfo) <= datetime.now(timezone.utc)
    ):
        await db.execute(
            "DELETE FROM email_verification_resend_controls WHERE user_id=$1",
            user["id"],
        )
        return {}
    return row


async def set_email_verification_resend_control(db, user: dict, count: int, avail=None, lock=None):
    await set_company_context(db, user.get("company_id", ""))
    await db.execute(
        "INSERT INTO email_verification_resend_controls(user_id,company_id,email,resend_count,resend_available_at,lock_until,updated_at) VALUES($1,$2,$3,$4,$5,$6,NOW()) ON CONFLICT(user_id) DO UPDATE SET company_id=EXCLUDED.company_id,email=EXCLUDED.email,resend_count=EXCLUDED.resend_count,resend_available_at=EXCLUDED.resend_available_at,lock_until=EXCLUDED.lock_until,updated_at=NOW()",  # noqa: E501
        user["id"],
        user.get("company_id", ""),
        (user.get("email") or "").strip().lower(),
        count,
        avail,
        lock,
    )


def build_email_verification_meta(
    verification: dict, count: int = 0, lock_until=None, resend_available_at=None
) -> dict:
    def secs(dt):
        if not dt:
            return 0
        if isinstance(dt, str):
            dt = parse_dt(dt)
        if not dt:
            return 0
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, int((dt - datetime.now(timezone.utc)).total_seconds()))

    locked = secs(lock_until)
    cooldown = 0 if locked else secs(resend_available_at)
    return {
        "required": True,
        "email": verification["email"],
        "expires_at": verification["expires_at"],
        "message": "Verification email sent. Please check your inbox.",
        "resend_available_in_seconds": cooldown or VERIFICATION_RESEND_COOLDOWN_SECONDS,
        "remaining_attempts": max(0, VERIFICATION_RESEND_MAX_ATTEMPTS - count),
        "locked": locked > 0,
        "lock_remaining_seconds": locked,
    }


async def send_email_verification_message(
    db,
    user: dict,
    request: Request,
    reason: str = "register",
    background_tasks: BackgroundTasks | None = None,
):
    ver = await create_email_verification_record(db, user, request)
    html = render_platform_email_html(
        title="Verify Your Email",
        intro=f"Hi {user.get('name') or 'there'}, welcome to Pulse Engine.",
        body_lines=[
            "Please verify your email address to activate your account.",
            f"This link expires in {EMAIL_VERIFICATION_TTL_MINUTES} minutes.",
            "If you did not create this account, ignore this email.",
        ],
        cta_label="Verify Email",
        cta_url=ver["verify_link"],
        footer_note="Automated security email from Pulse Engine.",
    )
    email_kwargs = {
        "to_email": ver["email"],
        "subject": "Verify your Pulse Engine email address",
        "body": f"Verify here: {ver['verify_link']}",
        "html_body": html,
        "raise_on_failure": False,
    }
    if background_tasks is not None:
        background_tasks.add_task(send_email_async, **email_kwargs)
    else:
        await send_email_async(**email_kwargs)
    now = datetime.now(timezone.utc)
    if reason == "register":
        avail = now + timedelta(seconds=VERIFICATION_RESEND_COOLDOWN_SECONDS)
        await set_email_verification_resend_control(db, user, 0, avail, None)
        return ver, build_email_verification_meta(ver, 0, resend_available_at=avail)
    ctrl = await get_email_verification_resend_control(db, user)
    count = int(ctrl.get("resend_count", 0)) + 1
    if count >= VERIFICATION_RESEND_MAX_ATTEMPTS:
        lock = now + timedelta(minutes=VERIFICATION_RESEND_LOCK_MINUTES)
        await set_email_verification_resend_control(db, user, count, lock, lock)
        meta = build_email_verification_meta(ver, count, lock_until=lock, resend_available_at=lock)
        meta["message"] = "You have reached the resend limit. Try again later."
        return ver, meta
    avail = now + timedelta(seconds=VERIFICATION_RESEND_COOLDOWN_SECONDS)
    await set_email_verification_resend_control(db, user, count, avail, None)
    return ver, build_email_verification_meta(ver, count, resend_available_at=avail)


async def ensure_company_defaults_for_oauth(db, email: str) -> str:
    cid = make_id()
    seed = (email.split("@")[0] if "@" in email else "").replace(".", " ").strip().title()
    name = f"{seed} Company" if seed else "My Company"
    await set_company_context(db, cid)
    await db.execute(
        "INSERT INTO companies(id,name,is_active,created_at,updated_at) VALUES($1,$2,TRUE,NOW(),NOW())",
        cid,
        name,
    )
    await db.execute(
        "INSERT INTO company_settings(id,company_id,ai_enabled,ai_use_conversation_engine,ai_confidence_threshold,auto_assign,active_llm_engine_id,created_at,updated_at) VALUES($1,$1,TRUE,TRUE,0.70,TRUE,'',NOW(),NOW())",  # noqa: E501
        cid,
    )
    return cid


async def _delete_company_workspace(db, company_id: str):
    if not company_id:
        return
    await set_company_context(db, company_id)
    archive_metadata = []
    for label, table_name in [
        ("users", "users"),
        ("customers", "customers"),
        ("messages", "messages"),
        ("conversations", "conversations"),
        ("tickets", "tickets"),
    ]:
        try:
            count = await db.fetchval(f"SELECT COUNT(*) FROM {table_name} WHERE company_id=$1", company_id)
            archive_metadata.append(f"{label}:{int(count or 0)}")
        except Exception:
            pass
    await db.execute(
        "INSERT INTO deleted_companies(id,created_at,deleted_at,metadata) "
        "SELECT id, created_at, NOW(), $2 FROM companies WHERE id=$1 "
        "ON CONFLICT (id) DO UPDATE SET deleted_at=EXCLUDED.deleted_at, metadata=EXCLUDED.metadata",
        company_id,
        ",".join(archive_metadata),
    )
    await db.execute("DELETE FROM companies WHERE id=$1", company_id)


async def delete_user_account_records(db, user: dict, delete_workspace: bool = False):
    uid = user.get("id", "")
    cid = user.get("company_id", "")
    if not uid:
        return
    if cid:
        try:
            await set_company_context(db, cid)
            await db.execute(
                "INSERT INTO deleted_accounts(id,deleted_user_id,company_id,name,email,deleted_at) VALUES($1,$2,$3,$4,$5,NOW())",  # noqa: E501
                make_id(),
                uid,
                cid,
                (user.get("name") or "").strip(),
                (user.get("email") or "").strip().lower(),
            )
        except Exception as exc:
            logger.warning("Failed to archive deleted account %s: %s", uid, exc)
    all_ids = (
        [dict(r)["id"] for r in await db.fetch("SELECT id FROM users WHERE company_id=$1", cid)]
        if (delete_workspace and cid)
        else [uid]
    )
    for u in all_ids:
        for tbl in [
            "sessions",
            "password_resets",
            "email_verifications",
            "account_deletion_verifications",
            "notifications",
            "security_events",
            "user_ai_memories",
            "email_verification_resend_controls",
        ]:
            await db.execute(f"DELETE FROM {tbl} WHERE user_id=$1", u)
        await db.execute("DELETE FROM api_keys WHERE created_by=$1", u)
        await db.execute("DELETE FROM templates WHERE created_by=$1", u)
        await db.execute("DELETE FROM user_oauth_providers WHERE user_id=$1", u)
        await db.execute(
            "DELETE FROM ai_agents WHERE company_id=$1 AND EXISTS (SELECT 1 FROM users WHERE id=$2 AND role='admin')",
            cid,
            u,
        ) if u == uid else None
    if delete_workspace and cid:
        await _delete_company_workspace(db, cid)
    else:
        await db.execute("DELETE FROM user_ai_memories WHERE user_id=$1", uid)
        await db.execute("DELETE FROM users WHERE id=$1", uid)
        if cid and not (await db.fetchval("SELECT COUNT(*) FROM users WHERE company_id=$1", cid) or 0):
            await _delete_company_workspace(db, cid)


async def refresh_conversation_rollup(db, convo_id: str):
    latest = await db.fetchrow(
        "SELECT content,created_at FROM messages WHERE conversation_id=$1 ORDER BY created_at DESC LIMIT 1",
        convo_id,
    )
    mc = await db.fetchval("SELECT COUNT(*) FROM messages WHERE conversation_id=$1", convo_id) or 0
    uc = (
        await db.fetchval(
            "SELECT COUNT(*) FROM messages WHERE conversation_id=$1 AND sender_type='customer' AND read=FALSE",
            convo_id,
        )
        or 0
    )
    await db.execute(
        "UPDATE conversations SET last_message=$1,last_message_at=$2,updated_at=NOW(),message_count=$3,unread_count=$4 WHERE id=$5",  # noqa: E501
        (dict(latest).get("content", "")[:100] if latest else ""),
        (dict(latest).get("created_at") if latest else datetime.now(timezone.utc)),
        mc,
        uc,
        convo_id,
    )


async def get_or_create_customer_from_contact(
    db,
    name: str,
    phone: str,
    current_user: dict,
    *,
    email: str = "",
    channel: str = "",
    channel_profile_id: str = "",
) -> dict:
    raw = (phone or "").strip()
    stored = await normalize_customer_contact_phone(db, current_user.get("company_id", ""), raw) if raw else ""
    normalized_email = (email or "").strip().lower()
    normalized_channel = str(channel or "").strip().lower()
    normalized_channel_profile_id = str(channel_profile_id or "").strip()
    cid = current_user.get("company_id", "")
    existing = await resolve_customer_by_contact(
        db,
        cid,
        phone=raw,
        email=normalized_email,
        channel=normalized_channel,
        channel_profile_id=normalized_channel_profile_id,
    )
    if existing:
        cust = dict(existing)
        updates = []
        args = []
        if name and cust.get("name") != name:
            updates.append(f"name=${len(args) + 1}")
            args.append(name)
        if normalized_email and not cust.get("email"):
            updates.append(f"email=${len(args) + 1}")
            args.append(normalized_email)
        if stored and _should_replace_customer_phone(cust.get("phone", ""), stored):
            updates.append(f"phone=${len(args) + 1}")
            args.append(stored)
        if updates:
            args.append(cust["id"])
            await db.execute(
                f"UPDATE customers SET {', '.join(updates)},updated_at=NOW() WHERE id=${len(args)}",
                *args,
            )
            cust = dict(await db.fetchrow("SELECT * FROM customers WHERE id=$1", cust["id"]))
        await upsert_customer_social_profile(db, cust["id"], normalized_channel, normalized_channel_profile_id)
        if normalized_channel:
            await db.execute(
                "INSERT INTO customer_channels(customer_id,channel) VALUES($1,$2) ON CONFLICT DO NOTHING",
                cust["id"],
                normalized_channel,
            )
        return cust
    nid = make_id()
    await db.execute(
        "INSERT INTO customers(id,company_id,name,email,phone,customer_company_name,segment,avatar,lifecycle_stage,lifetime_value,avg_sentiment,recent_tickets,complaint_count,days_since_last_contact,total_conversations,created_at,updated_at) VALUES($1,$2,$3,$4,$5,'','general','','lead',0,0,0,0,0,0,NOW(),NOW())",  # noqa: E501
        nid,
        cid,
        name or "Unknown",
        normalized_email,
        stored,
    )
    cust = dict(await db.fetchrow("SELECT * FROM customers WHERE id=$1", nid))
    await db.execute(
        "INSERT INTO customer_tags(customer_id,tag) VALUES($1,'profile-message') ON CONFLICT DO NOTHING",
        nid,
    )
    if normalized_channel:
        await db.execute(
            "INSERT INTO customer_channels(customer_id,channel) VALUES($1,$2) ON CONFLICT DO NOTHING",
            nid,
            normalized_channel,
        )
    await upsert_customer_social_profile(db, nid, normalized_channel, normalized_channel_profile_id)
    await ensure_customer_profile(db, cust)
    return cust


async def get_or_create_contact_conversation(
    db,
    customer: dict,
    channel: str,
    source: str,
    current_user: dict,
    *,
    channel_id: str = "",
) -> dict:
    cid = current_user.get("company_id", "")
    row = await db.fetchrow(
        "SELECT * FROM conversations WHERE customer_id=$1 AND channel=$2 AND status=ANY($3) AND company_id=$4 LIMIT 1",
        customer["id"],
        channel,
        ["open", "pending", "escalated"],
        cid,
    )
    if row:
        convo = dict(row)
        normalized_channel_id = str(channel_id or "").strip()
        if normalized_channel_id and convo.get("channel_id") != normalized_channel_id:
            await db.execute(
                "UPDATE conversations SET channel_id=$1,updated_at=NOW() WHERE id=$2 AND company_id=$3",
                normalized_channel_id,
                convo["id"],
                cid,
            )
            convo["channel_id"] = normalized_channel_id
        return convo
    nid = make_id()
    normalized_channel_id = str(channel_id or "").strip()
    await db.execute(
        "INSERT INTO conversations(id,company_id,customer_id,customer_name,customer_avatar,channel,channel_id,subject,status,priority,assigned_to,assigned_name,ai_handled,sentiment_score,sentiment_label,message_count,last_message,last_message_at,unread_count,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,'open','medium',$9,$10,TRUE,0,'neutral',0,'',NOW(),0,NOW(),NOW())",  # noqa: E501
        nid,
        cid,
        customer["id"],
        customer.get("name", "Unknown"),
        customer.get("avatar", ""),
        channel,
        normalized_channel_id,
        "Profile outreach",
        current_user.get("sub", ""),
        current_user.get("name", ""),
    )
    await db.execute(
        "INSERT INTO conversation_tags(conversation_id,tag) VALUES($1,$2) ON CONFLICT DO NOTHING",
        nid,
        source,
    )
    return dict(await db.fetchrow("SELECT * FROM conversations WHERE id=$1", nid))


async def convert_lead_to_customer_state(db, lead: dict, current_user: dict) -> dict:
    email = (lead.get("email") or "").strip().lower()
    _raw_phone = (lead.get("phone") or "").strip()
    phone = ""
    if _raw_phone:
        identity = normalize_whatsapp_phone(_raw_phone)
        phone = identity.canonical_value if identity.is_valid else (normalize_to_e164_digits(_raw_phone) or "")
    name = (lead.get("name") or "").strip() or "Unknown"
    cid = current_user.get("company_id", "")
    wp, args = [], [cid]
    if email:
        wp.append(f"email=${len(args) + 1}")
        args.append(email)
    if phone:
        wp.append(f"phone=${len(args) + 1}")
        args.append(phone)
    if _raw_phone and _raw_phone != phone:
        wp.append(f"phone=${len(args) + 1}")
        args.append(_raw_phone)
    existing = None
    if wp:
        existing = r(
            await db.fetchrow(
                f"SELECT * FROM customers WHERE company_id=$1 AND ({' OR '.join(wp)}) LIMIT 1",
                *args,
            )
        )
    if existing:
        await db.execute(
            "UPDATE customers SET name=$1,email=$2,phone=$3,customer_company_name=$4,"
            "lead_id=COALESCE(NULLIF($5,''),lead_id),"
            "lifecycle_stage='customer',updated_at=NOW() WHERE id=$6",
            name or existing.get("name", "Unknown"),
            email or existing.get("email", ""),
            phone or existing.get("phone", ""),
            lead.get("customer_company_name") or existing.get("customer_company_name", ""),
            lead.get("id", ""),
            existing["id"],
        )
        cust = r(await db.fetchrow("SELECT * FROM customers WHERE id=$1", existing["id"]))
    else:
        nid = make_id()
        await db.execute(
            "INSERT INTO customers(id,company_id,lead_id,name,email,phone,customer_company_name,"
            "segment,avatar,lifecycle_stage,lifetime_value,avg_sentiment,recent_tickets,complaint_count,"
            "days_since_last_contact,total_conversations,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,'general','','customer',0,0,0,0,0,0,NOW(),NOW())",
            nid,
            cid,
            lead.get("id", ""),
            name,
            email,
            phone,
            lead.get("customer_company_name", ""),
        )
        await db.execute(
            "INSERT INTO customer_tags(customer_id,tag) VALUES($1,'converted-from-lead') ON CONFLICT DO NOTHING",
            nid,
        )
        cust = r(await db.fetchrow("SELECT * FROM customers WHERE id=$1", nid))
    await ensure_customer_profile(db, cust)
    if email:
        await db.execute(
            "DELETE FROM leads WHERE company_id=$1 AND email=$2 AND id!=$3",
            cid,
            email,
            lead["id"],
        )
    if phone:
        await db.execute(
            "DELETE FROM leads WHERE company_id=$1 AND phone=$2 AND id!=$3",
            cid,
            phone,
            lead["id"],
        )
    return cust


async def get_sentiment_metrics(db, company_id: str = "") -> dict:
    from core.utils import sentiment_score_to_csat

    clause = (
        "WHERE company_id=$1 AND sentiment_score IS NOT NULL" if company_id else "WHERE sentiment_score IS NOT NULL"
    )
    args = [company_id] if company_id else []
    row = (
        r(
            await db.fetchrow(
                f"SELECT AVG(sentiment_score) AS avg_score,COUNT(*) AS total,SUM(CASE WHEN sentiment_score>0.3 THEN 1 ELSE 0 END) AS positive,SUM(CASE WHEN sentiment_score<-0.3 THEN 1 ELSE 0 END) AS negative,SUM(CASE WHEN sentiment_score>=0.35 THEN 1 ELSE 0 END) AS promoters,SUM(CASE WHEN sentiment_score<=-0.35 THEN 1 ELSE 0 END) AS detractors FROM messages {clause}",  # noqa: E501
                *args,
            )
        )
        or {}
    )
    total = int(row.get("total") or 0)
    avg = float(row.get("avg_score") or 0.0)
    pos = int(row.get("positive") or 0)
    neg = int(row.get("negative") or 0)
    prom = int(row.get("promoters") or 0)
    det = int(row.get("detractors") or 0)
    return {
        "total": total,
        "avg_score": round(avg, 2) if total else 0.0,
        "positive": pos,
        "neutral": max(total - pos - neg, 0),
        "negative": neg,
        "csat_score": sentiment_score_to_csat(avg, total),
        "nps_score": round(((prom - det) / total) * 100) if total else 0,
    }


async def get_average_response_minutes(db, company_id: str = "", assigned_to: Optional[str] = None) -> float:
    if company_id and assigned_to:
        rows = await db.fetch(
            "SELECT id FROM conversations WHERE company_id=$1 AND assigned_to=$2",
            company_id,
            assigned_to,
        )
    elif company_id:
        rows = await db.fetch("SELECT id FROM conversations WHERE company_id=$1", company_id)
    else:
        rows = await db.fetch("SELECT id FROM conversations")
    ids = [row["id"] for row in rows]
    if not ids:
        return 0.0
    msgs = await db.fetch(
        "SELECT conversation_id,sender_type,created_at FROM messages WHERE conversation_id=ANY($1) ORDER BY conversation_id,created_at",  # noqa: E501
        ids,
    )
    grouped: dict = {}
    for m in msgs:
        grouped.setdefault(m["conversation_id"], []).append(dict(m))
    minutes = []
    for convo_msgs in grouped.values():
        pending = None
        for m in convo_msgs:
            st = (m.get("sender_type") or "").lower()
            ca = m.get("created_at")
            if isinstance(ca, str):
                ca = parse_dt(ca)
            if not ca:
                continue
            if st == "customer":
                pending = pending or ca
                continue
            if pending is not None:
                minutes.append(max((ca - pending).total_seconds() / 60, 0))
                pending = None
    return round(sum(minutes) / len(minutes), 1) if minutes else 0.0


async def _bg_save_interaction_summary(
    db,
    company_id: str,
    customer_id: str,
    customer_name: str,
    conversation_id: str,
    messages: list,
):
    try:
        from services.ai_service.facade import summarize_customer_interaction

        cust = r(await db.fetchrow("SELECT * FROM customers WHERE id=$1", customer_id)) or {}
        data = await summarize_customer_interaction(messages, cust, db=db)
        today = datetime.now(timezone.utc).date()
        await db.execute(
            "INSERT INTO customer_interaction_summaries(id,company_id,customer_id,customer_name,conversation_id,summary_date,summary_text,total_messages,avg_sentiment,escalated,ai_handled,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,NOW())",  # noqa: E501
            make_id(),
            company_id,
            customer_id,
            customer_name,
            conversation_id,
            today,
            str(data.get("summary", "")),
            int(data.get("total_messages", len(messages))),
            float(data.get("avg_sentiment", 0)),
            bool(data.get("escalated", False)),
            bool(data.get("ai_handled", True)),
        )
    except Exception as e:
        logger.warning(f"_bg_save_interaction_summary failed: {e}")


async def ensure_whatsapp_lead(db, current_user: dict, name: str, phone: str):
    normalized = (phone or "").strip()
    cid = current_user.get("company_id", "")
    if not normalized:
        return None, False
    stage = await db.fetchval(
        "SELECT lifecycle_stage FROM customers WHERE phone=$1 AND company_id=$2 LIMIT 1",
        normalized,
        cid,
    )
    if stage == "customer":
        return None, False
    existing = r(
        await db.fetchrow(
            "SELECT * FROM leads WHERE phone=$1 AND company_id=$2 LIMIT 1",
            normalized,
            cid,
        )
    )
    if existing:
        return existing, False
    lid = make_id()
    await db.execute(
        "INSERT INTO leads(id,company_id,name,email,phone,customer_company_name,source,status,score,grade,phase,notes,assigned_to,assigned_name,created_at,updated_at) VALUES($1,$2,$3,'',$4,'','whatsapp','new',50,'warm','awareness','Auto-created from outbound WhatsApp',$5,$6,NOW(),NOW())",  # noqa: E501
        lid,
        cid,
        name or "Unknown",
        normalized,
        current_user.get("sub", ""),
        current_user.get("name", ""),
    )
    return r(await db.fetchrow("SELECT * FROM leads WHERE id=$1", lid)), True


# re-export for auth.py
def tenant_filter(current_user: dict) -> dict:
    """Kept for compatibility — use get_company_id() in new code."""
    cid = get_company_id(current_user)
    return {"company_id": cid} if cid else {}
