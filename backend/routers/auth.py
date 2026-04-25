"""routers/auth.py — Auth endpoints using PostgreSQL."""

import asyncio
import hashlib
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from shared.auth.jwt import (
    REFRESH_TOKEN_EXPIRE_DAYS,
    decode_token,
    hash_password,
    verify_password,
)
from core.request_helpers import (
    build_google_redirect_uri,
    build_oauth_redirect_url,
    build_facebook_redirect_uri,
    resolve_frontend_base_url,
    sanitize_frontend_origin,
)
from core.utils import make_id, validate_password, seconds_until
from models.reference_data import resolve_role_id
from services.billing_helpers import (
    PLAN_CATALOG,
    assert_stripe_ready,
    assert_workspace_seat_available,
    count_pending_invitations,
    count_workspace_seats_used,
    effective_max_users,
    get_or_create_billing_customer,
    relaxed_billing_env,
    stripe_configured,
    stripe_enabled_for_app,
    stripe_price_id,
    trial_period_days,
    update_billing_customer_status,
    upsert_subscription,
    uses_local_billing_customer_id,
)
from services.db_helpers import (
    bump_user_token_version,
    build_auth_payload,
    close_login_sessions,
    create_user_session,
    delete_user_account_records,
    ensure_user_company_assignment,
    get_current_user_flexible,
    record_auth_event,
    record_system_log,
    require_roles,
    resolve_refresh_token_rotation,
    revoke_refresh_tokens_for_user,
    send_email_verification_message,
    hash_token,
    token_candidates,
    get_email_verification_resend_control,
    generate_reset_token,
    generate_verification_fingerprint,
    r,
    rs,
    get_company_id,
    set_company_context,
    set_public_auth_context,
)
from services.email_service import render_platform_email_html, send_email_async
from services.oauth_service import (
    OAUTH_ACCOUNT_LINK_REQUIRED,
    OAUTH_LINK_ACCOUNT_NEXT_PATH,
    OAUTH_SIGNUP_PREFILL_NEXT_PATH,
    apply_oauth_provider_link,
    complete_oauth_login,
    consume_oauth_state,
    create_oauth_state,
    exchange_facebook_code,
    exchange_google_code,
    execute_oauth_flow,
    resolve_oauth_login_user,
)
from services.public_signup_service import (
    get_public_registration_status,
    prepare_public_registration,
)
from core.config import (
    PASSWORD_RESET_TTL_MINUTES,
    ACCOUNT_DELETION_TTL_MINUTES,
    VERIFICATION_RESEND_COOLDOWN_SECONDS,
    VERIFICATION_RESEND_MAX_ATTEMPTS,
)

logger = logging.getLogger(__name__)
router = APIRouter()
AUTHENTICATION_FAILED_ERROR = "Authentication failed"
REFRESH_COOKIE_NAME = "pe_refresh"
INVITATION_TTL_HOURS = max(1, int(os.environ.get("INVITATION_TTL_HOURS", "72") or 72))

try:
    import stripe
except Exception:  # pragma: no cover
    stripe = None


def _signup_prefill_frontend_base(request: Request, sd: dict) -> str:
    raw = (sd.get("frontend_origin") or "").strip()
    base = sanitize_frontend_origin(raw) if raw else ""
    return (base or resolve_frontend_base_url(request)).rstrip("/")


def _signup_prefill_error_redirect(request: Request, sd: dict) -> RedirectResponse:
    return RedirectResponse(
        f"{_signup_prefill_frontend_base(request, sd)}/signup?oauth_error=1",
        302,
    )


def _signup_prefill_workspace_redirect(request: Request, sd: dict) -> RedirectResponse:
    return RedirectResponse(
        f"{_signup_prefill_frontend_base(request, sd)}/signup?oauth_error=workspace",
        302,
    )


def _signup_prefill_link_required_redirect(request: Request, sd: dict) -> RedirectResponse:
    return RedirectResponse(
        f"{_signup_prefill_frontend_base(request, sd)}/signup?oauth_error=link_required",
        302,
    )


def _oauth_requires_paid_signup_first(exc: HTTPException) -> bool:
    if exc.status_code != 403:
        return False
    d = (str(exc.detail) or "").lower()
    return "paid signup" in d or "new workspace" in d


def _signup_prefill_success_redirect(request: Request, sd: dict, profile: dict, provider_key: str) -> RedirectResponse:
    email = (profile.get("email") or "").strip()[:254]
    name = (profile.get("name") or "").strip()[:200]
    avatar = (profile.get("avatar") or "").strip()[:512]
    params = {"oauth": "1", "email": email, "name": name, "provider": provider_key}
    if avatar:
        params["avatar"] = avatar
    return RedirectResponse(
        f"{_signup_prefill_frontend_base(request, sd)}/signup?{urlencode(params)}",
        302,
    )


def _db(req):
    return req.app.state.db


def _generate_temporary_password() -> str:
    token = secrets.token_urlsafe(9)
    return f"Tmp!{token}9a"


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def _auth_failed(status_code: int = 401) -> HTTPException:
    return HTTPException(status_code, AUTHENTICATION_FAILED_ERROR)


async def _record_auth_event_safe(db, user_id: str, event_type: str, request: Request, success: bool, email: str = "") -> None:
    try:
        await record_auth_event(db, user_id, event_type, request, success, email)
    except Exception as exc:
        logger.warning("auth event logging skipped event=%s user_id=%s success=%s error=%s", event_type, user_id, success, exc)


def _refresh_cookie_secure(request: Request) -> bool:
    forwarded_proto = (request.headers.get("x-forwarded-proto", "") or request.url.scheme).split(",")[0].strip().lower()
    return forwarded_proto == "https"


def _set_refresh_cookie(response: JSONResponse, request: Request, refresh_token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=_refresh_cookie_secure(request),
        samesite="lax",
        path="/api",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
    )


def _clear_refresh_cookie(response: JSONResponse, request: Request) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path="/api",
        samesite="lax",
        secure=_refresh_cookie_secure(request),
        httponly=True,
    )


def _auth_response(payload: dict, request: Request, status_code: int = 200) -> JSONResponse:
    content = dict(payload)
    refresh_token = str(content.pop("refresh_token", "") or "").strip()
    response = JSONResponse(status_code=status_code, content=content)
    response.headers["Cache-Control"] = "no-store"
    if refresh_token:
        _set_refresh_cookie(response, request, refresh_token)
    return response


def _workspace_hint_from_body(body: dict) -> str:
    return (body.get("workspace", "") or body.get("company_id", "") or body.get("tenant_id", "") or "").strip()


async def _list_users_by_email(db, email: str, *, workspace: str = "") -> list[dict]:
    norm = (email or "").strip().lower()
    if not norm:
        return []
    if workspace:
        rows = await db.fetch(
            "SELECT u.*, COALESCE(c.name, '') AS company_name "
            "FROM users u "
            "LEFT JOIN companies c ON c.id=u.company_id "
            "WHERE LOWER(u.email)=LOWER($1) "
            "  AND (LOWER(u.company_id)=LOWER($2) OR LOWER(COALESCE(c.name, ''))=LOWER($2)) "
            "ORDER BY u.created_at ASC",
            norm,
            workspace.strip(),
        )
        return rs(rows)
    rows = await db.fetch(
        "SELECT u.*, COALESCE(c.name, '') AS company_name "
        "FROM users u "
        "LEFT JOIN companies c ON c.id=u.company_id "
        "WHERE LOWER(u.email)=LOWER($1) "
        "ORDER BY u.created_at ASC",
        norm,
    )
    return rs(rows)


def _workspace_candidates(users: list[dict]) -> list[dict]:
    seen: set[str] = set()
    workspaces: list[dict] = []
    for user in users:
        company_id = (user.get("company_id", "") or "").strip()
        company_name = (user.get("company_name", "") or "").strip()
        if not company_id or company_id in seen:
            continue
        seen.add(company_id)
        workspaces.append(
            {
                "company_id": company_id,
                "company_name": company_name,
                "label": company_name or company_id,
            }
        )
    return workspaces


