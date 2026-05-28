"""
services/oauth_service.py - Google + Facebook OAuth (PostgreSQL)
"""

import hashlib
import logging
import os
import secrets
import traceback
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import HTTPException, Request

from core.config import OAUTH_STATE_TTL_MINUTES
from core.request_helpers import (
    build_facebook_redirect_uri,
    build_google_redirect_uri,
    sanitize_frontend_origin,
)
from core.utils import make_id, now_ts
from models.reference_data import (
    ensure_company_reference_data,
    ensure_global_roles,
    resolve_role_id,
)
from services.billing_helpers import relaxed_billing_env
from services.db_helpers import (
    build_auth_payload,
    create_user_session,
    ensure_company_defaults_for_oauth,
    ensure_user_company_assignment,
    r,
    record_auth_event,
    record_system_log,
    set_company_context,
    set_public_auth_context,
)

logger = logging.getLogger(__name__)

_HTTP_CLIENT = httpx.AsyncClient(
    timeout=httpx.Timeout(20.0, connect=5.0, read=20.0, write=20.0, pool=5.0),
    limits=httpx.Limits(
        max_keepalive_connections=10,
        max_connections=20,
        keepalive_expiry=60.0,
    ),
)
AUTHENTICATION_FAILED_ERROR = "Authentication failed"

# Reserved `next` path for signup-page OAuth: exchange profile and prefill form (no session for new emails).
OAUTH_SIGNUP_PREFILL_NEXT_PATH = "/__signup_oauth_prefill__"
# Reserved `next` path for authenticated "link OAuth to existing account" flow.
OAUTH_LINK_ACCOUNT_NEXT_PATH = "/__oauth_link__"

# Email exists but this OAuth subject is not linked — user must sign in and link (Settings) or use password.
OAUTH_ACCOUNT_LINK_REQUIRED = "OAUTH_ACCOUNT_LINK_REQUIRED"


def _oauth_provider_error(provider: str, response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else {}
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("error_description") or "").strip()
        code = str(error.get("code") or error.get("type") or "").strip()
        return f"{provider} OAuth error status={response.status_code} code={code} message={message[:240]}"
    if isinstance(payload, dict):
        message = str(payload.get("error_description") or payload.get("error") or "").strip()
        return f"{provider} OAuth error status={response.status_code} message={message[:240]}"
    return f"{provider} OAuth error status={response.status_code}"


async def resolve_oauth_login_user(
    db,
    provider: str,
    email: str,
    provider_user_id: str,
) -> dict | None:
    """Case A: return user only when (provider, provider_user_id) is already linked. Case C: None.

    Case B (email exists but subject not linked): raises HTTPException 409 with OAUTH_ACCOUNT_LINK_REQUIRED.

    Ambiguous multi-tenant email without a unique linked subject: raises 409 with workspace message.
    """
    norm = email.strip().lower()
    if not norm:
        return None
    await set_public_auth_context(db, email=norm)
    await ensure_global_roles(db)

    pid = (provider_user_id or "").strip()
    if pid:
        row = await db.fetchrow(
            "SELECT u.* "
            "FROM user_oauth_providers up "
            "JOIN users u ON u.id=up.user_id "
            "WHERE up.provider=$1 AND up.provider_id=$2 "
            "ORDER BY up.created_at ASC "
            "LIMIT 1",
            provider,
            pid,
        )
        if row:
            return dict(row)

    matches = [
        dict(row)
        for row in await db.fetch(
            "SELECT u.* FROM users u WHERE LOWER(u.email)=LOWER($1) "
            "ORDER BY CASE WHEN LOWER(COALESCE(u.auth_provider, ''))=LOWER($2) THEN 0 ELSE 1 END, u.created_at ASC",
            norm,
            provider,
        )
    ]
    if not matches:
        return None

    _multi_workspace = HTTPException(
        409,
        "Multiple workspaces use this email. Sign in with email and link OAuth from the target workspace first.",  # noqa: E501
    )

    if not pid:
        if len(matches) > 1:
            raise _multi_workspace
        raise HTTPException(409, OAUTH_ACCOUNT_LINK_REQUIRED)

    # pid present but no global (provider, provider_id) row — never match by email alone.
    if len(matches) > 1:
        raise _multi_workspace
    raise HTTPException(409, OAUTH_ACCOUNT_LINK_REQUIRED)


