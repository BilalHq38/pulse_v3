from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from services.db_helpers import (
    account_status_error_detail,
    build_auth_payload,
    bump_user_token_version,
    close_login_sessions,
    create_user_session,
    delete_user_account_records,
    ensure_user_company_assignment,
    record_auth_event,
    record_system_log,
    revoke_refresh_tokens_for_user,
    r,
    set_public_auth_context,
)
from services.billing_helpers import assert_workspace_seat_available
from shared.auth.jwt import REFRESH_TOKEN_EXPIRE_DAYS, verify_password

logger = logging.getLogger(__name__)

router = APIRouter(tags=["super-admin"])

REFRESH_COOKIE_NAME = "pe_refresh"

_SUSPICIOUS_SECURITY_FILTER = """
(
    COALESCE(se.success, TRUE) = FALSE
    OR LOWER(COALESCE(se.event_type, '')) LIKE ANY (
        ARRAY[
            '%failed%',
            '%suspicious%',
            '%anomaly%',
            '%blocked%',
            '%fraud%'
        ]
    )
)
"""
_USER_ACCESS_STATUSES = {"pending_approval", "active", "rejected", "blocked", "paused", "inactive"}
_NON_ACTIVE_STATUSES = _USER_ACCESS_STATUSES - {"active"}
_STATUS_ACTION_LOG = {
    "active": "super_admin_user_approved",
    "rejected": "super_admin_user_rejected",
    "blocked": "super_admin_user_blocked",
    "paused": "super_admin_user_paused",
    "inactive": "super_admin_user_paused",
    "pending_approval": "super_admin_approval_requested",
}


def _normalize_user_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _db(request: Request):
    return request.app.state.db


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _require_super_admin(request: Request) -> dict[str, Any]:
    auth_context = getattr(request.state, "auth_context", {}) or {}
    role = str(auth_context.get("role") or "").strip().lower()
    if role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin role required")
    return auth_context


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


def _auth_response(payload: dict[str, Any], request: Request, status_code: int = 200) -> JSONResponse:
    content = dict(payload)
    refresh_token = str(content.pop("refresh_token", "") or "").strip()
    response = JSONResponse(status_code=status_code, content=content)
    response.headers["Cache-Control"] = "no-store"
    if refresh_token:
        _set_refresh_cookie(response, request, refresh_token)
    return response


def _invalid_admin_login() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": "Invalid admin credentials"},
    )


