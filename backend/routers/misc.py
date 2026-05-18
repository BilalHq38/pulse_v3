"""routers/misc.py — Security, Notifications, System, Search, Journey — PostgreSQL."""

import hashlib
import json
import logging
import os
import secrets
import socket as _socket
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from fastapi import APIRouter, HTTPException, Query, Request, Response
from core.utils import format_response_minutes, make_id
from services.db_helpers import (
    r,
    rs,
    get_current_user_flexible,
    require_roles,
    get_company_id,
    create_notification,
    ensure_setup_reminder_notifications,
    get_average_response_minutes,
    token_candidates,
)

logger = logging.getLogger(__name__)

security_router = APIRouter()
notifications_router = APIRouter()
misc_router = APIRouter()


def _db(req):
    return req.app.state.db


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


def _client_ip_hash(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    ip = forwarded or (request.client.host if request.client else "")
    salt = (os.environ.get("VISITOR_TRACKING_SALT") or os.environ.get("JWT_SECRET") or "pulse-visitor").strip()
    return hashlib.sha256(f"{salt}:{ip}".encode("utf-8")).hexdigest() if ip else ""


async def _ensure_visitor_tracking_schema(db) -> None:
    await db.execute(
        "CREATE TABLE IF NOT EXISTS visitor_sessions ("
        "id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT '', user_id TEXT NOT NULL DEFAULT '', "
        "first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
        "page_url TEXT NOT NULL DEFAULT '', referrer TEXT NOT NULL DEFAULT '', landing_path TEXT NOT NULL DEFAULT '', "
        "user_agent TEXT NOT NULL DEFAULT '', ip_hash TEXT NOT NULL DEFAULT '', metadata JSONB NOT NULL DEFAULT '{}'::jsonb, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
    )
    await db.execute(
        "CREATE TABLE IF NOT EXISTS visitor_events ("
        "id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES visitor_sessions(id) ON DELETE CASCADE, "
        "company_id TEXT NOT NULL DEFAULT '', user_id TEXT NOT NULL DEFAULT '', event_type TEXT NOT NULL DEFAULT 'page_view', "
        "page_url TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '', referrer TEXT NOT NULL DEFAULT '', "
        "element TEXT NOT NULL DEFAULT '', metadata JSONB NOT NULL DEFAULT '{}'::jsonb, "
        "occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_visitor_sessions_company_seen ON visitor_sessions(company_id, last_seen_at DESC)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_visitor_events_company_time ON visitor_events(company_id, occurred_at DESC)"
    )


@misc_router.post("/visitor/track")
async def track_visitor(request: Request, response: Response):
    db = _db(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    await _ensure_visitor_tracking_schema(db)
    session_id = _text(
        body.get("session_id") or request.cookies.get("pulse_visitor_session") or request.headers.get("X-Visitor-Session"),
        120,
    )
    if not session_id:
        session_id = f"vis_{make_id()}"
    event_type = _text(body.get("event_type") or "page_view", 80) or "page_view"
    page_url = _text(body.get("page_url"), 1500)
    path = _text(body.get("path"), 500)
    referrer = _text(body.get("referrer"), 1500)
    company_id = _text(body.get("company_id"), 120)
    user_id = _text(body.get("user_id"), 120)
    element = _text(body.get("element"), 300)
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    user_agent = _text(request.headers.get("user-agent"), 600)
    ip_hash = _client_ip_hash(request)
    await db.execute(
        "INSERT INTO visitor_sessions(id,company_id,user_id,page_url,referrer,landing_path,user_agent,ip_hash,metadata,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,NOW(),NOW()) "
        "ON CONFLICT (id) DO UPDATE SET company_id=COALESCE(NULLIF(EXCLUDED.company_id,''),visitor_sessions.company_id), "
        "user_id=COALESCE(NULLIF(EXCLUDED.user_id,''),visitor_sessions.user_id), page_url=EXCLUDED.page_url, "
        "referrer=COALESCE(NULLIF(visitor_sessions.referrer,''),EXCLUDED.referrer), user_agent=EXCLUDED.user_agent, "
        "ip_hash=COALESCE(NULLIF(visitor_sessions.ip_hash,''),EXCLUDED.ip_hash), metadata=visitor_sessions.metadata || EXCLUDED.metadata, "
        "last_seen_at=NOW(), updated_at=NOW()",
        session_id,
        company_id,
        user_id,
        page_url,
        referrer,
        path,
        user_agent,
        ip_hash,
        json.dumps(metadata, ensure_ascii=True, default=str),
    )
    event_id = make_id()
    await db.execute(
        "INSERT INTO visitor_events(id,session_id,company_id,user_id,event_type,page_url,path,referrer,element,metadata,occurred_at,created_at) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,NOW(),NOW())",
        event_id,
        session_id,
        company_id,
        user_id,
        event_type,
        page_url,
        path,
        referrer,
        element,
        json.dumps(metadata, ensure_ascii=True, default=str),
    )
    response.set_cookie(
        "pulse_visitor_session",
        session_id,
        max_age=60 * 60 * 24 * 365,
        httponly=False,
        secure=request.url.scheme == "https",
        samesite="lax",
    )
    return {"ok": True, "session_id": session_id, "event_id": event_id}


@misc_router.get("/visitor/tracking")
async def list_visitor_tracking(
    request: Request,
    session_id: Optional[str] = None,
    company_id: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    await _ensure_visitor_tracking_schema(db)
    scoped_company_id = company_id if cu.get("role") == "super_admin" else cu.get("company_id", "")
    args: list[Any] = []
    filters: list[str] = []
    if scoped_company_id:
        args.append(scoped_company_id)
        filters.append(f"company_id=${len(args)}")
    if session_id:
        args.append(session_id)
        filters.append(f"id=${len(args)}")
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    args.append(limit)
    sessions = rs(
        await db.fetch(
            f"SELECT * FROM visitor_sessions {where} ORDER BY last_seen_at DESC LIMIT ${len(args)}",
            *args,
        )
    )
    session_ids = [row.get("id", "") for row in sessions if row.get("id")]
    events = []
    if session_ids:
        events = rs(
            await db.fetch(
                "SELECT * FROM visitor_events WHERE session_id=ANY($1::text[]) ORDER BY occurred_at DESC LIMIT $2",
                session_ids,
                limit,
            )
        )
    return {"sessions": sessions, "events": events}


async def _ensure_meta_verify_token(db, company_id: str) -> str:
    company_id = (company_id or "").strip()
    if not company_id:
        return ""

    default_rows = (
        ("whatsapp", False, "WhatsApp"),
        ("instagram", False, "Instagram"),
        ("facebook", False, "Facebook Messenger"),
    )
    for channel, enabled, display_name in default_rows:
        await db.execute(
            "INSERT INTO channel_settings(id,company_id,channel,display_name,enabled,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,NOW(),NOW()) ON CONFLICT (company_id, channel) DO NOTHING",
            make_id(),
            company_id,
            channel,
            display_name,
            enabled,
        )

    rows = rs(
        await db.fetch(
            "SELECT id,channel,display_name,verify_token FROM channel_settings "
            "WHERE company_id=$1 AND channel = ANY($2::text[])",
            company_id,
            [row[0] for row in default_rows],
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

    expected_names = {channel: display_name for channel, _, display_name in default_rows}
    for row in rows:
        updates = {}
        channel_key = str(row.get("channel") or "").strip().lower()
        expected_name = expected_names.get(channel_key, "")
        if expected_name and str(row.get("display_name") or "").strip() != expected_name:
            updates["display_name"] = expected_name
        if not str(row.get("verify_token") or "").strip():
            updates["verify_token"] = token
        if updates:
            assignments = []
            values = [row["id"]]
            for index, (key, value) in enumerate(updates.items(), start=2):
                assignments.append(f"{key}=${index}")
                values.append(value)
            assignments.append(f"updated_at=${len(values) + 1}")
            values.append(datetime.now(timezone.utc))
            await db.execute(
                f"UPDATE channel_settings SET {', '.join(assignments)} WHERE id=$1",
                *values,
            )
    return token


# Security
@security_router.get("/security/sessions")
async def list_sessions(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    rows = rs(
        await db.fetch(
            "SELECT * FROM sessions WHERE user_id=$1 AND company_id=$2 AND is_active=TRUE ORDER BY created_at DESC LIMIT 100",  # noqa: E501
            cu["sub"],
            cid,
        )
    )
    now = datetime.now(timezone.utc)
    active = []
    for s in rows:
        exp = s.get("expires_at")
        if isinstance(exp, datetime):
            exp_tz = exp.replace(tzinfo=timezone.utc) if exp.tzinfo is None else exp
            if exp_tz > now:
                user = r(await db.fetchrow("SELECT name,email FROM users WHERE id=$1", s["user_id"]))
                s["user_name"] = (user or {}).get("name", "Unknown")
                s["user_email"] = (user or {}).get("email", "")
                active.append(s)
    return active


@security_router.delete("/security/sessions/{session_id}")
async def revoke_session(session_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    raw_session_id, hashed_session_id = token_candidates(session_id)
    session = r(
        await db.fetchrow(
            "SELECT * FROM sessions WHERE (session_token=$1 OR session_token=$2) AND company_id=$3 LIMIT 1",
            raw_session_id,
            hashed_session_id,
            cid,
        )
    )
    if not session:
        raise HTTPException(404, "Session not found")
    if session.get("user_id") != cu["sub"] and cu.get("role") != "admin":
        raise HTTPException(403, "Cannot revoke other users' sessions")
    await db.execute(
        "UPDATE sessions SET is_active=FALSE,status='revoked',revoked_at=NOW(),updated_at=NOW() WHERE (session_token=$1 OR session_token=$2) AND company_id=$3",  # noqa: E501
        raw_session_id,
        hashed_session_id,
        cid,
    )
    return {"status": "revoked"}


@security_router.get("/security/login-history")
async def get_login_history(request: Request, limit: int = Query(default=50, ge=1, le=500)):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    return rs(
        await db.fetch(
            "SELECT *, created_at AS event_time FROM security_events WHERE user_id=$1 AND company_id=$2 ORDER BY created_at DESC LIMIT $3",  # noqa: E501
            cu["sub"],
            cu.get("company_id", ""),
            limit,
        )
    )


@security_router.post("/security/api-keys")
async def generate_api_key(request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin"])
    body = await request.json()
    name = str(body.get("name") or "").strip()[:128]
    if not name:
        raise HTTPException(400, "API key name is required")
    raw = secrets.token_urlsafe(48)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    kid = make_id()
    await db.execute(
        "INSERT INTO api_keys(id,name,key_prefix,key_hash,company_id,created_by,created_by_name,status,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,'active',NOW())",  # noqa: E501
        kid,
        name,
        raw[:8] + "...",
        key_hash,
        cu.get("company_id", ""),
        cu["sub"],
        cu.get("name", ""),
    )
    return {
        "id": kid,
        "name": name,
        "key": raw,
        "key_prefix": raw[:8] + "...",
        "message": "Save this key - it won't be shown again",
    }


@security_router.get("/security/api-keys")
async def list_api_keys(request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin"])
    cid = cu.get("company_id", "")
    return rs(
        await db.fetch(
            "SELECT id,name,key_prefix,company_id,created_by,created_by_name,last_used,status,created_at FROM api_keys WHERE status='active' AND company_id=$1 ORDER BY created_at DESC LIMIT 50",  # noqa: E501
            cid,
        )
    )


@security_router.delete("/security/api-keys/{key_id}")
async def revoke_api_key(key_id: str, request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin"])
    cid = cu.get("company_id", "")
    result = await db.execute(
        "UPDATE api_keys SET status='revoked',revoked_at=NOW() WHERE id=$1 AND company_id=$2",
        key_id,
        cid,
    )
    if result == "UPDATE 0":
        raise HTTPException(404, "API key not found")
    return {"status": "revoked"}


@security_router.get("/security/overview")
async def security_overview(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    uid = cu["sub"]
    cid = cu.get("company_id", "")
    return {
        "total_users": await db.fetchval("SELECT COUNT(*) FROM users WHERE company_id=$1", cid) or 0 if cid else 1,
        "active_sessions": await db.fetchval(
            "SELECT COUNT(*) FROM sessions WHERE user_id=$1 AND company_id=$2 AND is_active=TRUE AND expires_at>NOW()",
            uid,
            cid,
        )
        or 0,
        "active_api_keys": await db.fetchval(
            "SELECT COUNT(*) FROM api_keys WHERE status='active' AND company_id=$1", cid
        )
        or 0
        if cid
        else 0,
        "pending_password_resets": await db.fetchval(
            "SELECT COUNT(*) FROM password_resets WHERE used=FALSE AND user_id=$1", uid
        )
        or 0,
        "recent_events": rs(
            await db.fetch(
                "SELECT *, created_at AS event_time FROM security_events WHERE user_id=$1 AND company_id=$2 ORDER BY created_at DESC LIMIT 5",  # noqa: E501
                uid,
                cid,
            )
        ),
        "security_score": "good",
        "policies": {"password_min_length": 8, "session_expiry_days": 7},
    }


@security_router.get("/system/logs")
async def list_system_logs(
    request: Request, entity_type: Optional[str] = None, limit: int = Query(default=200, ge=1, le=1000)
):
    db = _db(request)
    cu = await require_roles(request, ["admin", "super_admin"])
    cid = get_company_id(cu)
    if cid:
        if entity_type:
            return rs(
                await db.fetch(
                    "SELECT * FROM system_logs WHERE company_id=$1 AND entity_type=$2 ORDER BY created_at DESC LIMIT $3",  # noqa: E501
                    cid,
                    entity_type,
                    limit,
                )
            )
        return rs(
            await db.fetch(
                "SELECT * FROM system_logs WHERE company_id=$1 ORDER BY created_at DESC LIMIT $2", cid, limit
            )
        )
    return []


@security_router.get("/system/auth-logs")
async def list_auth_logs(
    request: Request, user_id: Optional[str] = None, limit: int = Query(default=100, ge=1, le=500)
):
    db = _db(request)
    cu = await require_roles(request, ["admin", "super_admin"])
    cid = get_company_id(cu)
    if user_id and cid:
        return rs(
            await db.fetch(
                "SELECT *, created_at AS event_time FROM security_events WHERE user_id=$1 AND company_id=$2 ORDER BY created_at DESC LIMIT $3",  # noqa: E501
                user_id,
                cid,
                limit,
            )
        )
    if user_id:
        return []
    if cid:
        return rs(
            await db.fetch(
                "SELECT *, created_at AS event_time FROM security_events WHERE company_id=$1 ORDER BY created_at DESC LIMIT $2",  # noqa: E501
                cid,
                limit,
            )
        )
    return []


@security_router.get("/system/login-sessions")
async def list_login_sessions_admin(
    request: Request, user_id: Optional[str] = None, limit: int = Query(default=100, ge=1, le=500)
):
    db = _db(request)
    cu = await require_roles(request, ["admin", "super_admin"])
    cid = get_company_id(cu)
    if user_id and cid:
        return rs(
            await db.fetch(
                "SELECT *, created_at AS login_time FROM sessions WHERE user_id=$1 AND company_id=$2 ORDER BY created_at DESC LIMIT $3",  # noqa: E501
                user_id,
                cid,
                limit,
            )
        )
    if user_id:
        return []
    if cid:
        return rs(
            await db.fetch(
                "SELECT *, created_at AS login_time FROM sessions WHERE company_id=$1 ORDER BY created_at DESC LIMIT $2",  # noqa: E501
                cid,
                limit,
            )
        )
    return []


# Notifications
_DEFAULT_NS = {
    "notify_new_message": True,
    "notify_new_lead": True,
    "notify_new_ticket": True,
    "notify_ticket_updated": True,
    "notify_conversation_assigned": True,
    "notify_system_updates": True,
    "email_digest": False,
    "email_digest_frequency": "daily",
}


@notifications_router.get("/notifications")
async def list_notifications(request: Request, unread_only: bool = False, limit: int = Query(default=30, ge=1, le=200)):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    from core.socket import sio, connected_users

    await ensure_setup_reminder_notifications(db, sio, connected_users, cu)
    uid = cu["sub"]
    if unread_only:
        rows = await db.fetch(
            "SELECT * FROM notifications WHERE user_id=$1 AND is_read=FALSE ORDER BY created_at DESC LIMIT $2",
            uid,
            limit,
        )
    else:
        rows = await db.fetch(
            "SELECT * FROM notifications WHERE user_id=$1 ORDER BY created_at DESC LIMIT $2", uid, limit
        )
    unread = await db.fetchval("SELECT COUNT(*) FROM notifications WHERE user_id=$1 AND is_read=FALSE", uid) or 0
    return {"notifications": rs(rows), "unread_count": unread}


@notifications_router.post("/notifications")
async def push_notification(request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin"])
    body = await request.json()
    title = str(body.get("title") or "").strip()[:256]
    body_text = str(body.get("body") or "").strip()[:2048]
    if not title:
        raise HTTPException(400, "Notification title is required")
    notif_type = str(body.get("notification_type") or "info").strip()
    if notif_type not in {"info", "warning", "error", "success"}:
        notif_type = "info"
    target_user_id = str(body.get("target_user_id") or "").strip()[:64]
    from core.socket import sio, connected_users

    notif = await create_notification(db, sio, connected_users, cu, title, body_text, notif_type, target_user_id)
    return notif


@notifications_router.put("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    await db.execute("UPDATE notifications SET is_read=TRUE WHERE id=$1 AND user_id=$2", notification_id, cu["sub"])
    return {"status": "read", "id": notification_id}


@notifications_router.put("/notifications/read-all")
async def mark_all_notifications_read(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    await db.execute("UPDATE notifications SET is_read=TRUE WHERE user_id=$1 AND is_read=FALSE", cu["sub"])
    return {"status": "ok"}


@notifications_router.get("/notification-settings")
async def get_notification_settings(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    row = r(await db.fetchrow("SELECT * FROM notification_settings WHERE user_id=$1", cu["sub"]))
    result = {**_DEFAULT_NS}
    if row:
        result.update({k: v for k, v in row.items() if k in _DEFAULT_NS})
    return result


@notifications_router.put("/notification-settings")
async def update_notification_settings(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    body = await request.json()
    cid = cu.get("company_id", "")
    await db.execute(
        "INSERT INTO notification_settings(user_id,company_id,notify_new_message,notify_new_lead,notify_new_ticket,notify_ticket_updated,notify_conversation_assigned,notify_system_updates,email_digest,email_digest_frequency,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,NOW()) ON CONFLICT(user_id) DO UPDATE SET notify_new_message=$3,notify_new_lead=$4,notify_new_ticket=$5,notify_ticket_updated=$6,notify_conversation_assigned=$7,notify_system_updates=$8,email_digest=$9,email_digest_frequency=$10,updated_at=NOW()",  # noqa: E501
        cu["sub"],
        cid,
        bool(body.get("notify_new_message", True)),
        bool(body.get("notify_new_lead", True)),
        bool(body.get("notify_new_ticket", True)),
        bool(body.get("notify_ticket_updated", True)),
        bool(body.get("notify_conversation_assigned", True)),
        bool(body.get("notify_system_updates", True)),
        bool(body.get("email_digest", False)),
        str(body.get("email_digest_frequency", "daily")),
    )
    return {"status": "saved", **body}


# Misc
@misc_router.get("/healthz")
async def healthz(request: Request):
    db = _db(request)
    try:
        await db.command("SELECT 1")
        return {"status": "ok", "db": "up"}
    except Exception as exc:
        raise HTTPException(503, f"Database unavailable: {exc}")


@misc_router.get("/reference-data")
async def get_reference_data(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    # Reference data is tenant-scoped and rarely changes -> cache for 60s.
    # Survives process restarts via Redis, with an in-memory fallback.
    try:
        from shared.cache import get_cache_client

        cache = get_cache_client(namespace="refdata")
        cache_key = f"ref:{cid}"
        cached = await cache.get_json(cache_key)
        if isinstance(cached, dict):
            return cached
    except Exception:
        cache = None
        cache_key = None

    from models.reference_data import ensure_global_roles, ensure_company_reference_data

    await ensure_global_roles(db)
    await ensure_company_reference_data(db, cid)
    payload = {
        "roles": rs(await db.fetch("SELECT id,role_name,description FROM roles ORDER BY role_name")),
        "lead_statuses": rs(
            await db.fetch(
                "SELECT id,status_name,description,order_index FROM lead_statuses WHERE company_id=$1 ORDER BY order_index",  # noqa: E501
                cid,
            )
        ),
        "sources": rs(
            await db.fetch(
                "SELECT id,source_name,source_type,platform FROM sources WHERE company_id=$1 ORDER BY source_name", cid
            )
        ),
        "channels": rs(
            await db.fetch(
                "SELECT id,channel_name,channel_type,platform FROM channels WHERE company_id=$1 ORDER BY channel_name",
                cid,
            )
        ),
        "ticket_statuses": rs(
            await db.fetch(
                "SELECT id,status_name,color_code FROM ticket_statuses WHERE company_id=$1 ORDER BY status_name", cid
            )
        ),
        "customer_segments": [
            {"key": "general", "label": "General"},
            {"key": "growth", "label": "Growth"},
            {"key": "enterprise", "label": "Enterprise"},
            {"key": "vip", "label": "VIP"},
        ],
    }
    if cache is not None and cache_key:
        try:
            await cache.set_json(cache_key, payload, ttl_seconds=60)
        except Exception:
            pass
    return payload


@misc_router.get("/search/global")
async def global_search(request: Request, q: str = "", limit: int = Query(default=5, ge=1, le=50)):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    if not q:
        return {"query": q, "results": {"leads": [], "customers": [], "conversations": [], "tickets": []}}
    pq = f"%{q}%"
    leads = rs(
        await db.fetch(
            "SELECT id,name,email,customer_company_name FROM leads WHERE company_id=$1 AND (name ILIKE $2 OR email ILIKE $2 OR customer_company_name ILIKE $2) LIMIT $3",  # noqa: E501
            cid,
            pq,
            limit,
        )
    )
    for lead in leads:
        lead["company"] = lead.get("customer_company_name", "")
    customers = rs(
        await db.fetch(
            "SELECT id,name,email,phone FROM customers WHERE company_id=$1 AND (name ILIKE $2 OR email ILIKE $2 OR phone ILIKE $2) LIMIT $3",  # noqa: E501
            cid,
            pq,
            limit,
        )
    )
    conversations = rs(
        await db.fetch(
            "SELECT id,customer_name,subject,last_message FROM conversations WHERE company_id=$1 AND (customer_name ILIKE $2 OR subject ILIKE $2 OR last_message ILIKE $2) ORDER BY updated_at DESC LIMIT $3",  # noqa: E501
            cid,
            pq,
            limit,
        )
    )
    tickets = rs(
        await db.fetch(
            "SELECT id,subject,ticket_number,status FROM tickets WHERE company_id=$1 AND (subject ILIKE $2 OR ticket_number ILIKE $2) ORDER BY updated_at DESC LIMIT $3",  # noqa: E501
            cid,
            pq,
            limit,
        )
    )
    return {
        "query": q,
        "results": {"leads": leads, "customers": customers, "conversations": conversations, "tickets": tickets},
    }


@misc_router.get("/dashboard/feed")
async def dashboard_feed(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    return {
        "recent_conversations": rs(
            await db.fetch("SELECT * FROM conversations WHERE company_id=$1 ORDER BY updated_at DESC LIMIT 5", cid)
        ),
        "recent_leads": rs(
            await db.fetch("SELECT * FROM leads WHERE company_id=$1 ORDER BY created_at DESC LIMIT 5", cid)
        ),
        "recent_tickets": rs(
            await db.fetch("SELECT * FROM tickets WHERE company_id=$1 ORDER BY created_at DESC LIMIT 5", cid)
        ),
    }


@misc_router.get("/dashboard/live-summary")
async def dashboard_live_summary(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    uid = cu.get("sub", "")
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    active_statuses = ["open", "pending", "escalated"]
    urgent_threshold_raw = 0.4  # normalized 0.7 gate converted back to -1..1 storage scale

    active_conversations = (
        await db.fetchval(
            "SELECT COUNT(*) FROM conversations WHERE company_id=$1 AND status=ANY($2)", cid, active_statuses
        )
        or 0
    )
    pending_replies = (
        await db.fetchval(
            "SELECT COUNT(*) FROM conversations c "
            "JOIN LATERAL (SELECT sender_type FROM messages m WHERE m.conversation_id=c.id ORDER BY m.created_at DESC LIMIT 1) lm ON TRUE "  # noqa: E501
            "WHERE c.company_id=$1 AND c.status=ANY($2) AND lm.sender_type='customer'",
            cid,
            active_statuses,
        )
        or 0
    )
    incoming_messages = (
        await db.fetchval(
            "SELECT COUNT(*) FROM messages WHERE company_id=$1 AND sender_type='customer' AND created_at >= $2",
            cid,
            today_start,
        )
        or 0
    )
    ai_active = (
        await db.fetchval(
            "SELECT COUNT(*) FROM conversations WHERE company_id=$1 AND status=ANY($2) AND ai_handled=TRUE",
            cid,
            active_statuses,
        )
        or 0
    )
    human_active = (
        await db.fetchval(
            "SELECT COUNT(*) FROM conversations WHERE company_id=$1 AND status=ANY($2) AND ai_handled=FALSE",
            cid,
            active_statuses,
        )
        or 0
    )

    unread_total = (
        await db.fetchval("SELECT COALESCE(SUM(unread_count),0) FROM conversations WHERE company_id=$1", cid) or 0
    )
    platform_counts = {}
    for channel in ["whatsapp", "instagram", "facebook", "web_chat"]:
        platform_counts[channel] = (
            await db.fetchval(
                "SELECT COALESCE(SUM(unread_count),0) FROM conversations WHERE company_id=$1 AND channel=$2",
                cid,
                channel,
            )
            or 0
        )
    urgent_chats = (
        await db.fetchval(
            "SELECT COUNT(DISTINCT c.id) FROM conversations c "
            "LEFT JOIN conversation_tags ct ON ct.conversation_id=c.id "
            "WHERE c.company_id=$1 AND c.unread_count>0 AND (c.sentiment_score < $2 OR ct.tag=ANY($3))",
            cid,
            urgent_threshold_raw,
            ["toxic", "escalating", "possible-hate-speech"],
        )
        or 0
    )

    ai_messages_today = (
        await db.fetchval(
            "SELECT COUNT(*) FROM messages WHERE company_id=$1 AND sender_type='ai' AND created_at >= $2",
            cid,
            today_start,
        )
        or 0
    )
    customer_messages_today = (
        await db.fetchval(
            "SELECT COUNT(*) FROM messages WHERE company_id=$1 AND sender_type='customer' AND created_at >= $2",
            cid,
            today_start,
        )
        or 0
    )
    ai_success_rate = round((ai_messages_today / customer_messages_today) * 100, 1) if customer_messages_today else 0
    blocked_flagged = (
        await db.fetchval(
            "SELECT COUNT(DISTINCT c.id) FROM conversations c "
            "LEFT JOIN conversation_tags ct ON ct.conversation_id=c.id "
            "WHERE c.company_id=$1 AND (c.sentiment_score < $2 OR ct.tag=ANY($3))",
            cid,
            urgent_threshold_raw,
            ["toxic", "escalating", "possible-hate-speech"],
        )
        or 0
    )
    overall_resp = await get_average_response_minutes(db, cid)
    ai_response_time = format_response_minutes(overall_resp)
    human_response_time = format_response_minutes(await get_average_response_minutes(db, cid))
    suggested_replies_pending = 0

    new_leads_today = (
        await db.fetchval("SELECT COUNT(*) FROM leads WHERE company_id=$1 AND created_at >= $2", cid, today_start) or 0
    )
    converted_leads_today = (
        await db.fetchval(
            "SELECT COUNT(*) FROM journey_tracking WHERE company_id=$1 AND phase=ANY($2) AND started_at >= $3",
            cid,
            ["conversion", "purchase"],
            today_start,
        )
        or 0
    )
    hot_leads = await db.fetchval("SELECT COUNT(*) FROM leads WHERE company_id=$1 AND grade='hot'", cid) or 0
    recent_lead_activity = rs(
        await db.fetch(
            "SELECT id,name,customer_company_name,grade,status,updated_at,score FROM leads WHERE company_id=$1 ORDER BY updated_at DESC LIMIT 5",  # noqa: E501
            cid,
        )
    )
    for lead in recent_lead_activity:
        lead["company"] = lead.get("customer_company_name", "")

    toxic_rows = rs(
        await db.fetch(
            "SELECT DISTINCT c.id,c.customer_name,c.channel,c.last_message,c.updated_at FROM conversations c "
            "LEFT JOIN conversation_tags ct ON ct.conversation_id=c.id "
            "WHERE c.company_id=$1 AND (c.sentiment_score < $2 OR ct.tag=ANY($3)) "
            "ORDER BY c.updated_at DESC LIMIT 3",
            cid,
            urgent_threshold_raw,
            ["toxic", "escalating", "possible-hate-speech"],
        )
    )
    escalated_rows = rs(
        await db.fetch(
            "SELECT id,customer_name,channel,last_message,updated_at FROM conversations WHERE company_id=$1 AND status='escalated' ORDER BY updated_at DESC LIMIT 3",  # noqa: E501
            cid,
        )
    )
    notif_rows = rs(
        await db.fetch(
            "SELECT * FROM notifications WHERE user_id=$1 AND type=ANY($2) ORDER BY created_at DESC LIMIT 5",
            uid,
            ["warning", "error"],
        )
    )

    alerts = []
    for convo in toxic_rows:
        alerts.append(
            {
                "id": f"toxic-{convo['id']}",
                "kind": "negative_conversation",
                "title": f"Toxic or risky chat: {convo.get('customer_name', 'Customer')}",
                "detail": convo.get("last_message", ""),
                "severity": "error",
                "actions": [
                    {"label": "View", "url": f"/inbox?conversation={convo['id']}"},
                    {"label": "Reply", "url": f"/inbox?conversation={convo['id']}"},
                ],
            }
        )
    for convo in escalated_rows:
        alerts.append(
            {
                "id": f"escalated-{convo['id']}",
                "kind": "escalated_chat",
                "title": f"Escalated chat: {convo.get('customer_name', 'Customer')}",
                "detail": convo.get("last_message", ""),
                "severity": "warning",
                "actions": [
                    {"label": "View", "url": f"/inbox?conversation={convo['id']}"},
                    {"label": "Reply", "url": f"/inbox?conversation={convo['id']}"},
                ],
            }
        )
    for note in notif_rows:
        alerts.append(
            {
                "id": f"notif-{note['id']}",
                "kind": "system_issue",
                "title": note.get("title", "System issue"),
                "detail": note.get("body", ""),
                "severity": note.get("type", "warning"),
                "actions": [
                    {"label": "View", "url": note.get("action_url") or "/settings?tab=notifications"},
                    {"label": "Fix", "url": note.get("action_url") or "/settings?tab=integrations"},
                ],
            }
        )
    alerts = alerts[:8]

    gemini_configured = bool(os.environ.get("GEMINI_API_KEY", ""))
    channel_rows = rs(await db.fetch("SELECT * FROM channel_settings WHERE company_id=$1 ORDER BY channel", cid))
    channels = []
    for row in channel_rows:
        configured = bool(row.get("enabled")) and bool(
            row.get("access_token") or row.get("api_key") or row.get("page_id") or row.get("phone_number")
        )
        channels.append(
            {
                "channel": row.get("channel", ""),
                "enabled": bool(row.get("enabled")),
                "configured": configured,
                "last_sync_time": row.get("updated_at"),
            }
        )
    bulk_upload_status = (
        "failed"
        if any(a["kind"] == "system_issue" and "upload" in (a["title"] + " " + a["detail"]).lower() for a in alerts)
        else "success"
    )

    health = {
        "gemini_api": "online" if gemini_configured else "not_configured",
        "channels": channels,
        "bulk_upload_status": bulk_upload_status,
        "server_time": now.isoformat(),
    }

    last_24h_messages = rs(
        await db.fetch(
            "SELECT TO_CHAR(date_trunc('hour', created_at), 'HH24:00') AS bucket, COUNT(*) AS count "
            "FROM messages WHERE company_id=$1 AND created_at >= $2 GROUP BY date_trunc('hour', created_at) ORDER BY date_trunc('hour', created_at)",  # noqa: E501
            cid,
            now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23),
        )
    )
    sentiment_today = {
        "positive": await db.fetchval(
            "SELECT COUNT(*) FROM messages WHERE company_id=$1 AND created_at >= $2 AND sentiment_score > 0.4",
            cid,
            today_start,
        )
        or 0,
        "neutral": await db.fetchval(
            "SELECT COUNT(*) FROM messages WHERE company_id=$1 AND created_at >= $2 AND sentiment_score >= -0.2 AND sentiment_score <= 0.4",  # noqa: E501
            cid,
            today_start,
        )
        or 0,
        "negative": await db.fetchval(
            "SELECT COUNT(*) FROM messages WHERE company_id=$1 AND created_at >= $2 AND sentiment_score < -0.2",
            cid,
            today_start,
        )
        or 0,
    }

    insights = []
    total_sent_today = sum(sentiment_today.values()) or 1
    negative_pct = round((sentiment_today["negative"] / total_sent_today) * 100)
    if negative_pct >= 20:
        insights.append(f"{negative_pct}% conversations turned negative today.")
    if platform_counts.get("instagram", 0) > platform_counts.get("facebook", 0):
        insights.append("Instagram has more unread demand than Facebook right now.")
    if pending_replies > ai_active:
        insights.append("Pending replies are outpacing AI-handled active chats.")
    if not insights:
        insights.append("Live traffic is stable across channels.")

    recent_conversations = rs(
        await db.fetch(
            "SELECT id,customer_name,channel,last_message,status,updated_at FROM conversations WHERE company_id=$1 ORDER BY updated_at DESC LIMIT 6",  # noqa: E501
            cid,
        )
    )

    return {
        "real_time_activity": {
            "incoming_messages": incoming_messages,
            "active_conversations": active_conversations,
            "pending_replies": pending_replies,
            "ai_active_chats": ai_active,
            "human_active_chats": human_active,
            "live_feed": recent_conversations,
        },
        "inbox_summary": {
            "total_unread": unread_total,
            "platform_counts": platform_counts,
            "urgent_chats": urgent_chats,
        },
        "ai_performance": {
            "ai_response_success_rate_today": ai_success_rate,
            "blocked_or_flagged_conversations": blocked_flagged,
            "avg_response_time_ai": ai_response_time,
            "avg_response_time_human": human_response_time,
            "suggested_replies_pending": suggested_replies_pending,
        },
        "leads_quick_view": {
            "new_leads_today": new_leads_today,
            "converted_leads_today": converted_leads_today,
            "hot_leads": hot_leads,
            "recent_lead_activity": recent_lead_activity,
        },
        "alerts": alerts,
        "system_health": health,
        "micro_visuals": {
            "messages_last_24h": last_24h_messages,
            "sentiment_distribution": sentiment_today,
            "response_rate_gauge": ai_success_rate,
        },
        "insights": insights,
    }


@misc_router.get("/webhooks/info")
async def webhook_info(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    app_url = os.environ.get("APP_URL", "")
    verify_token = await _ensure_meta_verify_token(db, cid) if cid else ""
    return {
        "webhook_urls": {
            "whatsapp": f"{app_url}/api/webhooks/whatsapp",
            "facebook": f"{app_url}/api/webhooks/facebook",
            "instagram": f"{app_url}/api/webhooks/instagram",
            "lead_form": f"{app_url}/api/webhooks/lead-form",
            "external_purchases": f"{app_url}/api/webhooks/external/purchases",
        },
        "verify_token": verify_token,
        "signature_headers": {"meta": "X-Hub-Signature-256", "signed": "X-Webhook-Timestamp + X-Webhook-Signature"},
    }


@misc_router.get("/infrastructure/rds-health")
async def rds_health(request: Request):
    await require_roles(request, ["admin"])
    host = os.environ.get("PGHOST", "")
    port = int(os.environ.get("PGPORT", "5432"))
    if not host:
        return {"configured": False, "reachable": False, "message": "Set PGHOST/PGPORT env vars"}
    reachable = False
    error = ""
    try:
        with _socket.create_connection((host, port), timeout=3):
            reachable = True
    except Exception as exc:
        error = str(exc)
    return {"configured": True, "reachable": reachable, "host": host, "port": port, "error": error}


@misc_router.get("/infrastructure/postgres-health")
async def postgres_health(request: Request):
    await require_roles(request, ["admin"])
    provider = (os.environ.get("DB_PROVIDER", "postgres") or "postgres").strip().lower()
    database_url = (os.environ.get("DATABASE_URL", "") or "").strip()
    if not database_url:
        return {
            "provider": provider,
            "configured": False,
            "reachable": False,
            "message": "Set DATABASE_URL in the process environment or the repository root .env file",
        }

    try:
        from postgres.session import check_postgres_health

        await check_postgres_health()
        return {
            "provider": provider,
            "configured": True,
            "reachable": True,
        }
    except Exception as exc:
        return {
            "provider": provider,
            "configured": True,
            "reachable": False,
            "error": str(exc),
        }


@misc_router.get("/journey/tracking")
async def list_journey_records(
    request: Request,
    lead_id: Optional[str] = None,
    customer_id: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    if lead_id:
        return rs(
            await db.fetch(
                "SELECT * FROM journey_tracking WHERE company_id=$1 AND lead_id=$2 ORDER BY started_at DESC LIMIT $3",
                cid,
                lead_id,
                limit,
            )
        )
    if customer_id:
        return rs(
            await db.fetch(
                "SELECT * FROM journey_tracking WHERE company_id=$1 AND customer_id=$2 ORDER BY started_at DESC LIMIT $3",  # noqa: E501
                cid,
                customer_id,
                limit,
            )
        )
    return rs(
        await db.fetch(
            "SELECT * FROM journey_tracking WHERE company_id=$1 ORDER BY started_at DESC LIMIT $2", cid, limit
        )
    )


@misc_router.post("/journey/tracking")
async def create_journey_record(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    body = await request.json()
    jid = make_id()
    await db.execute(
        "INSERT INTO journey_tracking(id,company_id,user_id,lead_id,customer_id,purchase_id,session_id,phase,source,started_at,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW(),NOW())",  # noqa: E501
        jid,
        cid,
        body.get("user_id", cu.get("sub", "")),
        body.get("lead_id", ""),
        body.get("customer_id", ""),
        body.get("purchase_id", ""),
        body.get("session_id", ""),
        body.get("phase", "awareness"),
        body.get("source", ""),
    )
    if body.get("metadata"):
        await db.executemany(
            "INSERT INTO journey_metadata(journey_id,company_id,meta_key,meta_value) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",  # noqa: E501
            [(jid, cid, k, str(v)) for k, v in body["metadata"].items()],
        )
    return r(await db.fetchrow("SELECT * FROM journey_tracking WHERE id=$1", jid))


@misc_router.post("/seed")
async def seed_data():
    raise HTTPException(status_code=410, detail="Seed endpoint has been removed. Use real-time production data only.")