async def find_existing_oauth_login_user(
    db,
    provider: str,
    email: str,
    provider_user_id: str,
) -> dict | None:
    """Deprecated name; use resolve_oauth_login_user."""
    return await resolve_oauth_login_user(db, provider, email, provider_user_id)


async def create_oauth_state(
    db,
    provider: str,
    next_path: str = "",
    frontend_origin: str = "",
    link_user_id: str | None = None,
) -> str:
    state = secrets.token_urlsafe(32)
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    expires = datetime.now(timezone.utc) + timedelta(minutes=OAUTH_STATE_TTL_MINUTES)
    lid = (link_user_id or "").strip() or None
    await db.execute(
        "INSERT INTO oauth_states(id,provider,state_hash,next_path,frontend_origin,link_user_id,used,expires_at,created_at)"
        " VALUES($1,$2,$3,$4,$5,$6,FALSE,$7,NOW())",
        make_id(),
        provider,
        state_hash,
        next_path if next_path.startswith("/") else "",
        sanitize_frontend_origin(frontend_origin),
        lid,
        expires,
    )
    return state


async def consume_oauth_state(db, provider: str, state: str) -> dict:
    if not state:
        raise HTTPException(400, "Missing OAuth state")
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    row = r(
        await db.fetchrow(
            "SELECT * FROM oauth_states WHERE provider=$1 AND state_hash=$2 AND used=FALSE LIMIT 1",
            provider,
            state_hash,
        )
    )
    if not row:
        raise HTTPException(400, "Invalid OAuth state")
    expires = row.get("expires_at")
    if isinstance(expires, datetime) and expires < datetime.now(timezone.utc):
        await db.execute(
            "UPDATE oauth_states SET used=TRUE,used_at=NOW(),used_reason='expired' WHERE id=$1",
            row["id"],
        )
        raise HTTPException(400, "OAuth state has expired")
    await db.execute("UPDATE oauth_states SET used=TRUE,used_at=NOW() WHERE id=$1", row["id"])
    return {
        "next": row.get("next_path", ""),
        "frontend_origin": sanitize_frontend_origin(row.get("frontend_origin", "")),
        "link_user_id": str(row.get("link_user_id") or "").strip(),
    }


async def apply_oauth_provider_link(
    db,
    provider: str,
    profile: dict,
    link_user_id: str,
) -> None:
    """Attach OAuth subject to an existing user after explicit link flow (authenticated)."""
    lid = (link_user_id or "").strip()
    if not lid:
        raise HTTPException(400, "Invalid link session")
    pid = (profile.get("provider_user_id") or "").strip()
    if not pid:
        raise HTTPException(400, "OAuth profile missing subject")
    email = (profile.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "OAuth profile missing email")

    user = r(await db.fetchrow("SELECT * FROM users WHERE id=$1", lid))
    if not user:
        raise HTTPException(400, "User not found")
    if str(user.get("role") or "").strip().lower() == "super_admin":
        raise HTTPException(400, "OAuth linking is not available for super admins")
    if (user.get("email") or "").strip().lower() != email:
        raise HTTPException(403, "OAuth email does not match this account")

    conflict = r(
        await db.fetchrow(
            "SELECT user_id FROM user_oauth_providers WHERE provider=$1 AND provider_id=$2 AND user_id <> $3 LIMIT 1",
            provider,
            pid,
            lid,
        )
    )
    if conflict:
        raise HTTPException(
            409,
            "This social account is already linked to another workspace user",
        )

    cid = (user.get("company_id") or "").strip()
    if not cid:
        raise HTTPException(400, "User has no company context")
    await set_company_context(db, cid)
    await set_public_auth_context(db, email=email)

    await db.execute(
        "INSERT INTO user_oauth_providers(id,company_id,user_id,provider,provider_id,created_at) "
        "VALUES($1,$2,$3,$4,$5,NOW()) "
        "ON CONFLICT (user_id, provider) DO UPDATE SET "
        "provider_id = EXCLUDED.provider_id, company_id = EXCLUDED.company_id",
        make_id(),
        cid,
        lid,
        provider,
        pid,
    )
    if not (user.get("auth_provider") or "").strip():
        await db.execute(
            "UPDATE users SET auth_provider=$1,updated_at=NOW() WHERE id=$2",
            provider,
            lid,
        )