def _coerce_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def _strip_password_hash(record: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(record)
    sanitized.pop("password_hash", None)
    return sanitized


def _blocked_account_response(status: str) -> JSONResponse:
    normalized = str(status or "inactive").strip().lower() or "inactive"
    return JSONResponse(
        status_code=403,
        content=account_status_error_detail(normalized),
    )


async def _revoke_user_access_after_status_change(db, user_id: str, status: str) -> None:
    if status == "active":
        return
    await bump_user_token_version(db, user_id)
    await revoke_refresh_tokens_for_user(db, user_id, f"status_changed:{status}")
    await close_login_sessions(db, user_id)


async def _log_super_admin_user_action(
    db,
    actor: dict[str, Any],
    *,
    action: str,
    target: dict[str, Any],
    previous_status: str,
    new_status: str,
    reason: str = "",
) -> None:
    actor_user_id = str(actor.get("sub") or actor.get("id") or "")
    target_user_id = str(target.get("id") or "")
    company_id = str(target.get("company_id") or "")
    logger.info(
        "%s actor_user_id=%s target_user_id=%s company_id=%s previous_status=%s new_status=%s reason=%s",
        action,
        actor_user_id,
        target_user_id,
        company_id,
        previous_status,
        new_status,
        reason,
    )
    await record_system_log(
        db,
        actor,
        action,
        "user",
        target_user_id,
        {
            "target_email": target.get("email", ""),
            "company_id": company_id,
            "previous_status": previous_status,
            "new_status": new_status,
            "reason": reason,
        },
    )


def _normalize_record(record: Any) -> dict[str, Any]:
    return {key: _coerce_value(value) for key, value in dict(record or {}).items()}


def _normalize_rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [_normalize_record(row) for row in rows]


@router.post("/admin/login")
async def admin_login(request: Request) -> JSONResponse:
    db = _db(request)
    body = await _json_body(request)
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    if not email or not password:
        return JSONResponse(status_code=400, content={"detail": "Email and password are required"})

    await set_public_auth_context(db, email=email)
    user = r(
        await db.fetchrow(
            "SELECT * FROM users WHERE LOWER(email)=LOWER($1) AND role='super_admin' LIMIT 1",
            email,
        )
    )
    if not user or not user.get("password_hash") or not verify_password(password, user["password_hash"]):
        await record_auth_event(db, (user or {}).get("id", ""), "login", request, False, email)
        return _invalid_admin_login()

    user = await ensure_user_company_assignment(db, user)
    account_status = str(user.get("status") or "active").strip().lower()
    if account_status in {"paused", "blocked", "inactive"}:
        await record_auth_event(db, user["id"], "login", request, False, email)
        return _blocked_account_response(account_status)
    await db.execute(
        "UPDATE users SET last_login=NOW(),updated_at=NOW() WHERE id=$1",
        user["id"],
    )
    await create_user_session(db, user["id"], request)
    await record_auth_event(db, user["id"], "login", request, True, user["email"])
    await record_system_log(
        db,
        {"sub": user["id"], "company_id": user.get("company_id", ""), "role": "super_admin"},
        "admin_login",
        "user",
        user["id"],
        {"email": user.get("email", "")},
    )
    return _auth_response(await build_auth_payload(db, user, request), request)


@router.get("/admin/overview")
@router.get("/platform/super-admin/overview")
async def admin_overview(
    request: Request,
    days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    _require_super_admin(request)
    db = _db(request)

    overview_row = await db.fetchrow(
        f"""
        SELECT
            (SELECT COUNT(*) FROM companies WHERE deleted_at IS NULL) AS total_tenants,
            (SELECT COUNT(*) FROM users) AS total_users,
            (SELECT COUNT(*) FROM users WHERE role = 'company_agent') AS total_agents,
            (SELECT COUNT(*) FROM users WHERE status = 'active') AS active_users,
            (SELECT COUNT(*) FROM users WHERE status = 'paused') AS paused_users,
            (SELECT COUNT(*) FROM users WHERE status = 'blocked') AS blocked_users,
            (SELECT COUNT(*) FROM users WHERE status = 'pending_approval') AS pending_approval_users,
            (SELECT COUNT(*) FROM users WHERE status = 'rejected') AS rejected_users,
            (SELECT COUNT(*) FROM users WHERE status = 'inactive') AS inactive_users,
            (SELECT COUNT(*) FROM leads) AS total_leads,
            (SELECT COUNT(*) FROM customers) AS total_customers,
            (SELECT COUNT(*) FROM conversations) AS total_conversations,
            (SELECT COUNT(*) FROM tickets) AS total_tickets,
            (SELECT COUNT(*) FROM company_products) AS total_products,
            (SELECT COUNT(*) FROM ai_sessions) AS total_ai_requests,
            (
                SELECT COALESCE(SUM(CASE WHEN usage_type ILIKE 'ai%%' THEN usage_units ELSE 0 END), 0)
                FROM usage_ledger
            ) AS total_ai_usage,
            (SELECT COALESCE(SUM(usage_units), 0) FROM usage_ledger) AS total_billing_usage,
            (SELECT COALESCE(SUM(request_count), 0) FROM meta_api_usage) AS total_api_usage,
            (SELECT COUNT(*) FROM webhook_events) AS total_webhook_events,
            (
                SELECT COUNT(*)
                FROM webhook_events
                WHERE LOWER(COALESCE(processing_status, '')) NOT IN ('processed', 'completed', 'success', 'delivered')
            ) AS failed_webhook_events,
            (SELECT COUNT(*) FROM resolution_audit_log) AS total_identity_resolutions,
            (
                SELECT COUNT(*)
                FROM consent_ledger
                WHERE consent_given = TRUE AND revoked_at IS NULL
            ) AS active_consents,
            (
                SELECT COUNT(*)
                FROM security_events se
                WHERE {_SUSPICIOUS_SECURITY_FILTER}
                  AND se.created_at >= NOW() - make_interval(days => $1::int)
            ) AS suspicious_events_window,
            (
                SELECT COUNT(DISTINCT se.company_id)
                FROM security_events se
                WHERE {_SUSPICIOUS_SECURITY_FILTER}
                  AND se.company_id IS NOT NULL
                  AND se.created_at >= NOW() - make_interval(days => $1::int)
            ) AS tenants_with_anomalies,
            (
                SELECT COUNT(*)
                FROM system_logs
                WHERE created_at >= NOW() - make_interval(days => $1::int)
            ) AS recent_system_logs
        """,
        days,
    )
    anomaly_rows = await db.fetch(
        f"""
        SELECT
            se.company_id,
            COALESCE(c.name, se.company_id, 'unknown') AS tenant_name,
            se.event_type,
            COUNT(*) AS event_count,
            MAX(se.created_at) AS last_seen_at
        FROM security_events se
        LEFT JOIN companies c ON c.id = se.company_id
        WHERE {_SUSPICIOUS_SECURITY_FILTER}
          AND se.created_at >= NOW() - make_interval(days => $1::int)
        GROUP BY se.company_id, c.name, se.event_type
        ORDER BY event_count DESC, last_seen_at DESC
        LIMIT 10
        """,
        days,
    )

    overview = _normalize_record(overview_row)
    overview.update(
        {
            "window_days": days,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "anomalies": _normalize_rows(anomaly_rows),
        }
    )
    return overview


@router.get("/admin/users")
@router.get("/platform/super-admin/users")
async def admin_users(
    request: Request,
    limit: int = Query(default=1000, ge=1, le=2000),
) -> list[dict[str, Any]]:
    _require_super_admin(request)
    db = _db(request)
    rows = await db.fetch(
        """
        WITH lead_totals AS (
            SELECT company_id, COUNT(*) AS total_leads
            FROM leads
            GROUP BY company_id
        ),
        user_totals AS (
            SELECT
                company_id,
                COUNT(*) FILTER (WHERE status = 'active' AND role IN ('admin', 'company_agent')) AS users_used
            FROM users
            GROUP BY company_id
        ),
        customer_totals AS (
            SELECT company_id, COUNT(*) AS total_customers
            FROM customers
            GROUP BY company_id
        ),
        conversation_totals AS (
            SELECT company_id, COUNT(*) AS total_conversations
            FROM conversations
            GROUP BY company_id
        ),
        ticket_totals AS (
            SELECT company_id, COUNT(*) AS total_tickets
            FROM tickets
            GROUP BY company_id
        ),
        product_totals AS (
            SELECT company_id, COUNT(*) AS total_products
            FROM company_products
            GROUP BY company_id
        ),
        conversation_month AS (
            SELECT company_id, COALESCE(SUM(usage_units), 0) AS monthly_conversation_usage
            FROM usage_ledger
            WHERE occurred_at >= date_trunc('month', timezone('utc', now()))
              AND usage_type = 'conversation_message'
            GROUP BY company_id
        )
        SELECT
            u.*,
            COALESCE(c.name, '') AS company_name,
            COALESCE(sub.plan_code, 'free') AS subscription_plan,
            COALESCE(sub.status, 'inactive') AS subscription_status,
            COALESCE(ut.users_used, 0) AS users_used,
            CASE
                WHEN COALESCE(sub.max_users, 0) > 0 THEN sub.max_users
                WHEN COALESCE(sub.plan_code, 'free') = 'enterprise' THEN 3
                ELSE 1
            END AS user_limit,
            COALESCE(cm.monthly_conversation_usage, 0) AS conversations_used,
            CASE
                WHEN COALESCE(sub.monthly_conversation_limit, 0) > 0 THEN sub.monthly_conversation_limit
                WHEN COALESCE(sub.plan_code, 'free') = 'enterprise' THEN 10000
                WHEN COALESCE(sub.plan_code, 'free') = 'pro' THEN 2500
                ELSE 250
            END AS conversation_limit,
            COALESCE(lt.total_leads, 0) AS leads_count,
            COALESCE(ct.total_customers, 0) AS customers_count,
            COALESCE(conv.total_conversations, 0) AS conversations_count,
            COALESCE(tt.total_tickets, 0) AS tickets_count,
            COALESCE(pt.total_products, 0) AS products_count
        FROM users u
        LEFT JOIN companies c ON c.id = u.company_id
        LEFT JOIN subscriptions sub ON sub.company_id = u.company_id
        LEFT JOIN user_totals ut ON ut.company_id = u.company_id
        LEFT JOIN lead_totals lt ON lt.company_id = u.company_id
        LEFT JOIN customer_totals ct ON ct.company_id = u.company_id
        LEFT JOIN conversation_totals conv ON conv.company_id = u.company_id
        LEFT JOIN ticket_totals tt ON tt.company_id = u.company_id
        LEFT JOIN product_totals pt ON pt.company_id = u.company_id
        LEFT JOIN conversation_month cm ON cm.company_id = u.company_id
        ORDER BY u.created_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [_strip_password_hash(_normalize_record(row)) for row in rows]


@router.put("/admin/users/{user_id}/status")
@router.put("/platform/super-admin/users/{user_id}/status")
async def admin_update_user_status(user_id: str, request: Request) -> dict[str, Any]:
    current_user = _require_super_admin(request)
    db = _db(request)
    body = await _json_body(request)
    status = str(body.get("status") or "").strip().lower()
    allowed = _USER_ACCESS_STATUSES
    if status not in allowed:
        raise HTTPException(400, f"Invalid status. Must be one of: {', '.join(sorted(allowed))}")
    if user_id == current_user.get("sub"):
        raise HTTPException(400, "Cannot change your own status")

    target = r(await db.fetchrow("SELECT * FROM users WHERE id=$1 LIMIT 1", user_id))
    if not target:
        raise HTTPException(404, "User not found")
    previous_status = _normalize_user_status(target.get("status")) or "active"
    reason = str(body.get("reason") or "").strip()[:512]
    logger.info(
        "super_admin_user_action_requested actor_user_id=%s target_user_id=%s company_id=%s previous_status=%s new_status=%s reason=%s",
        current_user.get("sub", ""),
        user_id,
        target.get("company_id", ""),
        previous_status,
        status,
        reason,
    )
    if target.get("role") == "super_admin" and previous_status == "active" and status != "active":
        other_active_super_admins = int(
            await db.fetchval(
                "SELECT COUNT(*) FROM users WHERE role='super_admin' AND status='active' AND id<>$1",
                user_id,
            )
            or 0
        )
        if other_active_super_admins < 1:
            raise HTTPException(400, "Cannot disable the last active super admin")
    if status == "active" and previous_status != "active" and target.get("role") in {"admin", "company_agent"}:
        async with db.transaction() as conn:
            await assert_workspace_seat_available(conn, str(target.get("company_id") or ""))
            await conn.execute(
                "UPDATE users SET status=$1,updated_at=NOW() WHERE id=$2",
                status,
                user_id,
            )
    else:
        await db.execute(
            "UPDATE users SET status=$1,updated_at=NOW() WHERE id=$2",
            status,
            user_id,
        )
    await _revoke_user_access_after_status_change(db, user_id, status)
    if status in _NON_ACTIVE_STATUSES:
        logger.info(
            "user_session_invalidated actor_user_id=%s target_user_id=%s company_id=%s previous_status=%s new_status=%s reason=%s",
            current_user.get("sub", ""),
            user_id,
            target.get("company_id", ""),
            previous_status,
            status,
            reason or f"status_changed:{status}",
        )
    action = _STATUS_ACTION_LOG.get(status, "super_admin_user_action")
    if previous_status in {"blocked", "paused", "inactive"} and status == "active":
        action = "super_admin_user_resumed"
    await _log_super_admin_user_action(
        db,
        current_user,
        action=action,
        target=target,
        previous_status=previous_status,
        new_status=status,
        reason=reason,
    )
    return {"status": "ok", "user_id": user_id, "new_status": status}


@router.post("/admin/users/{user_id}/review")
@router.post("/platform/super-admin/users/{user_id}/review")
async def admin_review_user(user_id: str, request: Request) -> dict[str, Any]:
    current_user = _require_super_admin(request)
    db = _db(request)
    target = r(await db.fetchrow("SELECT * FROM users WHERE id=$1 LIMIT 1", user_id))
    if not target:
        raise HTTPException(404, "User not found")
    status = _normalize_user_status(target.get("status")) or "active"
    await _log_super_admin_user_action(
        db,
        current_user,
        action="super_admin_user_reviewed",
        target=target,
        previous_status=status,
        new_status=status,
        reason="review",
    )
    return {"status": "ok", "user": _strip_password_hash(_normalize_record(target))}


@router.delete("/admin/users/{user_id}")
@router.delete("/platform/super-admin/users/{user_id}")
async def admin_delete_user(user_id: str, request: Request) -> dict[str, Any]:
    current_user = _require_super_admin(request)
    db = _db(request)
    if user_id == current_user.get("sub"):
        raise HTTPException(400, "Cannot delete your own account")

    target = r(await db.fetchrow("SELECT * FROM users WHERE id=$1 LIMIT 1", user_id))
    if not target:
        raise HTTPException(404, "User not found")

    await delete_user_account_records(
        db,
        target,
        bool(target.get("company_id")) and target.get("role") == "admin",
    )
    await record_system_log(
        db,
        current_user,
        "admin_user_delete",
        "user",
        user_id,
        {"target_email": target.get("email", "")},
    )
    return {"status": "deleted", "user_id": user_id}


@router.get("/admin/logs")
async def admin_logs(
    request: Request,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    _require_super_admin(request)
    db = _db(request)
    rows = await db.fetch(
        """
        SELECT
            sl.id,
            sl.user_id,
            sl.company_id,
            sl.action,
            sl.entity_type,
            sl.entity_id,
            sl.created_at,
            COALESCE(u.name, '') AS user_name,
            COALESCE(u.role, '') AS user_role,
            COALESCE(c.name, '') AS company_name,
            COALESCE(
                jsonb_object_agg(slm.meta_key, slm.meta_value)
                FILTER (WHERE slm.meta_key IS NOT NULL),
                '{}'::jsonb
            ) AS details
        FROM system_logs sl
        LEFT JOIN users u ON u.id = sl.user_id
        LEFT JOIN companies c ON c.id = sl.company_id
        LEFT JOIN system_log_metadata slm ON slm.log_id = sl.id
        GROUP BY sl.id, u.name, u.role, c.name
        ORDER BY sl.created_at DESC
        LIMIT $1
        """,
        limit,
    )
    return _normalize_rows(rows)


@router.get("/admin/auth-logs")
async def admin_auth_logs(
    request: Request,
    user_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    _require_super_admin(request)
    db = _db(request)
    rows = await db.fetch(
        """
        SELECT
            se.*,
            se.created_at AS event_time,
            COALESCE(u.name, '') AS user_name,
            COALESCE(u.role, '') AS user_role,
            COALESCE(u.auth_provider, 'email') AS auth_provider,
            COALESCE(c.name, '') AS company_name
        FROM security_events se
        LEFT JOIN users u ON u.id = se.user_id
        LEFT JOIN companies c ON c.id = se.company_id
        WHERE ($1::text IS NULL OR se.user_id = $1)
        ORDER BY se.created_at DESC
        LIMIT $2
        """,
        user_id,
        limit,
    )
    return _normalize_rows(rows)


@router.get("/admin/login-sessions")
async def admin_login_sessions(
    request: Request,
    user_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    _require_super_admin(request)
    db = _db(request)
    rows = await db.fetch(
        """
        SELECT
            s.*,
            s.created_at AS login_time,
            COALESCE(u.name, '') AS user_name,
            COALESCE(u.role, '') AS user_role,
            COALESCE(u.auth_provider, 'email') AS auth_provider,
            COALESCE(c.name, '') AS company_name
        FROM sessions s
        LEFT JOIN users u ON u.id = s.user_id
        LEFT JOIN companies c ON c.id = s.company_id
        WHERE ($1::text IS NULL OR s.user_id = $1)
        ORDER BY s.created_at DESC
        LIMIT $2
        """,
        user_id,
        limit,
    )
    return _normalize_rows(rows)


@router.get("/admin/tenants")
async def admin_tenants(
    request: Request,
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=250, ge=1, le=1000),
) -> dict[str, Any]:
    _require_super_admin(request)
    db = _db(request)

    tenant_rows = await db.fetch(
        f"""
        WITH user_totals AS (
            SELECT
                company_id,
                COUNT(*) AS total_users,
                COUNT(*) FILTER (WHERE status = 'active') AS active_users,
                MAX(COALESCE(last_login, created_at)) AS last_user_activity_at
            FROM users
            GROUP BY company_id
        ),
        lead_totals AS (
            SELECT company_id, COUNT(*) AS total_leads, MAX(updated_at) AS last_lead_activity_at
            FROM leads
            GROUP BY company_id
        ),
        customer_totals AS (
            SELECT company_id, COUNT(*) AS total_customers, MAX(updated_at) AS last_customer_activity_at
            FROM customers
            GROUP BY company_id
        ),
        conversation_totals AS (
            SELECT
                company_id,
                COUNT(*) AS total_conversations,
                COUNT(*) FILTER (WHERE status IN ('open', 'pending', 'escalated')) AS active_conversations,
                MAX(updated_at) AS last_conversation_activity_at
            FROM conversations
            GROUP BY company_id
        ),
        ai_window AS (
            SELECT company_id, COUNT(*) AS ai_requests, MAX(created_at) AS last_ai_activity_at
            FROM ai_sessions
            WHERE created_at >= NOW() - make_interval(days => $1::int)
            GROUP BY company_id
        ),
        usage_window AS (
            SELECT
                company_id,
                COALESCE(SUM(usage_units), 0) AS billing_usage_units,
                COALESCE(SUM(CASE WHEN usage_type ILIKE 'ai%%' THEN usage_units ELSE 0 END), 0) AS ai_usage_units,
                COUNT(*) AS usage_events
            FROM usage_ledger
            WHERE occurred_at >= NOW() - make_interval(days => $1::int)
            GROUP BY company_id
        ),
        api_window AS (
            SELECT
                company_id,
                COALESCE(SUM(request_count), 0) AS api_requests,
                COALESCE(SUM(billable_units), 0) AS api_billable_units,
                MAX(recorded_at) AS last_api_activity_at
            FROM meta_api_usage
            WHERE recorded_at >= NOW() - make_interval(days => $1::int)
            GROUP BY company_id
        ),
        webhook_window AS (
            SELECT
                company_id,
                COUNT(*) AS webhook_events,
                COUNT(*) FILTER (
                    WHERE LOWER(COALESCE(processing_status, '')) NOT IN (
                        'processed', 'completed', 'success', 'delivered'
                    )
                ) AS webhook_failures,
                MAX(created_at) AS last_webhook_activity_at
            FROM webhook_events
            WHERE created_at >= NOW() - make_interval(days => $1::int)
            GROUP BY company_id
        ),
        security_window AS (
            SELECT
                company_id,
                COUNT(*) AS security_events,
                COUNT(*) FILTER (WHERE {_SUSPICIOUS_SECURITY_FILTER}) AS suspicious_events,
                COUNT(*) FILTER (
                    WHERE COALESCE(success, TRUE) = FALSE
                       OR LOWER(COALESCE(event_type, '')) LIKE '%%failed%%'
                ) AS failed_security_events,
                MAX(created_at) AS last_security_activity_at
            FROM security_events se
            WHERE created_at >= NOW() - make_interval(days => $1::int)
            GROUP BY company_id
        ),
        identity_window AS (
            SELECT
                tenant_id AS company_id,
                COUNT(*) AS identity_resolutions,
                MAX(created_at) AS last_identity_activity_at
            FROM resolution_audit_log
            WHERE created_at >= NOW() - make_interval(days => $1::int)
            GROUP BY tenant_id
        ),
        consent_window AS (
            SELECT
                tenant_id AS company_id,
                COUNT(*) AS consent_events,
                COUNT(*) FILTER (WHERE consent_given = TRUE AND revoked_at IS NULL) AS active_consents,
                MAX(consent_timestamp) AS last_consent_activity_at
            FROM consent_ledger
            WHERE consent_timestamp >= NOW() - make_interval(days => $1::int)
            GROUP BY tenant_id
        ),
        meta_config_totals AS (
            SELECT company_id, COUNT(*) FILTER (WHERE is_active) AS active_meta_configs
            FROM tenant_meta_config
            GROUP BY company_id
        ),
        whatsapp_totals AS (
            SELECT company_id, COUNT(*) FILTER (WHERE is_active) AS active_whatsapp_channels
            FROM whatsapp_channels
            GROUP BY company_id
        ),
        conversation_month AS (
            SELECT
                company_id,
                COALESCE(SUM(usage_units), 0) AS monthly_conversation_usage
            FROM usage_ledger
            WHERE occurred_at >= date_trunc('month', timezone('utc', now()))
              AND usage_type = 'conversation_message'
            GROUP BY company_id
        )
        SELECT
            c.id AS company_id,
            c.name AS company_name,
            c.is_active,
            c.created_at,
            cs.industry,
            cs.ai_enabled,
            COALESCE(ut.total_users, 0) AS total_users,
            COALESCE(ut.active_users, 0) AS active_users,
            COALESCE(lt.total_leads, 0) AS total_leads,
            COALESCE(ct.total_customers, 0) AS total_customers,
            COALESCE(conv.total_conversations, 0) AS total_conversations,
            COALESCE(conv.active_conversations, 0) AS active_conversations,
            COALESCE(ai.ai_requests, 0) AS ai_requests_window,
            COALESCE(uw.ai_usage_units, 0) AS ai_usage_units_window,
            COALESCE(uw.billing_usage_units, 0) AS billing_usage_units_window,
            COALESCE(uw.usage_events, 0) AS billing_events_window,
            COALESCE(api.api_requests, 0) AS api_requests_window,
            COALESCE(api.api_billable_units, 0) AS api_billable_units_window,
            COALESCE(web.webhook_events, 0) AS webhook_events_window,
            COALESCE(web.webhook_failures, 0) AS webhook_failures_window,
            COALESCE(sec.security_events, 0) AS security_events_window,
            COALESCE(sec.failed_security_events, 0) AS failed_security_events_window,
            COALESCE(sec.suspicious_events, 0) AS suspicious_events_window,
            COALESCE(idn.identity_resolutions, 0) AS identity_resolutions_window,
            COALESCE(consent.consent_events, 0) AS consent_events_window,
            COALESCE(consent.active_consents, 0) AS active_consents,
            COALESCE(meta.active_meta_configs, 0) AS active_meta_configs,
            COALESCE(wa.active_whatsapp_channels, 0) AS active_whatsapp_channels,
            COALESCE(sub.plan_code, 'free') AS subscription_plan,
            COALESCE(sub.status, 'inactive') AS subscription_status,
            COALESCE(bc.payment_status, 'inactive') AS billing_status,
            COALESCE(sub.monthly_conversation_limit, 0) AS monthly_conversation_limit,
            COALESCE(sub.max_users, 0) AS max_users_per_account,
            COALESCE(cm.monthly_conversation_usage, 0) AS monthly_conversation_usage,
            CASE
                WHEN COALESCE(sub.monthly_conversation_limit, 0) > 0 THEN sub.monthly_conversation_limit
                WHEN COALESCE(sub.plan_code, 'free') = 'enterprise' THEN 10000
                WHEN COALESCE(sub.plan_code, 'free') = 'pro' THEN 2500
                ELSE 250
            END AS effective_monthly_conversation_limit,
            CASE
                WHEN COALESCE(sub.max_users, 0) > 0 THEN sub.max_users
                WHEN COALESCE(sub.plan_code, 'free') = 'enterprise' THEN 3
                ELSE 1
            END AS effective_max_users,
            GREATEST(
                c.updated_at,
                COALESCE(ut.last_user_activity_at, c.created_at),
                COALESCE(lt.last_lead_activity_at, c.created_at),
                COALESCE(ct.last_customer_activity_at, c.created_at),
                COALESCE(conv.last_conversation_activity_at, c.created_at),
                COALESCE(ai.last_ai_activity_at, c.created_at),
                COALESCE(api.last_api_activity_at, c.created_at),
                COALESCE(web.last_webhook_activity_at, c.created_at),
                COALESCE(sec.last_security_activity_at, c.created_at),
                COALESCE(idn.last_identity_activity_at, c.created_at),
                COALESCE(consent.last_consent_activity_at, c.created_at)
            ) AS last_activity_at
        FROM companies c
        LEFT JOIN company_settings cs ON cs.company_id = c.id
        LEFT JOIN user_totals ut ON ut.company_id = c.id
        LEFT JOIN lead_totals lt ON lt.company_id = c.id
        LEFT JOIN customer_totals ct ON ct.company_id = c.id
        LEFT JOIN conversation_totals conv ON conv.company_id = c.id
        LEFT JOIN ai_window ai ON ai.company_id = c.id
        LEFT JOIN usage_window uw ON uw.company_id = c.id
        LEFT JOIN api_window api ON api.company_id = c.id
        LEFT JOIN webhook_window web ON web.company_id = c.id
        LEFT JOIN security_window sec ON sec.company_id = c.id
        LEFT JOIN identity_window idn ON idn.company_id = c.id
        LEFT JOIN consent_window consent ON consent.company_id = c.id
        LEFT JOIN meta_config_totals meta ON meta.company_id = c.id
        LEFT JOIN whatsapp_totals wa ON wa.company_id = c.id
        LEFT JOIN conversation_month cm ON cm.company_id = c.id
        LEFT JOIN subscriptions sub ON sub.company_id = c.id
        LEFT JOIN billing_customers bc ON bc.company_id = c.id
        WHERE c.deleted_at IS NULL
        ORDER BY last_activity_at DESC, c.name ASC
        LIMIT $2
        """,
        days,
        limit,
    )

    return {
        "window_days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tenants": _normalize_rows(tenant_rows),
    }


@router.get("/admin/usage")
async def admin_usage(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    _require_super_admin(request)
    db = _db(request)

    platform_row = await db.fetchrow(
        f"""
        SELECT
            (
                SELECT COUNT(*)
                FROM ai_sessions
                WHERE created_at >= NOW() - make_interval(days => $1::int)
            ) AS ai_requests,
            (
                SELECT COALESCE(SUM(CASE WHEN usage_type ILIKE 'ai%%' THEN usage_units ELSE 0 END), 0)
                FROM usage_ledger
                WHERE occurred_at >= NOW() - make_interval(days => $1::int)
            ) AS ai_usage_units,
            (
                SELECT COALESCE(SUM(usage_units), 0)
                FROM usage_ledger
                WHERE occurred_at >= NOW() - make_interval(days => $1::int)
            ) AS billing_usage_units,
            (
                SELECT COUNT(*)
                FROM usage_ledger
                WHERE occurred_at >= NOW() - make_interval(days => $1::int)
            ) AS billing_events,
            (
                SELECT COALESCE(SUM(request_count), 0)
                FROM meta_api_usage
                WHERE recorded_at >= NOW() - make_interval(days => $1::int)
            ) AS api_requests,
            (
                SELECT COALESCE(SUM(billable_units), 0)
                FROM meta_api_usage
                WHERE recorded_at >= NOW() - make_interval(days => $1::int)
            ) AS api_billable_units,
            (
                SELECT COUNT(*)
                FROM webhook_events
                WHERE created_at >= NOW() - make_interval(days => $1::int)
            ) AS webhook_events,
            (
                SELECT COUNT(*)
                FROM webhook_events
                WHERE created_at >= NOW() - make_interval(days => $1::int)
                  AND LOWER(COALESCE(processing_status, '')) NOT IN ('processed', 'completed', 'success', 'delivered')
            ) AS webhook_failures,
            (
                SELECT COUNT(*)
                FROM security_events
                WHERE created_at >= NOW() - make_interval(days => $1::int)
            ) AS security_events,
            (
                SELECT COUNT(*)
                FROM security_events se
                WHERE se.created_at >= NOW() - make_interval(days => $1::int)
                  AND {_SUSPICIOUS_SECURITY_FILTER}
            ) AS suspicious_security_events,
            (
                SELECT COUNT(*)
                FROM resolution_audit_log
                WHERE created_at >= NOW() - make_interval(days => $1::int)
            ) AS identity_resolutions,
            (
                SELECT COUNT(*)
                FROM consent_ledger
                WHERE consent_timestamp >= NOW() - make_interval(days => $1::int)
            ) AS consent_events
        """,
        days,
    )
    usage_type_rows = await db.fetch(
        """
        SELECT
            usage_type,
            COUNT(*) AS event_count,
            COALESCE(SUM(usage_units), 0) AS usage_units
        FROM usage_ledger
        WHERE occurred_at >= NOW() - make_interval(days => $1::int)
        GROUP BY usage_type
        ORDER BY usage_units DESC, event_count DESC, usage_type ASC
        """,
        days,
    )
    tenant_rows = await db.fetch(
        """
        WITH ai_window AS (
            SELECT company_id, COUNT(*) AS ai_requests
            FROM ai_sessions
            WHERE created_at >= NOW() - make_interval(days => $1::int)
            GROUP BY company_id
        ),
        usage_window AS (
            SELECT
                company_id,
                COALESCE(SUM(usage_units), 0) AS total_usage_units,
                COALESCE(SUM(CASE WHEN usage_type ILIKE 'ai%%' THEN usage_units ELSE 0 END), 0) AS ai_usage_units
            FROM usage_ledger
            WHERE occurred_at >= NOW() - make_interval(days => $1::int)
            GROUP BY company_id
        ),
        api_window AS (
            SELECT
                company_id,
                COALESCE(SUM(request_count), 0) AS api_requests,
                COALESCE(SUM(billable_units), 0) AS api_billable_units
            FROM meta_api_usage
            WHERE recorded_at >= NOW() - make_interval(days => $1::int)
            GROUP BY company_id
        ),
        webhook_window AS (
            SELECT
                company_id,
                COUNT(*) AS webhook_events,
                COUNT(*) FILTER (
                    WHERE LOWER(COALESCE(processing_status, '')) NOT IN (
                        'processed', 'completed', 'success', 'delivered'
                    )
                ) AS webhook_failures
            FROM webhook_events
            WHERE created_at >= NOW() - make_interval(days => $1::int)
            GROUP BY company_id
        )
        SELECT
            c.id AS company_id,
            c.name AS company_name,
            COALESCE(ai.ai_requests, 0) AS ai_requests,
            COALESCE(uw.ai_usage_units, 0) AS ai_usage_units,
            COALESCE(uw.total_usage_units, 0) AS billing_usage_units,
            COALESCE(api.api_requests, 0) AS api_requests,
            COALESCE(api.api_billable_units, 0) AS api_billable_units,
            COALESCE(web.webhook_events, 0) AS webhook_events,
            COALESCE(web.webhook_failures, 0) AS webhook_failures,
            COALESCE(sub.plan_code, 'free') AS subscription_plan,
            COALESCE(sub.status, 'inactive') AS subscription_status
        FROM companies c
        LEFT JOIN ai_window ai ON ai.company_id = c.id
        LEFT JOIN usage_window uw ON uw.company_id = c.id
        LEFT JOIN api_window api ON api.company_id = c.id
        LEFT JOIN webhook_window web ON web.company_id = c.id
        LEFT JOIN subscriptions sub ON sub.company_id = c.id
        WHERE c.deleted_at IS NULL
        ORDER BY billing_usage_units DESC, ai_requests DESC, api_requests DESC, c.name ASC
        LIMIT $2
        """,
        days,
        limit,
    )

    return {
        "window_days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": _normalize_record(platform_row),
        "usage_by_type": _normalize_rows(usage_type_rows),
        "tenants": _normalize_rows(tenant_rows),
    }


# ─── Tenant enable/disable ──────────────────────────────────────────────────


class TenantStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    is_active: bool = Field(..., description="true to enable tenant, false to disable")
    reason: str = Field(default="", max_length=512)


@router.put("/admin/tenants/{tenant_id}/status")
@router.put("/platform/super-admin/tenants/{tenant_id}/status")
async def admin_update_tenant_status(
    tenant_id: str,
    request: Request,
    body: TenantStatusUpdate,
) -> dict[str, Any]:
    """Enable or disable a tenant. Disabled tenants cannot authenticate."""
    current_user = _require_super_admin(request)
    db = _db(request)

    tenant = _normalize_record(
        await db.fetchrow("SELECT id, name, is_active FROM companies WHERE id=$1 LIMIT 1", tenant_id)
    )
    if not tenant:
        raise HTTPException(404, "Tenant not found")

    await db.execute(
        "UPDATE companies SET is_active=$1, updated_at=NOW() WHERE id=$2",
        body.is_active,
        tenant_id,
    )
    action = "admin_tenant_enable" if body.is_active else "admin_tenant_disable"
    await record_system_log(
        db,
        current_user,
        action,
        "company",
        tenant_id,
        {
            "tenant_name": tenant.get("name", ""),
            "is_active": body.is_active,
            "reason": body.reason,
        },
    )

    # Invalidate billing cache and (on disable) revoke all active sessions
    try:
        from shared.billing_cache import invalidate_billing_cache, blacklist_all_tenant_sessions

        await invalidate_billing_cache(tenant_id)
        if not body.is_active:
            await blacklist_all_tenant_sessions(
                db,
                tenant_id,
                reason=f"tenant_disabled:{body.reason[:80]}",
            )
    except Exception as exc:
        logger.warning("tenant status change cache/session invalidation failed: %s", exc)

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "is_active": body.is_active,
    }


class TenantPlanLimitsUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    monthly_conversation_limit: Optional[int] = Field(default=None, ge=1, le=999999999)
    max_users: Optional[int] = Field(default=None, ge=1, le=999999)


@router.put("/admin/tenants/{tenant_id}/plan-limits")
@router.put("/platform/super-admin/tenants/{tenant_id}/plan-limits")
async def admin_update_tenant_plan_limits(
    tenant_id: str,
    request: Request,
    body: TenantPlanLimitsUpdate,
) -> dict[str, Any]:
    """Override per-tenant conversation caps and seat limits (subscriptions row)."""
    actor = _require_super_admin(request)
    db = _db(request)

    tenant_ok = await db.fetchval(
        "SELECT id FROM companies WHERE id=$1 AND deleted_at IS NULL LIMIT 1",
        tenant_id,
    )
    if not tenant_ok:
        raise HTTPException(404, "Tenant not found")

    sub_id = await db.fetchval("SELECT id FROM subscriptions WHERE company_id=$1 LIMIT 1", tenant_id)
    if not sub_id:
        raise HTTPException(404, "Subscription not found for tenant; create billing/subscription first")

    sets: list[str] = []
    vals: list[Any] = []
    if body.monthly_conversation_limit is not None:
        sets.append(f"monthly_conversation_limit=${len(vals) + 1}")
        vals.append(int(body.monthly_conversation_limit))
    if body.max_users is not None:
        sets.append(f"max_users=${len(vals) + 1}")
        vals.append(int(body.max_users))
    if not sets:
        raise HTTPException(400, "Provide monthly_conversation_limit and/or max_users")

    vals.append(tenant_id)
    await db.execute(
        f"UPDATE subscriptions SET {', '.join(sets)}, updated_at=NOW() WHERE company_id=${len(vals)}",
        *vals,
    )
    row = await db.fetchrow(
        "SELECT monthly_conversation_limit, max_users, plan_code FROM subscriptions WHERE company_id=$1 LIMIT 1",
        tenant_id,
    )
    try:
        from shared.billing_cache import invalidate_billing_cache
        from shared.usage_guard import invalidate_conversation_usage_cache

        await invalidate_billing_cache(tenant_id)
        await invalidate_conversation_usage_cache(tenant_id)
    except Exception as exc:
        logger.warning("plan limits cache invalidate failed tenant=%s: %s", tenant_id, exc)

    await record_system_log(
        db,
        actor,
        "admin_tenant_plan_limits",
        "subscription",
        tenant_id,
        {
            "monthly_conversation_limit": body.monthly_conversation_limit,
            "max_users": body.max_users,
        },
    )

    return {"ok": True, "tenant_id": tenant_id, "subscription": _normalize_record(row)}


# ─── Admin billing override ──────────────────────────────────────────────────


class BillingOverride(BaseModel):
    model_config = ConfigDict(extra="ignore")

    plan_code: str = Field(..., min_length=1, max_length=32)
    status: str = Field(..., min_length=1, max_length=32)
    payment_status: Optional[str] = Field(default=None, max_length=32)
    reason: str = Field(..., min_length=1, max_length=512, description="Required: explain the reason for this override")


_VALID_SUBSCRIPTION_STATUSES = {"active", "trialing", "past_due", "canceled", "unpaid", "incomplete", "paused"}
_VALID_PAYMENT_STATUSES = {"active", "inactive", "failed", "refunded"}
_VALID_PLAN_CODES = {"free", "pro", "enterprise"}
_BLOCKING_STATUSES = {"canceled", "unpaid", "past_due", "incomplete"}


@router.put("/admin/tenants/{tenant_id}/billing")
@router.put("/platform/super-admin/tenants/{tenant_id}/billing")
async def admin_override_billing(
    tenant_id: str,
    request: Request,
    body: BillingOverride,
) -> dict[str, Any]:
    """
    Manually override a tenant's subscription plan and status.
    Stores previous_state for audit + rollback.
    Invalidates Redis billing cache and (on blocking status) blacklists sessions.
    """
    current_user = _require_super_admin(request)
    db = _db(request)

    plan_code = body.plan_code.strip().lower()
    status = body.status.strip().lower()

    if plan_code not in _VALID_PLAN_CODES:
        raise HTTPException(400, f"Invalid plan_code. Must be one of: {', '.join(sorted(_VALID_PLAN_CODES))}")
    if status not in _VALID_SUBSCRIPTION_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of: {', '.join(sorted(_VALID_SUBSCRIPTION_STATUSES))}")
    if body.payment_status and body.payment_status not in _VALID_PAYMENT_STATUSES:
        raise HTTPException(
            400, f"Invalid payment_status. Must be one of: {', '.join(sorted(_VALID_PAYMENT_STATUSES))}"
        )

    tenant = _normalize_record(await db.fetchrow("SELECT id, name FROM companies WHERE id=$1 LIMIT 1", tenant_id))
    if not tenant:
        raise HTTPException(404, "Tenant not found")

    from services.billing_helpers import PLAN_CATALOG, upsert_subscription

    plan = PLAN_CATALOG.get(plan_code)
    if not plan:
        raise HTTPException(400, "Unknown plan code")

    # Capture previous state for audit trail + rollback
    prev_sub = _normalize_record(
        await db.fetchrow(
            "SELECT plan_code, status, stripe_subscription_id, billing_interval, "
            "current_period_start, current_period_end, canceled_at "
            "FROM subscriptions WHERE company_id=$1 LIMIT 1",
            tenant_id,
        )
    )
    prev_bc = _normalize_record(
        await db.fetchrow(
            "SELECT payment_status FROM billing_customers WHERE company_id=$1 LIMIT 1",
            tenant_id,
        )
    )
    previous_state = {
        "plan_code": prev_sub.get("plan_code", "free"),
        "status": prev_sub.get("status", "active"),
        "payment_status": prev_bc.get("payment_status", "inactive"),
        "stripe_subscription_id": prev_sub.get("stripe_subscription_id", ""),
    }
    new_state = {
        "plan_code": plan_code,
        "status": status,
        "payment_status": body.payment_status,
    }

    subscription = await upsert_subscription(
        db,
        tenant_id,
        plan_code=plan_code,
        status=status,
    )

    if body.payment_status:
        await db.execute(
            "UPDATE billing_customers SET payment_status=$1, updated_at=NOW() WHERE company_id=$2",
            body.payment_status,
            tenant_id,
        )

    import json
    from core.utils import make_id

    await db.execute(
        "INSERT INTO admin_billing_overrides("
        "id, company_id, previous_state, new_state, reason, changed_by, created_at"
        ") VALUES($1,$2,$3,$4,$5,$6,NOW())",
        make_id(),
        tenant_id,
        json.dumps(previous_state, ensure_ascii=True, default=str),
        json.dumps(new_state, ensure_ascii=True, default=str),
        body.reason,
        str(current_user.get("sub") or ""),
    )

    await record_system_log(
        db,
        current_user,
        "admin_billing_override",
        "company",
        tenant_id,
        {
            "tenant_name": tenant.get("name", ""),
            "previous_state": previous_state,
            "new_state": new_state,
            "reason": body.reason,
        },
    )

    # Invalidate billing cache so next request re-reads from DB
    try:
        from shared.billing_cache import invalidate_billing_cache, blacklist_all_tenant_sessions

        await invalidate_billing_cache(tenant_id)
        # If moving to a blocking status, immediately revoke all active sessions
        if status in _BLOCKING_STATUSES or body.payment_status == "failed":
            await blacklist_all_tenant_sessions(
                db,
                tenant_id,
                reason=f"billing_override:{status}:{body.reason[:80]}",
            )
    except Exception as exc:
        logger.warning("billing override cache/session invalidation failed: %s", exc)

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "subscription": subscription,
        "previous_state": previous_state,
        "new_state": new_state,
    }


@router.post("/admin/tenants/{tenant_id}/billing/rollback")
@router.post("/platform/super-admin/tenants/{tenant_id}/billing/rollback")
async def admin_rollback_billing(
    tenant_id: str,
    request: Request,
) -> dict[str, Any]:
    """
    Restore the most recent previous billing state for a tenant.
    Reads from admin_billing_overrides and re-applies previous_state.
    """
    current_user = _require_super_admin(request)
    db = _db(request)

    tenant = _normalize_record(await db.fetchrow("SELECT id, name FROM companies WHERE id=$1 LIMIT 1", tenant_id))
    if not tenant:
        raise HTTPException(404, "Tenant not found")

    row = _normalize_record(
        await db.fetchrow(
            "SELECT id, previous_state FROM admin_billing_overrides "
            "WHERE company_id=$1 ORDER BY created_at DESC LIMIT 1",
            tenant_id,
        )
    )
    if not row or not row.get("previous_state"):
        raise HTTPException(404, "No billing override history found for this tenant")

    import json

    prev = json.loads(row["previous_state"]) if isinstance(row["previous_state"], str) else row["previous_state"]
    plan_code = str(prev.get("plan_code") or "free").lower()
    status = str(prev.get("status") or "active").lower()
    payment_status = str(prev.get("payment_status") or "inactive")

    from services.billing_helpers import PLAN_CATALOG, upsert_subscription

    if plan_code not in PLAN_CATALOG:
        raise HTTPException(400, f"Rollback plan_code={plan_code!r} is not valid")

    subscription = await upsert_subscription(db, tenant_id, plan_code=plan_code, status=status)
    await db.execute(
        "UPDATE billing_customers SET payment_status=$1, updated_at=NOW() WHERE company_id=$2",
        payment_status,
        tenant_id,
    )

    await record_system_log(
        db,
        current_user,
        "admin_billing_rollback",
        "company",
        tenant_id,
        {"tenant_name": tenant.get("name", ""), "restored": prev},
    )

    try:
        from shared.billing_cache import invalidate_billing_cache

        await invalidate_billing_cache(tenant_id)
    except Exception as exc:
        logger.warning("billing rollback cache invalidation failed: %s", exc)

    return {"ok": True, "tenant_id": tenant_id, "restored": prev, "subscription": subscription}


# ─── Revenue / billing overview ──────────────────────────────────────────────


@router.get("/admin/billing")
@router.get("/platform/super-admin/billing")
async def admin_billing_overview(request: Request) -> dict[str, Any]:
    """
    Platform-wide revenue snapshot: MRR, ARR, plan breakdown, payment health,
    top-paying tenants, and who has expired/lapsed subscriptions.
    """
    _require_super_admin(request)
    db = _db(request)

    plan_rows, payment_health_rows, top_tenants_row, expired_rows = await asyncio.gather(
        # Revenue breakdown by plan × status
        db.fetch(
            """
            SELECT
                sub.plan_code,
                sub.status,
                COUNT(*) AS tenant_count,
                COALESCE(SUM(CASE WHEN bc.payment_status = 'active' THEN 1 ELSE 0 END), 0) AS paid_count,
                COALESCE(SUM(CASE WHEN bc.payment_status = 'failed' THEN 1 ELSE 0 END), 0) AS failed_count
            FROM subscriptions sub
            LEFT JOIN billing_customers bc ON bc.company_id = sub.company_id
            GROUP BY sub.plan_code, sub.status
            ORDER BY sub.plan_code, sub.status
            """
        ),
        # Payment status health
        db.fetch(
            """
            SELECT
                payment_status,
                COUNT(*) AS tenant_count
            FROM billing_customers
            GROUP BY payment_status
            ORDER BY tenant_count DESC
            """
        ),
        # Summary aggregates for MRR/ARR approximation
        db.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE sub.plan_code = 'pro' AND sub.status = 'active') AS pro_active,
                COUNT(*) FILTER (WHERE sub.plan_code = 'enterprise' AND sub.status = 'active') AS enterprise_active,
                COUNT(*) FILTER (WHERE sub.plan_code = 'free') AS free_count,
                COUNT(*) FILTER (WHERE sub.status IN ('canceled', 'unpaid', 'past_due')) AS lapsed_count,
                COUNT(DISTINCT sub.company_id) AS total_billed_tenants,
                COUNT(DISTINCT bc.stripe_customer_id) FILTER (WHERE bc.stripe_customer_id <> '') AS stripe_customers
            FROM subscriptions sub
            LEFT JOIN billing_customers bc ON bc.company_id = sub.company_id
            """
        ),
        # Recently expired / unpaid tenants
        db.fetch(
            """
            SELECT
                c.id AS company_id,
                c.name AS company_name,
                sub.plan_code,
                sub.status,
                sub.current_period_end,
                sub.canceled_at,
                COALESCE(bc.payment_status, 'inactive') AS payment_status,
                COALESCE(bc.billing_email, '') AS billing_email
            FROM subscriptions sub
            JOIN companies c ON c.id = sub.company_id
            LEFT JOIN billing_customers bc ON bc.company_id = sub.company_id
            WHERE sub.status IN ('canceled', 'unpaid', 'past_due', 'incomplete')
               OR bc.payment_status = 'failed'
            ORDER BY sub.updated_at DESC
            LIMIT 50
            """
        ),
    )

    from services.billing_helpers import PLAN_CATALOG

    summary = _normalize_record(top_tenants_row)
    pro_price = int(PLAN_CATALOG["pro"]["monthly_price_cents"])
    ent_price = int(PLAN_CATALOG["enterprise"]["monthly_price_cents"])
    pro_mrr_cents = int(summary.get("pro_active") or 0) * pro_price
    enterprise_mrr_cents = int(summary.get("enterprise_active") or 0) * ent_price
    total_mrr_cents = pro_mrr_cents + enterprise_mrr_cents

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mrr_cents": total_mrr_cents,
        "arr_cents": total_mrr_cents * 12,
        "mrr_formatted": f"${total_mrr_cents / 100:,.2f}",
        "arr_formatted": f"${total_mrr_cents * 12 / 100:,.2f}",
        "summary": {
            "pro_active": int(summary.get("pro_active") or 0),
            "enterprise_active": int(summary.get("enterprise_active") or 0),
            "free_count": int(summary.get("free_count") or 0),
            "lapsed_count": int(summary.get("lapsed_count") or 0),
            "total_billed_tenants": int(summary.get("total_billed_tenants") or 0),
            "stripe_customers": int(summary.get("stripe_customers") or 0),
        },
        "by_plan": _normalize_rows(plan_rows),
        "payment_health": _normalize_rows(payment_health_rows),
        "lapsed_tenants": _normalize_rows(expired_rows),
    }