async def _issue_password_reset_email(db, user: dict, request: Request) -> None:
    norm = (user.get("email", "") or "").strip().lower()
    token = generate_reset_token()
    expires = datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_TTL_MINUTES)
    token_hash = hash_token(token)
    await db.execute(
        "INSERT INTO password_resets(id,user_id,company_id,email,token,used,expires_at,created_at) VALUES($1,$2,$3,$4,$5,FALSE,$6,NOW())",  # noqa: E501
        make_id(),
        user["id"],
        user.get("company_id", ""),
        norm,
        token_hash,
        expires,
    )
    link = f"{resolve_frontend_base_url(request)}/signin#{urlencode({'mode': 'reset', 'token': token})}"
    html = render_platform_email_html(
        title="Reset Your Password",
        intro=f"Hi {user.get('name') or 'there'}, we received a request to reset your Pulse Engine password.",
        body_lines=[
            "Use the button below to set a new password.",
            f"This link expires in {PASSWORD_RESET_TTL_MINUTES} minutes.",
        ],
        cta_label="Reset Password",
        cta_url=link,
        accent="#059669",
    )
    try:
        await send_email_async(
            to_email=norm,
            subject="Reset your Pulse Engine password",
            body=link,
            html_body=html,
        )
    except Exception as e:
        logger.error("Password reset email failed: %s", e)
        raise HTTPException(500, "Failed to send password reset email") from e


@router.post("/auth/register")
async def register(request: Request):
    db = _db(request)
    body = await _json_body(request)
    return await prepare_public_registration(db, request, body)


@router.get("/auth/signup-billing-info")
async def signup_billing_info():
    """Public copy for signup UI: Stripe vs offline trial (no auth)."""
    relaxed = relaxed_billing_env()
    stripe_checkout = bool(stripe_enabled_for_app())
    days = trial_period_days()
    if relaxed:
        msg = (
            "Demo or optional-Stripe mode is on: you can complete self-serve signup without Stripe checkout. "
            "After email verification you will finish onboarding (company details) before using the app."
        )
        mode = "trial_relaxed"
    elif stripe_checkout:
        msg = "After you submit the form, you will complete payment on Stripe’s secure checkout."
        mode = "stripe"
    else:
        msg = f"No card required. You get a {days}-day trial on your selected plan when you complete signup."
        mode = "trial_no_payment"
    return {
        "stripe_checkout": stripe_checkout and not relaxed,
        "trial_days": days,
        "billing_mode": mode,
        "trial_mode_message": msg,
    }


@router.get("/auth/register/status")
async def register_status(request: Request, session_id: str = Query("", min_length=1)):
    db = _db(request)
    return await get_public_registration_status(db, session_id)


@router.post("/auth/login")
async def login(request: Request):
    db = _db(request)
    body = await _json_body(request)
    email = (body.get("email", "") or "").strip().lower()
    password = body.get("password", "")
    workspace = _workspace_hint_from_body(body)
    if not email:
        raise HTTPException(400, "Email is required")
    if not isinstance(password, str) or not password:
        raise HTTPException(400, "Password is required")
    try:
        await set_public_auth_context(db, email=email)
        users = await _list_users_by_email(db, email, workspace=workspace)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("auth login_failed method=email reason=db_lookup_error email=%s", email)
        raise HTTPException(500, "Unable to sign in right now. Please try again.") from exc
    if not users:
        await _record_auth_event_safe(db, "", "login", request, False, email)
        logger.warning("auth login_failed method=email reason=user_not_found")
        raise _auth_failed()
    password_matches = [
        user
        for user in users
        if user.get("password_hash") and verify_password(password, user["password_hash"])
    ]
    if not password_matches:
        await _record_auth_event_safe(
            db,
            users[0].get("id", ""),
            "login",
            request,
            False,
            email,
        )
        logger.warning("auth login_failed method=email reason=password_auth_unavailable")
        raise _auth_failed()
    if len(password_matches) > 1:
        await _record_auth_event_safe(
            db,
            password_matches[0].get("id", ""),
            "login",
            request,
            False,
            email,
        )
        logger.warning("auth login_conflict method=email reason=workspace_required")
        raise HTTPException(
            409,
            {
                "code": "workspace_required",
                "message": "Multiple workspaces match this email. Provide your workspace name or id to continue.",
                "workspaces": _workspace_candidates(password_matches),
            },
        )
    user = password_matches[0]
    user = await ensure_user_company_assignment(db, user)
    if user.get("role") == "super_admin" and not relaxed_billing_env():
        logger.warning("auth login_failed method=email reason=super_admin_requires_admin_login")
        return JSONResponse(
            status_code=409,
            content={
                "code": "admin_login_required",
                "detail": "Super admins must sign in through /admin/login",
            },
        )
    try:
        await db.execute("UPDATE users SET last_login=NOW(),updated_at=NOW() WHERE id=$1", user["id"])
        await create_user_session(db, user["id"], request)
        await _record_auth_event_safe(db, user["id"], "login", request, True, user["email"])
        logger.info(
            "auth login_success method=email user_id=%s company_id=%s email_verified=%s",
            user["id"],
            user.get("company_id", ""),
            bool(user.get("email_verified")),
        )
        payload = await build_auth_payload(db, user, request)
        if (
            not payload.get("user", {}).get("email_verified", False)
            and not relaxed_billing_env()
            and payload.get("user", {}).get("role") != "super_admin"
        ):
            payload["email_verification"] = {
                "required": True,
                "email": payload.get("user", {}).get("email", email),
                "message": "Email verification is required before using the workspace.",
            }
        return _auth_response(payload, request)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "auth login_failed method=email reason=session_or_payload_error user_id=%s company_id=%s",
            user.get("id", ""),
            user.get("company_id", ""),
        )
        raise HTTPException(500, "Unable to complete sign in right now. Please try again.") from exc


@router.post("/auth/logout")
async def logout(request: Request):
    db = _db(request)
    refresh_token = (request.cookies.get(REFRESH_COOKIE_NAME, "") or "").strip()
    try:
        current_user = await get_current_user_flexible(request)
        if current_user and current_user.get("sub"):
            await close_login_sessions(db, current_user["sub"])
            await revoke_refresh_tokens_for_user(db, current_user["sub"], "logout")
            await record_auth_event(
                db,
                current_user["sub"],
                "logout",
                request,
                True,
                "",
            )
    except Exception:
        current_user = {}
    if not current_user.get("sub") and refresh_token:
        payload = decode_token(refresh_token)
        if payload and payload.get("sub"):
            try:
                await close_login_sessions(db, payload["sub"])
                await revoke_refresh_tokens_for_user(db, payload["sub"], "logout")
            except Exception:
                pass
    response = JSONResponse({"status": "logged_out"})
    response.headers["Cache-Control"] = "no-store"
    _clear_refresh_cookie(response, request)
    return response


@router.post("/auth/refresh")
async def refresh_token(request: Request):
    db = _db(request)
    body = await _json_body(request)
    refresh_value = (body.get("refresh_token", "") or request.cookies.get(REFRESH_COOKIE_NAME, "") or "").strip()
    if not refresh_value:
        raise _auth_failed()
    return _auth_response(await resolve_refresh_token_rotation(db, refresh_value, request), request)