async def upsert_oauth_user(
    db,
    provider: str,
    email: str,
    name: str,
    avatar: str,
    provider_user_id: str,
    provider_email_verified: bool | None = None,
) -> dict:
    norm = email.strip().lower()
    if not norm:
        raise HTTPException(401, AUTHENTICATION_FAILED_ERROR)

    user = await resolve_oauth_login_user(db, provider, norm, provider_user_id)
    if user:
        if not user.get("company_id"):
            company_id = await ensure_company_defaults_for_oauth(db, norm)
            await set_public_auth_context(db, email=norm)
            await db.execute(
                "UPDATE users SET company_id=$1,updated_at=NOW() WHERE id=$2",
                company_id,
                user["id"],
            )
            await ensure_company_reference_data(db, company_id)
            await set_company_context(db, company_id)
            user["company_id"] = company_id
        else:
            await set_company_context(db, user.get("company_id", ""))

        set_parts = []
        values = []
        set_parts.append(f"updated_at=${len(values) + 1}")
        values.append(now_ts())
        set_parts.append(f"last_login=${len(values) + 1}")
        values.append(now_ts())
        if provider_email_verified is True:
            set_parts.append(f"email_verified=${len(values) + 1}")
            values.append(True)
        if name and not (user.get("name") or "").strip():
            set_parts.append(f"name=${len(values) + 1}")
            values.append(name)
        if avatar:
            set_parts.append(f"avatar=${len(values) + 1}")
            values.append(avatar)
        if not (user.get("auth_provider") or "").strip():
            set_parts.append(f"auth_provider=${len(values) + 1}")
            values.append(provider)

        values.append(user["id"])
        await db.execute(
            f"UPDATE users SET {', '.join(set_parts)} WHERE id=${len(values)}",
            *values,
        )

        if provider_user_id:
            await db.execute(
                "INSERT INTO user_oauth_providers(id,company_id,user_id,provider,provider_id,created_at)"
                " VALUES($1,$2,$3,$4,$5,NOW()) ON CONFLICT(user_id,provider) DO UPDATE SET "
                "provider_id = EXCLUDED.provider_id, company_id = EXCLUDED.company_id",
                make_id(),
                user.get("company_id", ""),
                user["id"],
                provider,
                provider_user_id,
            )

        refreshed = r(await db.fetchrow("SELECT * FROM users WHERE id=$1", user["id"]))
        return await ensure_user_company_assignment(db, refreshed or user)

    if not relaxed_billing_env():
        raise HTTPException(
            403,
            "Complete the paid signup flow before using Google or Facebook sign-in for a new workspace.",
        )

    # DEMO_MODE / STRIPE_OPTIONAL: provision tenant + admin without Stripe checkout.
    await ensure_global_roles(db)
    company_id = await ensure_company_defaults_for_oauth(db, norm)
    await set_public_auth_context(db, email=norm)
    await ensure_company_reference_data(db, company_id)
    await set_company_context(db, company_id)
    role_id = await resolve_role_id(db, "admin")
    display = (name or "").strip() or (
        (norm.split("@", 1)[0] if "@" in norm else "").replace(".", " ").title() or "User"
    )
    user_id = make_id()
    initial_verified = provider_email_verified is True
    await db.execute(
        "INSERT INTO users("
        "id,email,password_hash,name,role,role_id,sub_role,status,avatar,company_id,phone,"
        "onboarding_completed,plan_selected,billing_status,auth_provider,email_verified,created_at,updated_at"
        ") VALUES ($1,$2,$3,$4,'admin',$5,'','pending_approval','',$6,'',"
        "FALSE,FALSE,'active',$7,$8,NOW(),NOW())",
        user_id,
        norm,
        "",
        display,
        role_id,
        company_id,
        provider,
        initial_verified,
    )
    if provider_user_id:
        await db.execute(
            "INSERT INTO user_oauth_providers(id,company_id,user_id,provider,provider_id,created_at) "
            "VALUES($1,$2,$3,$4,$5,NOW())",
            make_id(),
            company_id,
            user_id,
            provider,
            provider_user_id,
        )
    if avatar:
        await db.execute(
            "UPDATE users SET avatar=$1,updated_at=NOW() WHERE id=$2",
            avatar[:512],
            user_id,
        )
    refreshed = r(await db.fetchrow("SELECT * FROM users WHERE id=$1", user_id))
    logger.info(
        "oauth auto_provisioned user_id=%s company_id=%s email=%s provider=%s relaxed_billing=1",
        user_id,
        company_id,
        norm,
        provider,
    )
    logger.info(
        "user_pending_approval_created actor_user_id=%s target_user_id=%s company_id=%s previous_status=%s new_status=%s reason=%s",
        user_id,
        user_id,
        company_id,
        "",
        "pending_approval",
        f"oauth_{provider}",
    )
    return await ensure_user_company_assignment(db, refreshed or {})