@router.post("/auth/forgot-password")
async def forgot_password(request: Request):
    db = _db(request)
    body = await _json_body(request)
    norm = (body.get("email", "") or "").strip().lower()
    workspace = _workspace_hint_from_body(body)
    await set_public_auth_context(db, email=norm)
    users = await _list_users_by_email(db, norm, workspace=workspace)
    if not users:
        return {"message": "If the email exists, a password reset email has been sent."}
    if workspace or len(users) == 1:
        user = await ensure_user_company_assignment(db, users[0])
        await set_company_context(db, user.get("company_id", ""))
        prov = (user.get("auth_provider") or "").lower()
        if prov in ("google", "facebook"):
            raise HTTPException(400, f"{prov.capitalize()} sign-in users cannot reset password via email.")
        await _issue_password_reset_email(db, user, request)
        return {"message": "If the email exists, a password reset email has been sent."}
    for user in users:
        user = await ensure_user_company_assignment(db, user)
        prov = (user.get("auth_provider") or "").lower()
        if prov in ("google", "facebook"):
            continue
        await set_company_context(db, user.get("company_id", ""))
        try:
            await _issue_password_reset_email(db, user, request)
        except HTTPException:
            logger.warning(
                "Password reset email failed for workspace company_id=%s",
                user.get("company_id", ""),
            )
    return {"message": "If the email exists, a password reset email has been sent."}


@router.post("/auth/reset-password")
async def reset_password(request: Request):
    db = _db(request)
    body = await _json_body(request)
    token = body.get("token", "")
    new_password = body.get("new_password", "")
    await set_public_auth_context(db, token=token)
    raw_token, token_hash = token_candidates(token)
    row = r(
        await db.fetchrow(
            "SELECT * FROM password_resets WHERE (token=$1 OR token=$2) AND used=FALSE LIMIT 1",
            raw_token,
            token_hash,
        )
    )
    if not row:
        raise HTTPException(400, "Invalid or expired reset token")
    await set_company_context(db, row.get("company_id", ""))
    exp = row.get("expires_at")
    if isinstance(exp, datetime) and exp < datetime.now(timezone.utc):
        raise HTTPException(400, "Reset token has expired")
    is_valid, errors = validate_password(new_password)
    if not is_valid:
        raise HTTPException(400, "; ".join(errors))
    await db.execute(
        "UPDATE users SET password_hash=$1,updated_at=NOW() WHERE id=$2",
        hash_password(new_password),
        row["user_id"],
    )
    await bump_user_token_version(db, row["user_id"])
    await revoke_refresh_tokens_for_user(db, row["user_id"], "password_reset")
    await db.execute("UPDATE password_resets SET used=TRUE WHERE token=$1 OR token=$2", raw_token, token_hash)
    return {
        "status": "password_reset",
        "message": "Password has been reset successfully",
    }


@router.get("/auth/verify-email")
async def verify_email(request: Request, token: str):
    db = _db(request)
    token = (token or "").strip()
    if not token:
        raise HTTPException(400, "Verification token is required")
    await set_public_auth_context(db, token=token)
    raw_token, token_hash = token_candidates(token)
    row = r(
        await db.fetchrow(
            "SELECT * FROM email_verifications WHERE (token=$1 OR token=$2) AND used=FALSE LIMIT 1",
            raw_token,
            token_hash,
        )
    )
    if not row:
        decoded = decode_token(token)
        if decoded and decoded.get("sub"):
            user = r(await db.fetchrow("SELECT * FROM users WHERE id=$1 LIMIT 1", decoded["sub"]))
            if user and user.get("email_verified", False):
                user = await ensure_user_company_assignment(db, user)
                await set_company_context(db, user.get("company_id", ""))
                await create_user_session(db, user["id"], request)
                payload = await build_auth_payload(db, user, request)
                payload["message"] = (
                    "Email already verified. Continue with company setup before inviting your first teammate."
                )
                payload["next_path"] = "/onboarding" if user.get("onboarding_completed") is False else "/dashboard"
                return _auth_response(payload, request)
        raise HTTPException(400, "Invalid or expired verification token")
    exp = row.get("expires_at")
    if isinstance(exp, datetime) and exp < datetime.now(timezone.utc):
        raise HTTPException(400, "Verification token has expired")
    await set_company_context(db, row.get("company_id", ""))
    await db.execute(
        "UPDATE users SET email_verified=TRUE,updated_at=NOW() WHERE id=$1",
        row["user_id"],
    )
    await db.execute("UPDATE email_verifications SET used=TRUE WHERE token=$1 OR token=$2", raw_token, token_hash)
    await db.execute(
        "DELETE FROM email_verification_resend_controls WHERE user_id=$1",
        row["user_id"],
    )
    user = r(await db.fetchrow("SELECT * FROM users WHERE id=$1 LIMIT 1", row["user_id"]))
    if not user:
        raise HTTPException(404, "User not found")
    user = await ensure_user_company_assignment(db, user)
    await create_user_session(db, user["id"], request)
    await record_auth_event(
        db,
        user["id"],
        "verify_email",
        request,
        True,
        user.get("email", ""),
    )
    payload = await build_auth_payload(db, user, request)
    payload["message"] = (
        "Email verified successfully. Continue with company setup before inviting your first teammate."
    )
    payload["next_path"] = "/onboarding"
    return _auth_response(payload, request)


@router.post("/auth/resend-verification")
async def resend_verification(request: Request):
    db = _db(request)
    body = await _json_body(request)
    norm = (body.get("email", "") or "").strip().lower()
    workspace = _workspace_hint_from_body(body)
    await set_public_auth_context(db, email=norm)
    users = await _list_users_by_email(db, norm, workspace=workspace)
    if not users:
        return {"message": "If the email exists, a verification email has been sent."}
    if workspace or len(users) == 1:
        user = await ensure_user_company_assignment(db, users[0])
        await set_company_context(db, user.get("company_id", ""))
        if user.get("email_verified", False):
            return {"message": "This email address is already verified."}
        ctrl = await get_email_verification_resend_control(db, user)
        lu = ctrl.get("lock_until")
        avail = ctrl.get("resend_available_at")
        lock_secs = seconds_until(lu) if lu else 0
        if lock_secs > 0:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many resend attempts.",
                    "retry_after_seconds": lock_secs,
                    "remaining_attempts": 0,
                    "locked": True,
                },
            )
        wait_secs = seconds_until(avail) if avail else 0
        if wait_secs > 0:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Please wait before requesting another verification email.",
                    "retry_after_seconds": wait_secs,
                    "locked": False,
                },
            )
        try:
            _, meta = await send_email_verification_message(db, user, request, "resend")
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Resend verification failed: %s", e)
            raise HTTPException(500, "Failed to send verification email")
        return {
            "message": meta.get("message") or "Verification email sent.",
            "resend_available_in_seconds": meta.get(
                "resend_available_in_seconds", VERIFICATION_RESEND_COOLDOWN_SECONDS
            ),
            "remaining_attempts": meta.get("remaining_attempts", VERIFICATION_RESEND_MAX_ATTEMPTS),
            "locked": meta.get("locked", False),
            "lock_remaining_seconds": meta.get("lock_remaining_seconds", 0),
        }
    for user in users:
        user = await ensure_user_company_assignment(db, user)
        await set_company_context(db, user.get("company_id", ""))
        if user.get("email_verified", False):
            continue
        ctrl = await get_email_verification_resend_control(db, user)
        lu = ctrl.get("lock_until")
        avail = ctrl.get("resend_available_at")
        if (lu and seconds_until(lu) > 0) or (avail and seconds_until(avail) > 0):
            continue
        try:
            await send_email_verification_message(db, user, request, "resend")
        except HTTPException:
            logger.warning(
                "Verification resend skipped for workspace company_id=%s",
                user.get("company_id", ""),
            )
        except Exception as e:
            logger.error("Resend verification failed: %s", e)
    return {"message": "If the email exists, a verification email has been sent."}


@router.get("/auth/reset-tokens")
async def list_reset_tokens(request: Request):
    db = _db(request)
    current_user = await require_roles(request, ["admin", "super_admin"])
    company_id = get_company_id(current_user)
    now = datetime.now(timezone.utc)

    if (current_user.get("role") or "").strip().lower() == "super_admin" and not company_id:
        rows = rs(
            await db.fetch(
                "SELECT id,user_id,company_id,email,used,expires_at,created_at "
                "FROM password_resets WHERE used=FALSE "
                "ORDER BY created_at DESC LIMIT 50"
            )
        )
    else:
        rows = rs(
            await db.fetch(
                "SELECT id,user_id,company_id,email,used,expires_at,created_at "
                "FROM password_resets WHERE used=FALSE AND company_id=$1 "
                "ORDER BY created_at DESC LIMIT 50",
                company_id,
            )
        )

    valid_tokens = []
    for row in rows:
        exp = row.get("expires_at")
        if isinstance(exp, datetime):
            exp_tz = exp.replace(tzinfo=timezone.utc) if exp.tzinfo is None else exp
            if exp_tz > now:
                valid_tokens.append(row)
    return valid_tokens


@router.api_route("/auth/session", methods=["GET", "POST"])
async def auth_session(request: Request):
    db = _db(request)
    body = await _json_body(request)
    sid = (body.get("session_id", "") or "").strip()
    if sid:
        await set_public_auth_context(db, session_token=sid)
        raw_sid, sid_hash = token_candidates(sid)
        session = r(
            await db.fetchrow(
                "SELECT * FROM sessions WHERE (session_token=$1 OR session_token=$2) AND is_active=TRUE LIMIT 1",
                raw_sid,
                sid_hash,
            )
        )
        if not session:
            raise _auth_failed()
        await set_company_context(db, session.get("company_id", ""))
        exp = session.get("expires_at")
        if isinstance(exp, datetime) and exp < datetime.now(timezone.utc):
            raise _auth_failed()
        user = r(
            await db.fetchrow(
                "SELECT * FROM users WHERE id=$1 LIMIT 1",
                session["user_id"],
            )
        )
        if not user:
            raise _auth_failed()
        user = await ensure_user_company_assignment(db, user)
        logger.info(
            "auth session_resolved via=session user_id=%s company_id=%s",
            user["id"],
            user.get("company_id", ""),
        )
        return _auth_response(await build_auth_payload(db, user, request), request)

    try:
        current_user = await get_current_user_flexible(request)
    except HTTPException:
        current_user = {}
    if not current_user.get("sub"):
        raise _auth_failed()
    user = r(
        await db.fetchrow(
            "SELECT * FROM users WHERE id=$1 LIMIT 1",
            current_user["sub"],
        )
    )
    if not user:
        raise _auth_failed()
    user = await ensure_user_company_assignment(db, user)
    logger.info(
        "auth session_resolved via=jwt user_id=%s company_id=%s",
        user["id"],
        user.get("company_id", ""),
    )
    return _auth_response(await build_auth_payload(db, user, request), request)


@router.get("/auth/google")
async def auth_google(
    request: Request,
    next_path: str = Query("", alias="next"),
    frontend_origin: str = Query("", alias="frontend_origin"),
):
    db = _db(request)
    if not os.environ.get("GOOGLE_CLIENT_ID") or not os.environ.get("GOOGLE_CLIENT_SECRET"):
        raise HTTPException(501, "Google OAuth is not configured.")

    state = await create_oauth_state(db, "google", next_path, frontend_origin)
    params = {
        "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
        "redirect_uri": build_google_redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "select_account",
    }
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}", 302)