async def exchange_google_code(code: str, request: Request) -> dict:
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        logger.error(
            "oauth config_missing provider=google client_id_present=%s client_secret_present=%s",
            bool(client_id),
            bool(client_secret),
        )
        raise HTTPException(501, "Google OAuth is not configured.")

    redirect_uri = build_google_redirect_uri(request)
    logger.info("oauth exchange provider=google redirect_uri=%s", redirect_uri)

    token_resp = await _HTTP_CLIENT.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    if token_resp.status_code >= 400:
        logger.error("oauth exchange_failed provider=google %s", _oauth_provider_error("google", token_resp))
        raise HTTPException(401, AUTHENTICATION_FAILED_ERROR)

    access_token = token_resp.json().get("access_token", "")
    if not access_token:
        raise HTTPException(401, AUTHENTICATION_FAILED_ERROR)

    profile_resp = await _HTTP_CLIENT.get(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    if profile_resp.status_code >= 400:
        logger.error("oauth profile_failed provider=google %s", _oauth_provider_error("google", profile_resp))
        raise HTTPException(401, AUTHENTICATION_FAILED_ERROR)

    profile = profile_resp.json()

    email = (profile.get("email") or "").strip().lower()
    if not email or profile.get("email_verified") is False:
        raise HTTPException(401, AUTHENTICATION_FAILED_ERROR)
    return {
        "email": email,
        "name": (profile.get("name") or "").strip(),
        "avatar": profile.get("picture", "") or "",
        "provider_user_id": profile.get("sub", "") or "",
        "email_verified": True,
    }


async def exchange_facebook_code(code: str, request: Request) -> dict:
    app_id = os.environ.get("FACEBOOK_APP_ID", "").strip()
    app_secret = os.environ.get("FACEBOOK_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        logger.error(
            "oauth config_missing provider=facebook app_id_present=%s app_secret_present=%s",
            bool(app_id),
            bool(app_secret),
        )
        raise HTTPException(501, "Facebook OAuth is not configured.")

    redirect_uri = build_facebook_redirect_uri(request)
    logger.info("oauth exchange provider=facebook redirect_uri=%s", redirect_uri)

    token_resp = await _HTTP_CLIENT.get(
        "https://graph.facebook.com/v21.0/oauth/access_token",
        params={
            "client_id": app_id,
            "client_secret": app_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=20,
    )
    if token_resp.status_code >= 400:
        logger.error(
            "oauth exchange_failed provider=facebook redirect_uri=%s %s",
            redirect_uri,
            _oauth_provider_error("facebook", token_resp),
        )
        raise HTTPException(401, AUTHENTICATION_FAILED_ERROR)

    try:
        token_payload = token_resp.json()
    except Exception as exc:
        logger.error("oauth exchange_failed provider=facebook reason=invalid_json error=%s", exc)
        raise HTTPException(401, AUTHENTICATION_FAILED_ERROR) from exc
    access_token = token_payload.get("access_token", "")
    if not access_token:
        logger.error("oauth exchange_failed provider=facebook reason=missing_access_token")
        raise HTTPException(401, AUTHENTICATION_FAILED_ERROR)

    profile_resp = await _HTTP_CLIENT.get(
        "https://graph.facebook.com/me",
        params={
            "fields": "id,name,email,picture.type(large)",
            "access_token": access_token,
        },
        timeout=20,
    )
    if profile_resp.status_code >= 400:
        logger.error("oauth profile_failed provider=facebook %s", _oauth_provider_error("facebook", profile_resp))
        raise HTTPException(401, AUTHENTICATION_FAILED_ERROR)

    profile = profile_resp.json()

    email = (profile.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(401, AUTHENTICATION_FAILED_ERROR)
    pic = profile.get("picture") or {}
    pic_url = (pic.get("data") or {}).get("url", "") if isinstance(pic, dict) else ""
    return {
        "email": email,
        "name": (profile.get("name") or "").strip(),
        "avatar": pic_url,
        "provider_user_id": profile.get("id", "") or "",
        "email_verified": False,
    }


async def complete_oauth_login(db, provider: str, profile: dict, request: Request):
    try:
        pev = profile.get("email_verified")
        provider_email_verified: bool | None = None if pev is None else bool(pev)
        user = await upsert_oauth_user(
            db,
            provider,
            profile["email"],
            profile.get("name", ""),
            profile.get("avatar", ""),
            profile.get("provider_user_id", ""),
            provider_email_verified,
        )
    except HTTPException:
        raise
    except Exception:
        logger.error("upsert_oauth_user failed: %s", traceback.format_exc())
        raise HTTPException(500, AUTHENTICATION_FAILED_ERROR)

    try:
        session_token = await create_user_session(db, user["id"], request)
    except Exception as exc:
        logger.error("create_user_session failed: %s", exc)
        raise HTTPException(500, AUTHENTICATION_FAILED_ERROR)

    try:
        await record_auth_event(
            db,
            user["id"],
            f"login_{provider}",
            request,
            True,
            user["email"],
        )
        await record_system_log(
            db,
            {"sub": user["id"], "company_id": user.get("company_id", "")},
            f"oauth_login_{provider}",
            "user",
            user["id"],
            {},
        )
    except Exception as exc:
        logger.warning("OAuth login logging failed (non-fatal): %s", exc)

    auth_payload = await build_auth_payload(db, user, request)
    logger.info(
        "auth login_success method=%s user_id=%s company_id=%s",
        provider,
        user["id"],
        user.get("company_id", ""),
    )
    return auth_payload, session_token


async def execute_oauth_flow(db, provider: str, code: str, request: Request):
    try:
        if provider == "google":
            profile = await exchange_google_code(code, request)
        elif provider == "facebook":
            profile = await exchange_facebook_code(code, request)
        else:
            raise HTTPException(400, "Unsupported OAuth provider")
    except HTTPException:
        raise
    except Exception:
        logger.error(
            "OAuth code exchange failed for %s: %s",
            provider,
            traceback.format_exc(),
        )
        raise HTTPException(500, AUTHENTICATION_FAILED_ERROR)

    return await complete_oauth_login(db, provider, profile, request)