@router.post("/auth/oauth/link/google/start")
async def oauth_link_google_start(request: Request):
    db = _db(request)
    current = await get_current_user_flexible(request)
    if not current.get("sub"):
        raise HTTPException(401, AUTHENTICATION_FAILED_ERROR)
    if str(current.get("role") or "").strip().lower() == "super_admin":
        raise HTTPException(400, "OAuth linking is not available for super admins")
    if not os.environ.get("GOOGLE_CLIENT_ID") or not os.environ.get("GOOGLE_CLIENT_SECRET"):
        raise HTTPException(501, "Google OAuth is not configured.")
    origin = sanitize_frontend_origin(request.headers.get("origin", ""))
    state = await create_oauth_state(
        db,
        "google",
        OAUTH_LINK_ACCOUNT_NEXT_PATH,
        origin,
        link_user_id=str(current["sub"]),
    )
    params = {
        "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
        "redirect_uri": build_google_redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "select_account",
    }
    return {"authorization_url": f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"}


@router.get("/auth/google/callback")
async def google_callback_get(
    request: Request,
    code: str = Query(""),
    state: str = Query(""),
    error: str = Query(""),
    error_description: str = Query(""),
):
    db = _db(request)
    sd = {"next": "", "frontend_origin": "", "link_user_id": ""}
    if state:
        try:
            sd = await consume_oauth_state(db, "google", state)
        except HTTPException:
            pass
    signup_prefill = (sd.get("next") or "") == OAUTH_SIGNUP_PREFILL_NEXT_PATH
    oauth_link = (sd.get("next") or "") == OAUTH_LINK_ACCOUNT_NEXT_PATH
    link_uid = (sd.get("link_user_id") or "").strip()
    if oauth_link and not link_uid:
        base = _signup_prefill_frontend_base(request, sd)
        return RedirectResponse(f"{base}/settings?tab=security&oauth_link_error=1", 302)

    if error or not code:
        if oauth_link and link_uid:
            base = _signup_prefill_frontend_base(request, sd)
            q = "oauth_link_error=cancelled" if error else "oauth_link_error=missing_code"
            return RedirectResponse(f"{base}/settings?tab=security&{q}", 302)
        if signup_prefill:
            return _signup_prefill_error_redirect(request, sd)
        return RedirectResponse(
            build_oauth_redirect_url(
                request,
                "",
                error=AUTHENTICATION_FAILED_ERROR,
                next_path=sd.get("next", ""),
                frontend_origin=sd.get("frontend_origin", ""),
            ),
            302,
        )

    if oauth_link and link_uid:
        base = _signup_prefill_frontend_base(request, sd)
        try:
            profile = await exchange_google_code(code, request)
            await apply_oauth_provider_link(db, "google", profile, link_uid)
            return RedirectResponse(f"{base}/settings?tab=security&oauth_linked=google", 302)
        except HTTPException as exc:
            logger.warning("oauth link google failed detail=%s", exc.detail)
            return RedirectResponse(f"{base}/settings?tab=security&oauth_link_error=1", 302)
        except Exception:
            logger.exception("oauth link google failed")
            return RedirectResponse(f"{base}/settings?tab=security&oauth_link_error=1", 302)

    if signup_prefill:
        try:
            profile = await exchange_google_code(code, request)
        except HTTPException as exc:
            logger.warning("auth signup_prefill exchange_failed method=google detail=%s", exc.detail)
            return _signup_prefill_error_redirect(request, sd)
        try:
            user = await resolve_oauth_login_user(
                db,
                "google",
                profile["email"],
                profile.get("provider_user_id", ""),
            )
        except HTTPException as exc:
            if exc.status_code == 409:
                if exc.detail == OAUTH_ACCOUNT_LINK_REQUIRED:
                    return _signup_prefill_link_required_redirect(request, sd)
                return _signup_prefill_workspace_redirect(request, sd)
            logger.warning("auth signup_prefill lookup_failed method=google detail=%s", exc.detail)
            return _signup_prefill_error_redirect(request, sd)
        if user:
            try:
                auth_payload, session_token = await complete_oauth_login(db, "google", profile, request)
                user_dict = auth_payload.get("user") or {}
                next_after = "/onboarding" if user_dict.get("onboarding_completed") is False else "/dashboard"
                return RedirectResponse(
                    build_oauth_redirect_url(
                        request,
                        session_token,
                        next_after,
                        "",
                        sd.get("frontend_origin", ""),
                    ),
                    302,
                )
            except HTTPException as exc:
                logger.warning("auth signup_prefill login_failed method=google detail=%s", exc.detail)
                return _signup_prefill_error_redirect(request, sd)
        return _signup_prefill_success_redirect(request, sd, profile, "google")

    try:
        _, session_token = await execute_oauth_flow(db, "google", code, request)
        return RedirectResponse(
            build_oauth_redirect_url(
                request,
                session_token,
                next_path=sd.get("next", ""),
                frontend_origin=sd.get("frontend_origin", ""),
            ),
            302,
        )
    except HTTPException as e:
        logger.warning("auth login_failed method=google detail=%s", e.detail)
        if _oauth_requires_paid_signup_first(e):
            return RedirectResponse(
                f"{_signup_prefill_frontend_base(request, sd)}/signup?oauth_error=no_account",
                302,
            )
        if e.status_code == 409 and e.detail == OAUTH_ACCOUNT_LINK_REQUIRED:
            return RedirectResponse(
                f"{_signup_prefill_frontend_base(request, sd)}/signin?oauth_error=link_required",
                302,
            )
        return RedirectResponse(
            build_oauth_redirect_url(
                request,
                "",
                error=AUTHENTICATION_FAILED_ERROR,
                next_path=sd.get("next", ""),
                frontend_origin=sd.get("frontend_origin", ""),
            ),
            302,
        )


@router.post("/auth/google/callback")
async def google_callback_post(request: Request):
    db = _db(request)
    body = await _json_body(request)
    code = body.get("code", "").strip()
    if not code:
        raise _auth_failed()
    try:
        if body.get("state"):
            await consume_oauth_state(db, "google", body["state"])
        auth_payload, _ = await execute_oauth_flow(db, "google", code, request)
        return _auth_response(auth_payload, request)
    except HTTPException as exc:
        logger.warning("auth login_failed method=google detail=%s", exc.detail)
        if _oauth_requires_paid_signup_first(exc):
            raise HTTPException(
                403,
                "OAUTH_REQUIRES_SIGNUP",
            )
        if exc.status_code == 409 and exc.detail == OAUTH_ACCOUNT_LINK_REQUIRED:
            raise HTTPException(409, OAUTH_ACCOUNT_LINK_REQUIRED)
        raise _auth_failed(exc.status_code if exc.status_code in {401, 403} else 401)


@router.get("/auth/facebook")
async def auth_facebook(
    request: Request,
    next_path: str = Query("", alias="next"),
    frontend_origin: str = Query("", alias="frontend_origin"),
):
    db = _db(request)
    if not os.environ.get("FACEBOOK_APP_ID") or not os.environ.get("FACEBOOK_APP_SECRET"):
        raise HTTPException(501, "Facebook OAuth is not configured.")

    state = await create_oauth_state(db, "facebook", next_path, frontend_origin)
    params = {
        "client_id": os.environ.get("FACEBOOK_APP_ID"),
        "redirect_uri": build_facebook_redirect_uri(request),
        "state": state,
        "scope": "email,public_profile",
        "response_type": "code",
    }
    return RedirectResponse(f"https://www.facebook.com/v21.0/dialog/oauth?{urlencode(params)}", 302)


@router.post("/auth/oauth/link/facebook/start")
async def oauth_link_facebook_start(request: Request):
    db = _db(request)
    current = await get_current_user_flexible(request)
    if not current.get("sub"):
        raise HTTPException(401, AUTHENTICATION_FAILED_ERROR)
    if str(current.get("role") or "").strip().lower() == "super_admin":
        raise HTTPException(400, "OAuth linking is not available for super admins")
    if not os.environ.get("FACEBOOK_APP_ID") or not os.environ.get("FACEBOOK_APP_SECRET"):
        raise HTTPException(501, "Facebook OAuth is not configured.")
    origin = sanitize_frontend_origin(request.headers.get("origin", ""))
    state = await create_oauth_state(
        db,
        "facebook",
        OAUTH_LINK_ACCOUNT_NEXT_PATH,
        origin,
        link_user_id=str(current["sub"]),
    )
    params = {
        "client_id": os.environ.get("FACEBOOK_APP_ID"),
        "redirect_uri": build_facebook_redirect_uri(request),
        "state": state,
        "scope": "email,public_profile",
        "response_type": "code",
    }
    return {"authorization_url": f"https://www.facebook.com/v21.0/dialog/oauth?{urlencode(params)}"}


@router.get("/auth/facebook/callback")
async def facebook_callback_get(
    request: Request,
    code: str = Query(""),
    state: str = Query(""),
    error: str = Query(""),
    error_description: str = Query(""),
):
    db = _db(request)
    sd = {"next": "", "frontend_origin": "", "link_user_id": ""}
    if state:
        try:
            sd = await consume_oauth_state(db, "facebook", state)
        except HTTPException:
            pass
    signup_prefill = (sd.get("next") or "") == OAUTH_SIGNUP_PREFILL_NEXT_PATH
    oauth_link = (sd.get("next") or "") == OAUTH_LINK_ACCOUNT_NEXT_PATH
    link_uid = (sd.get("link_user_id") or "").strip()
    if oauth_link and not link_uid:
        base = _signup_prefill_frontend_base(request, sd)
        return RedirectResponse(f"{base}/settings?tab=security&oauth_link_error=1", 302)

    if error or not code:
        if oauth_link and link_uid:
            base = _signup_prefill_frontend_base(request, sd)
            q = "oauth_link_error=cancelled" if error else "oauth_link_error=missing_code"
            return RedirectResponse(f"{base}/settings?tab=security&{q}", 302)
        if signup_prefill:
            return _signup_prefill_error_redirect(request, sd)
        return RedirectResponse(
            build_oauth_redirect_url(
                request,
                "",
                error=AUTHENTICATION_FAILED_ERROR,
                next_path=sd.get("next", ""),
                frontend_origin=sd.get("frontend_origin", ""),
            ),
            302,
        )

    if oauth_link and link_uid:
        base = _signup_prefill_frontend_base(request, sd)
        try:
            profile = await exchange_facebook_code(code, request)
            await apply_oauth_provider_link(db, "facebook", profile, link_uid)
            return RedirectResponse(f"{base}/settings?tab=security&oauth_linked=facebook", 302)
        except HTTPException as exc:
            logger.warning("oauth link facebook failed detail=%s", exc.detail)
            return RedirectResponse(f"{base}/settings?tab=security&oauth_link_error=1", 302)
        except Exception:
            logger.exception("oauth link facebook failed")
            return RedirectResponse(f"{base}/settings?tab=security&oauth_link_error=1", 302)

    if signup_prefill:
        try:
            profile = await exchange_facebook_code(code, request)
        except HTTPException as exc:
            logger.warning(
                "auth signup_prefill exchange_failed method=facebook detail=%s",
                exc.detail,
            )
            return _signup_prefill_error_redirect(request, sd)
        try:
            user = await resolve_oauth_login_user(
                db,
                "facebook",
                profile["email"],
                profile.get("provider_user_id", ""),
            )
        except HTTPException as exc:
            if exc.status_code == 409:
                if exc.detail == OAUTH_ACCOUNT_LINK_REQUIRED:
                    return _signup_prefill_link_required_redirect(request, sd)
                return _signup_prefill_workspace_redirect(request, sd)
            logger.warning("auth signup_prefill lookup_failed method=facebook detail=%s", exc.detail)
            return _signup_prefill_error_redirect(request, sd)
        if user:
            try:
                auth_payload, session_token = await complete_oauth_login(db, "facebook", profile, request)
                user_dict = auth_payload.get("user") or {}
                next_after = "/onboarding" if user_dict.get("onboarding_completed") is False else "/dashboard"
                return RedirectResponse(
                    build_oauth_redirect_url(
                        request,
                        session_token,
                        next_after,
                        "",
                        sd.get("frontend_origin", ""),
                    ),
                    302,
                )
            except HTTPException as exc:
                logger.warning(
                    "auth signup_prefill login_failed method=facebook detail=%s",
                    exc.detail,
                )
                return _signup_prefill_error_redirect(request, sd)
        return _signup_prefill_success_redirect(request, sd, profile, "facebook")

    try:
        _, session_token = await execute_oauth_flow(db, "facebook", code, request)
        return RedirectResponse(
            build_oauth_redirect_url(
                request,
                session_token,
                next_path=sd.get("next", ""),
                frontend_origin=sd.get("frontend_origin", ""),
            ),
            302,
        )
    except HTTPException as e:
        logger.warning("auth login_failed method=facebook detail=%s", e.detail)
        if _oauth_requires_paid_signup_first(e):
            return RedirectResponse(
                f"{_signup_prefill_frontend_base(request, sd)}/signup?oauth_error=no_account",
                302,
            )
        if e.status_code == 409 and e.detail == OAUTH_ACCOUNT_LINK_REQUIRED:
            return RedirectResponse(
                f"{_signup_prefill_frontend_base(request, sd)}/signin?oauth_error=link_required",
                302,
            )
        return RedirectResponse(
            build_oauth_redirect_url(
                request,
                "",
                error=AUTHENTICATION_FAILED_ERROR,
                next_path=sd.get("next", ""),
                frontend_origin=sd.get("frontend_origin", ""),
            ),
            302,
        )


@router.post("/auth/facebook/callback")
async def facebook_callback_post(request: Request):
    db = _db(request)
    body = await _json_body(request)
    code = body.get("code", "").strip()
    if not code:
        raise _auth_failed()
    try:
        if body.get("state"):
            await consume_oauth_state(db, "facebook", body["state"])
        auth_payload, _ = await execute_oauth_flow(db, "facebook", code, request)
        return _auth_response(auth_payload, request)
    except HTTPException as exc:
        logger.warning("auth login_failed method=facebook detail=%s", exc.detail)
        if _oauth_requires_paid_signup_first(exc):
            raise HTTPException(
                403,
                "OAUTH_REQUIRES_SIGNUP",
            )
        if exc.status_code == 409 and exc.detail == OAUTH_ACCOUNT_LINK_REQUIRED:
            raise HTTPException(409, OAUTH_ACCOUNT_LINK_REQUIRED)
        raise _auth_failed(exc.status_code if exc.status_code in {401, 403} else 401)


@router.post("/auth/invitations/accept")
async def accept_invitation(request: Request):
    db = _db(request)
    body = await _json_body(request)
    token = body.get("token", "").strip()
    name = body.get("name", "").strip()
    password = body.get("password", "")
    if not token:
        raise HTTPException(400, "Invitation token is required")
    if not name:
        raise HTTPException(400, "Name is required")
    is_valid, errors = validate_password(password)
    if not is_valid:
        raise HTTPException(400, "; ".join(errors))
    raw_token, token_hash = token_candidates(token)
    inv = r(
        await db.fetchrow(
            "SELECT * FROM invitations WHERE (token=$1 OR token=$2) AND status='pending' LIMIT 1",
            raw_token,
            token_hash,
        )
    )
    if not inv:
        raise HTTPException(400, "Invalid or expired invitation")
    exp = inv.get("expires_at")
    if isinstance(exp, datetime) and exp < datetime.now(timezone.utc):
        await db.execute(
            "UPDATE invitations SET status='expired' WHERE token=$1 OR token=$2",
            raw_token,
            token_hash,
        )
        raise HTTPException(400, "Invitation has expired")
    company_id = (inv.get("company_id") or "").strip()
    if not company_id:
        raise HTTPException(400, "Invitation is missing tenant context")
    await set_company_context(db, company_id)
    role = (inv.get("role") or "company_agent").strip().lower() or "company_agent"
    invited_sub_role = (inv.get("sub_role") or "").strip()
    invited_user_status = (inv.get("invitee_status") or "active").strip().lower()
    if invited_user_status not in {"active", "inactive"}:
        invited_user_status = "active"
    role_id = await resolve_role_id(db, role)
    existing_user = r(
        await db.fetchrow(
            "SELECT * FROM users WHERE email=$1 AND company_id=$2 LIMIT 1",
            inv["email"],
            company_id,
        )
    )
    verification_meta: dict = {}
    verification_error = ""
    if existing_user:
        await db.execute(
            "UPDATE users SET name=$1,password_hash=$2,role=$3,role_id=$4,sub_role=$5,status=$6,"
            "onboarding_completed=TRUE,plan_selected=TRUE,billing_status='active',updated_at=NOW() WHERE id=$7",  # noqa: E501
            name,
            hash_password(password),
            role,
            role_id,
            invited_sub_role,
            invited_user_status,
            existing_user["id"],
        )
        user_id = existing_user["id"]
    else:
        sub_row = await db.fetchrow(
            "SELECT plan_code, max_users FROM subscriptions WHERE company_id=$1 LIMIT 1",
            company_id,
        )
        cap = effective_max_users(dict(sub_row) if sub_row else None)
        used = await count_workspace_seats_used(db, company_id)
        pending_ct = await count_pending_invitations(db, company_id)
        if used + pending_ct > cap:
            raise HTTPException(
                403,
                "This workspace no longer has room for new users under its current plan. Ask an admin to upgrade or increase seats.",
            )
        user_id = make_id()
        await db.execute(
            "INSERT INTO users(id,email,password_hash,name,role,role_id,sub_role,status,avatar,company_id,"
            "onboarding_completed,plan_selected,billing_status,auth_provider,email_verified,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,$6,'','active','',$7,TRUE,TRUE,'active','email',FALSE,NOW(),NOW())",
            user_id,
            inv["email"],
            hash_password(password),
            name,
            role,
            role_id,
            company_id,
        )
        await db.execute(
            "UPDATE users SET sub_role=$1,status=$2,updated_at=NOW() WHERE id=$3",
            invited_sub_role,
            invited_user_status,
            user_id,
        )
    invited = r(await db.fetchrow("SELECT * FROM users WHERE id=$1 LIMIT 1", user_id))
    if invited and not invited.get("email_verified", False):
        try:
            _, verification_meta = await send_email_verification_message(db, invited, request, "register")
        except Exception as exc:
            verification_error = str(exc)[:300]
            logger.error("Invitation verification email failed: %s", exc)
    await db.execute(
        "UPDATE invitations SET status='accepted',accepted_at=NOW() WHERE token=$1 OR token=$2",
        raw_token,
        token_hash,
    )
    await record_system_log(
        db,
        {"sub": user_id, "company_id": company_id, "role": role},
        "accept_invitation",
        "user",
        user_id,
        {"email": inv["email"]},
    )
    return {
        "status": "accepted",
        "message": (
            "Invitation accepted. Verify your email to finish activating the account."
            if verification_meta
            else "Invitation accepted! You can now sign in."
        ),
        "email": inv["email"],
        "email_verification": verification_meta,
        "verification_error": verification_error,
    }


@router.patch("/auth/onboarding/profile")
async def patch_onboarding_profile(request: Request):
    """Persist onboarding wizard fields (company, industry, preferred channels)."""
    db = _db(request)
    cu = await get_current_user_flexible(request)
    uid = str(cu.get("sub") or "").strip()
    if not uid:
        raise HTTPException(401, AUTHENTICATION_FAILED_ERROR)
    if str(cu.get("role") or "").strip().lower() == "super_admin":
        return {"status": "ok"}
    company_id = get_company_id(cu)
    if not company_id:
        raise HTTPException(400, "Company context is required")
    body = await _json_body(request)
    await set_company_context(db, company_id)
    cn = (body.get("company_name") or "").strip()
    if cn:
        await db.execute(
            "UPDATE companies SET name=$1, updated_at=NOW() WHERE id=$2",
            cn,
            company_id,
        )
    if body.get("industry") is not None:
        ind = str(body.get("industry") or "").strip()
        await db.execute(
            "UPDATE company_settings SET industry=$1, updated_at=NOW() WHERE company_id=$2",
            ind,
            company_id,
        )
    if body.get("preferred_channels") is not None:
        ch = body.get("preferred_channels")
        if not isinstance(ch, list):
            raise HTTPException(400, "preferred_channels must be a list")
        channel_display_names = {
            "whatsapp": "WhatsApp",
            "facebook": "Facebook Messenger",
            "instagram": "Instagram",
            "email": "Email",
            "web_chat": "Web Chat Widget",
        }
        normalized_channels: list[str] = []
        seen_channels: set[str] = set()
        for raw_channel in ch:
            normalized = str(raw_channel or "").strip().lower()
            if normalized not in channel_display_names or normalized in seen_channels:
                continue
            seen_channels.add(normalized)
            normalized_channels.append(normalized)

        await db.execute(
            "INSERT INTO company_settings(id,company_id,preferred_channels,created_at,updated_at) "
            "VALUES($1,$2,$3::jsonb,NOW(),NOW()) "
            "ON CONFLICT (company_id) DO UPDATE SET preferred_channels=EXCLUDED.preferred_channels, updated_at=NOW()",
            make_id(),
            company_id,
            json.dumps(normalized_channels),
        )

        for channel_key, display_name in channel_display_names.items():
            enabled = channel_key in seen_channels
            await db.execute(
                "INSERT INTO channel_settings(id,company_id,channel,display_name,enabled,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,$5,NOW(),NOW()) "
                "ON CONFLICT (company_id, channel) DO UPDATE "
                "SET display_name=EXCLUDED.display_name, enabled=EXCLUDED.enabled, updated_at=NOW()",
                make_id(),
                company_id,
                channel_key,
                display_name,
                enabled,
            )
    return {"status": "ok"}


@router.put("/auth/onboarding/complete")
async def complete_onboarding(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    await db.execute(
        "UPDATE users SET onboarding_completed=TRUE,updated_at=NOW() WHERE id=$1",
        cu["sub"],
    )
    user = r(await db.fetchrow("SELECT * FROM users WHERE id=$1 LIMIT 1", cu["sub"]))
    if not user:
        raise HTTPException(404, "User not found")
    user = await ensure_user_company_assignment(db, user)
    return _auth_response(await build_auth_payload(db, user, request), request)


@router.post("/auth/billing/plan")
async def select_billing_plan(request: Request):
    """After onboarding, user selects a real free-trial or paid billing path."""
    db = _db(request)
    cu = await get_current_user_flexible(request)
    uid = str(cu.get("sub") or "").strip()
    if not uid:
        raise HTTPException(401, AUTHENTICATION_FAILED_ERROR)
    if str(cu.get("role") or "").strip().lower() == "super_admin":
        return {"status": "ok", "message": "Super admin bypass"}
    body = await _json_body(request)
    mode = str(body.get("mode") or "").strip().lower()
    if mode not in ("free_trial", "paid"):
        raise HTTPException(400, "mode must be free_trial or paid")
    user = r(await db.fetchrow("SELECT * FROM users WHERE id=$1 LIMIT 1", uid))
    if not user:
        raise HTTPException(404, "User not found")
    user = await ensure_user_company_assignment(db, user)
    if not user.get("onboarding_completed"):
        raise HTTPException(400, "Complete onboarding before selecting a plan")
    company_id = str(user.get("company_id") or "").strip()
    if not company_id:
        raise HTTPException(400, "Company context is required before selecting a plan")

    subscription = r(await db.fetchrow("SELECT * FROM subscriptions WHERE company_id=$1 LIMIT 1", company_id))
    plan_code = str((subscription or {}).get("plan_code") or "pro").strip().lower()
    if plan_code not in PLAN_CATALOG or plan_code == "free":
        plan_code = "pro"

    if mode == "free_trial":
        now = datetime.now(timezone.utc)
        existing_start = (subscription or {}).get("current_period_start")
        existing_end = (subscription or {}).get("current_period_end")
        existing_status = str((subscription or {}).get("status") or "").strip().lower()
        reset_trial_window = (
            not isinstance(existing_end, datetime)
            or existing_end <= now
            or existing_status not in {"trialing", "active"}
        )
        period_start = now if reset_trial_window or not isinstance(existing_start, datetime) else existing_start
        period_end = now + timedelta(days=trial_period_days()) if reset_trial_window else existing_end
        billing_customer = await update_billing_customer_status(
            db,
            company_id=company_id,
            payment_status="inactive",
            billing_email=(user.get("email") or "").strip().lower(),
            billing_name=(user.get("name") or "").strip(),
        )
        await upsert_subscription(
            db,
            company_id,
            billing_customer_id=billing_customer["id"],
            plan_code=plan_code,
            status="trialing",
            current_period_start=period_start,
            current_period_end=period_end,
        )
        await db.execute(
            "UPDATE users SET plan_selected=TRUE,billing_status='trial',updated_at=NOW() WHERE company_id=$1",
            company_id,
        )
        refreshed_user = r(await db.fetchrow("SELECT * FROM users WHERE id=$1 LIMIT 1", uid))
        if not refreshed_user:
            raise HTTPException(404, "User not found")
        refreshed_user = await ensure_user_company_assignment(db, refreshed_user)
        return _auth_response(await build_auth_payload(db, refreshed_user, request), request)

    if not stripe_configured() or not stripe_enabled_for_app():
        raise HTTPException(
            422,
            "Paid checkout requires Stripe (STRIPE_SECRET_KEY and STRIPE_PRICE_*). "
            "Use the free trial, set STRIPE_ENABLED=auto (default), or enable Stripe in your environment.",
        )
    assert_stripe_ready()
    if not stripe:
        raise HTTPException(501, "Stripe SDK is not installed")

    price_id = stripe_price_id(plan_code)
    if not price_id:
        raise HTTPException(501, f"Stripe price is not configured for plan {plan_code}")

    billing_customer = await get_or_create_billing_customer(
        db,
        company_id,
        billing_email=(user.get("email") or "").strip().lower(),
        billing_name=(user.get("name") or "").strip(),
        payment_status="inactive",
    )
    stripe_customer_id = str(billing_customer.get("stripe_customer_id") or "").strip()
    if not stripe_customer_id or uses_local_billing_customer_id(stripe_customer_id):
        try:
            customer = await asyncio.to_thread(
                stripe.Customer.create,
                email=(billing_customer.get("billing_email") or "").strip().lower(),
                name=(billing_customer.get("billing_name") or "").strip(),
                metadata={"billing_customer_id": billing_customer["id"], "company_id": company_id},
            )
        except Exception as exc:
            logger.exception(
                "billing checkout customer_create_failed company_id=%s user_id=%s",
                company_id,
                uid,
            )
            raise HTTPException(502, "Could not prepare Stripe checkout. Verify your Stripe configuration.") from exc
        stripe_customer_id = str(customer.get("id") or "").strip()
        await db.execute(
            "UPDATE billing_customers SET stripe_customer_id=$1,updated_at=NOW() WHERE id=$2",
            stripe_customer_id,
            billing_customer["id"],
        )
        billing_customer["stripe_customer_id"] = stripe_customer_id

    frontend_base = resolve_frontend_base_url(request).rstrip("/")
    try:
        session = await asyncio.to_thread(
            stripe.checkout.Session.create,
            mode="subscription",
            customer=billing_customer["stripe_customer_id"],
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{frontend_base}/billing?checkout=success",
            cancel_url=f"{frontend_base}/billing?checkout=cancelled",
            metadata={"company_id": company_id, "plan_code": plan_code, "source": "onboarding_plan_selection"},
            subscription_data={
                "metadata": {"company_id": company_id, "plan_code": plan_code, "source": "onboarding_plan_selection"}
            },
            allow_promotion_codes=True,
        )
    except Exception as exc:
        logger.exception(
            "billing checkout session_create_failed company_id=%s user_id=%s plan_code=%s",
            company_id,
            uid,
            plan_code,
        )
        raise HTTPException(502, "Could not open Stripe checkout. Verify your Stripe keys and price IDs.") from exc

    await update_billing_customer_status(
        db,
        company_id=company_id,
        stripe_customer_id=billing_customer["stripe_customer_id"],
        payment_status="inactive",
        billing_email=(user.get("email") or "").strip().lower(),
        billing_name=(user.get("name") or "").strip(),
    )
    await upsert_subscription(
        db,
        company_id,
        billing_customer_id=billing_customer["id"],
        plan_code=plan_code,
        status="pending",
    )
    return {"ok": True, "checkout_url": session.url, "session_id": session.id}


@router.post("/auth/onboarding/invite")
async def invite_team_member(request: Request):
    db = _db(request)
    current_user = await require_roles(request, ["admin"])
    body = await _json_body(request)
    email = (body.get("email", "") or "").strip().lower()
    role = (body.get("role", "company_agent") or "").strip().lower()
    name = (body.get("name", "") or "").strip() or (email.split("@")[0] if email else "Team Member")
    sub_role = (body.get("sub_role", "") or "").strip()
    invitee_status = (body.get("status", "active") or "").strip().lower()
    if invitee_status not in {"active", "inactive"}:
        invitee_status = "active"
    if role not in ["admin", "company_agent"]:
        raise HTTPException(400, "Invalid role")
    if not email:
        raise HTTPException(400, "Email is required")
    company_id = (current_user.get("company_id") or "").strip()
    if not company_id:
        raise HTTPException(400, "Company context is required")
    await assert_workspace_seat_available(db, company_id)
    existing_user = await db.fetchval(
        "SELECT id FROM users WHERE email=$1 AND company_id=$2",
        email,
        company_id,
    )
    if existing_user:
        raise HTTPException(400, "User already exists in this company")
    await db.execute(
        "UPDATE invitations SET status='expired' WHERE company_id=$1 AND email=$2 AND status='pending'",
        company_id,
        email,
    )
    raw_token = secrets.token_urlsafe(32)
    token_hash = hash_token(raw_token)
    invitation_id = make_id()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=INVITATION_TTL_HOURS)
    await db.execute(
        "INSERT INTO invitations(id,company_id,email,token,status,role,sub_role,invitee_status,expires_at,created_at) "
        "VALUES($1,$2,$3,$4,'pending',$5,$6,$7,$8,NOW())",
        invitation_id,
        company_id,
        email,
        token_hash,
        role,
        sub_role,
        invitee_status,
        expires_at,
    )
    company = r(await db.fetchrow("SELECT name FROM companies WHERE id=$1 LIMIT 1", company_id))
    invite_url = f"{resolve_frontend_base_url(request)}/accept-invite?{urlencode({'token': raw_token, 'email': email})}"
    html = render_platform_email_html(
        title="You're invited to Pulse Engine",
        intro=f"Hi {name}, you've been invited to join {(company or {}).get('name') or 'a Pulse Engine workspace'}.",
        body_lines=[
            f"Role: {role.replace('_', ' ').title()}",
            f"This invitation expires in {INVITATION_TTL_HOURS} hours.",
        ],
        cta_label="Accept Invitation",
        cta_url=invite_url,
        accent="#2563eb",
    )
    try:
        await send_email_async(
            to_email=email,
            subject="Your Pulse Engine invitation",
            body=invite_url,
            html_body=html,
        )
    except Exception as exc:
        logger.error("Invitation email failed: %s", exc)
        raise HTTPException(500, "Failed to send invitation email")
    sub_row = r(await db.fetchrow("SELECT plan_code FROM subscriptions WHERE company_id=$1 LIMIT 1", company_id))
    if sub_row and str(sub_row.get("plan_code") or "").strip().lower() == "enterprise":
        await db.execute(
            "UPDATE companies SET enterprise_team_gate_met=TRUE, updated_at=NOW() WHERE id=$1",
            company_id,
        )
    return {
        "id": invitation_id,
        "email": email,
        "name": name,
        "role": role,
        "sub_role": sub_role,
        "invitee_status": invitee_status,
        "status": "pending",
        "expires_at": expires_at,
        "message": "Invitation sent successfully.",
    }


@router.post("/account/delete/request-verification")
async def request_account_deletion_verification(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    user = r(await db.fetchrow("SELECT * FROM users WHERE id=$1 LIMIT 1", cu["sub"]))
    if not user:
        raise HTTPException(404, "User not found")
    email = (user.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "Your account does not have an email address")
    fp = generate_verification_fingerprint()
    code_hash = hashlib.sha256(fp.encode()).hexdigest()
    expires = datetime.now(timezone.utc) + timedelta(minutes=ACCOUNT_DELETION_TTL_MINUTES)
    await db.execute(
        "UPDATE account_deletion_verifications SET used=TRUE,used_reason='superseded',used_at=NOW() WHERE user_id=$1 AND used=FALSE",  # noqa: E501
        user["id"],
    )
    await db.execute(
        "INSERT INTO account_deletion_verifications(id,user_id,company_id,email,code_hash,used,expires_at,created_at) VALUES($1,$2,$3,$4,$5,FALSE,$6,NOW())",  # noqa: E501
        make_id(),
        user["id"],
        user.get("company_id", ""),
        email,
        code_hash,
        expires,
    )
    html = render_platform_email_html(
        title="Confirm Account Deletion",
        intro=f"Hi {user.get('name') or 'there'}, we received a request to permanently delete your account.",
        body_lines=[
            "Use the verification fingerprint below to confirm.",
            f"This fingerprint expires in {ACCOUNT_DELETION_TTL_MINUTES} minutes.",
        ],
        footer_note="Account deletion is permanent.",
        accent="#dc2626",
        highlight_label="Verification fingerprint",
        highlight_value=fp,
    )
    try:
        await send_email_async(
            to_email=email,
            subject="Verify your Pulse Engine account deletion",
            body=fp,
            html_body=html,
        )
    except Exception:
        raise HTTPException(500, "Failed to send account deletion verification email")
    return {
        "status": "verification_sent",
        "message": "A verification fingerprint has been sent to your email.",
        "expires_in_minutes": ACCOUNT_DELETION_TTL_MINUTES,
    }


@router.post("/account/delete/confirm")
async def confirm_account_deletion(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    body = await _json_body(request)
    user = r(await db.fetchrow("SELECT * FROM users WHERE id=$1 LIMIT 1", cu["sub"]))
    if not user:
        raise HTTPException(404, "User not found")
    method = (body.get("method", "") or "").strip().lower()
    if method == "password":
        if (user.get("auth_provider") or "email").lower() in (
            "google",
            "facebook",
        ) or not user.get("password_hash"):
            raise HTTPException(400, "Password verification is not available for this account.")
        if not body.get("current_password") or not verify_password(
            body["current_password"], user.get("password_hash", "")
        ):
            raise HTTPException(401, "Current password is incorrect")
    elif method == "email":
        code = (body.get("verification_code", "") or "").strip().upper()
        if not code:
            raise HTTPException(400, "Verification fingerprint is required")
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        ver = r(
            await db.fetchrow(
                "SELECT * FROM account_deletion_verifications WHERE user_id=$1 AND code_hash=$2 AND used=FALSE LIMIT 1",
                user["id"],
                code_hash,
            )
        )
        if not ver:
            raise HTTPException(400, "Invalid or expired verification fingerprint")
        exp = ver.get("expires_at")
        if isinstance(exp, datetime) and exp < datetime.now(timezone.utc):
            await db.execute(
                "UPDATE account_deletion_verifications SET used=TRUE,used_reason='expired',used_at=NOW() WHERE id=$1",
                ver["id"],
            )
            raise HTTPException(400, "Verification fingerprint has expired")
        await db.execute(
            "UPDATE account_deletion_verifications SET used=TRUE,used_reason='completed',used_at=NOW() WHERE id=$1",
            ver["id"],
        )
    else:
        raise HTTPException(400, "Unsupported verification method")
    await close_login_sessions(db, user["id"])
    await delete_user_account_records(
        db,
        user,
        delete_workspace=bool(user.get("company_id")) and user.get("role") == "admin",
    )
    return {
        "status": "deleted",
        "message": "Your account has been permanently deleted.",
    }
