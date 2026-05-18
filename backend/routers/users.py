"""routers/users.py — PostgreSQL version."""

import logging
import secrets
from fastapi import APIRouter, HTTPException, Request
from shared.auth.jwt import hash_password
from core.utils import make_id, now_ts, validate_password
from models.reference_data import resolve_role_id
from services.billing_helpers import assert_workspace_seat_available
from services.db_helpers import (
    bump_user_token_version,
    close_login_sessions,
    delete_user_account_records,
    ensure_unique_company_role,
    get_current_user_flexible,
    require_roles,
    revoke_refresh_tokens_for_user,
    r,
    rs,
    get_company_id,
)

logger = logging.getLogger(__name__)
router = APIRouter()

USER_UPDATE_FIELDS = {
    "name",
    "email",
    "role",
    "sub_role",
    "status",
    "avatar",
    "phone",
    "mobile_number",
    "timezone",
    "preferred_language",
    "date_format",
    "currency",
    "locale_information",
    "address_info",
    "website_address",
}


def _generate_temporary_password() -> str:
    token = secrets.token_urlsafe(9)
    return f"Tmp!{token}9a"


def _db(req):
    return req.app.state.db


def _strip_pw(d):
    if d:
        d.pop("password_hash", None)
    return d


async def _revoke_user_access_after_status_change(db, user_id: str, status: str) -> None:
    if status == "active":
        return
    await bump_user_token_version(db, user_id)
    await revoke_refresh_tokens_for_user(db, user_id, f"status_changed:{status}")
    await close_login_sessions(db, user_id)


@router.get("/users")
async def list_users(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    rows = (
        await db.fetch("SELECT * FROM users WHERE company_id=$1 ORDER BY created_at DESC", cid)
        if cid
        else await db.fetch("SELECT * FROM users ORDER BY created_at DESC LIMIT 500")
    )
    return [_strip_pw(dict(r)) for r in rows]


@router.post("/users")
async def create_user(request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin"])
    body = await request.json()
    email = (body.get("email", "") or "").strip().lower()
    if not email:
        raise HTTPException(400, "Email is required")
    role = (body.get("role", "company_agent") or "").strip().lower()
    if role not in {"admin", "company_agent"}:
        raise HTTPException(400, "Invalid role")
    await ensure_unique_company_role(db, cu, role)
    temp_pw = (body.get("temporary_password", "") or "").strip() or _generate_temporary_password()
    is_valid, errors = validate_password(temp_pw)
    if not is_valid:
        raise HTTPException(400, "; ".join(errors))
    cid = cu.get("company_id", "")
    role_id = await resolve_role_id(db, role)
    uid = make_id()
    async with db.transaction() as conn:
        await assert_workspace_seat_available(conn, cid)
        if await conn.fetchval(
            "SELECT id FROM users WHERE email=$1 AND company_id=$2",
            email,
            cid,
        ):
            raise HTTPException(400, "User already exists in this company")
        await conn.execute(
            "INSERT INTO users(id,email,password_hash,name,role,role_id,sub_role,status,avatar,company_id,onboarding_completed,plan_selected,billing_status,auth_provider,email_verified,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,'',$9,TRUE,TRUE,'active','email',FALSE,NOW(),NOW())",  # noqa: E501
            uid,
            email,
            hash_password(temp_pw),
            (body.get("name", "") or "").strip() or email.split("@")[0],
            role,
            role_id,
            (body.get("sub_role", "") or "").strip(),
            body.get("status", "active") if body.get("status") in ("active", "inactive") else "active",
            cid,
        )
    return _strip_pw(dict(await db.fetchrow("SELECT * FROM users WHERE id=$1", uid)))


@router.put("/users/{user_id}")
async def update_user(user_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu) or ""
    target = r(await db.fetchrow("SELECT * FROM users WHERE id=$1 AND company_id=$2 LIMIT 1", user_id, cid))
    if not target:
        raise HTTPException(404, "User not found")
    body = await request.json()
    if cu.get("role") != "admin" and cu["sub"] != user_id:
        raise HTTPException(403, "Insufficient permissions")
    if cu.get("role") != "admin" and cu["sub"] == user_id:
        body = {k: v for k, v in body.items() if k in {"name", "avatar", "phone"}}
    body.pop("password_hash", None)
    body.pop("_id", None)
    if body.get("role"):
        body["role"] = (body["role"] or "").strip().lower()
        if body["role"] not in {"admin", "company_agent"}:
            raise HTTPException(400, "Invalid role")
        await ensure_unique_company_role(db, cu, body["role"], exclude_user_id=user_id)
    if body.get("email") is not None:
        next_email = (body.get("email") or "").strip().lower()
        if not next_email:
            raise HTTPException(400, "Email is required")
        if await db.fetchval(
            "SELECT id FROM users WHERE email=$1 AND company_id=$2 AND id<>$3",
            next_email,
            cid,
            user_id,
        ):
            raise HTTPException(409, "Another user in this company already uses that email")
        body["email"] = next_email
    safe_body = {k: v for k, v in body.items() if k in USER_UPDATE_FIELDS}
    if body and not safe_body:
        raise HTTPException(400, "No valid fields provided")
    safe_body["updated_at"] = now_ts()
    columns = list(safe_body.keys())
    set_parts = ", ".join(f"{k}=${i + 3}" for i, k in enumerate(columns))
    values = [safe_body[col] for col in columns]
    await db.execute(f"UPDATE users SET {set_parts} WHERE id=$1 AND company_id=$2", user_id, cid, *values)
    return _strip_pw(dict(await db.fetchrow("SELECT * FROM users WHERE id=$1", user_id)))


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin"])
    if cu["sub"] == user_id:
        raise HTTPException(400, "Cannot delete your own account")
    cid = get_company_id(cu) or ""
    target = r(await db.fetchrow("SELECT * FROM users WHERE id=$1 AND company_id=$2 LIMIT 1", user_id, cid))
    if not target:
        raise HTTPException(404, "User not found")
    await delete_user_account_records(db, target, bool(target.get("company_id")) and target.get("role") == "admin")
    return {"status": "deleted", "user_id": user_id}


@router.put("/users/{user_id}/password")
async def change_user_password(user_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    if cu["sub"] != user_id and cu.get("role") != "admin":
        raise HTTPException(403, "You can only change your own password")
    body = await request.json()
    is_valid, errors = validate_password(body.get("new_password", ""))
    if not is_valid:
        raise HTTPException(400, "; ".join(errors))
    cid = get_company_id(cu) or ""
    if not await db.fetchval("SELECT id FROM users WHERE id=$1 AND company_id=$2", user_id, cid):
        raise HTTPException(404, "User not found")
    await db.execute(
        "UPDATE users SET password_hash=$1,updated_at=NOW() WHERE id=$2", hash_password(body["new_password"]), user_id
    )
    await bump_user_token_version(db, user_id)
    await revoke_refresh_tokens_for_user(db, user_id, "password_changed")
    return {"status": "password_changed", "message": "Password updated successfully"}


@router.get("/platform/super-admin/overview")
async def super_admin_overview(request: Request):
    db = _db(request)
    await require_roles(request, ["super_admin"])
    return {
        "total_users": await db.fetchval("SELECT COUNT(*) FROM users") or 0,
        "total_agents": await db.fetchval("SELECT COUNT(*) FROM users WHERE role='company_agent'") or 0,
        "active_users": await db.fetchval("SELECT COUNT(*) FROM users WHERE status='active'") or 0,
        "paused_users": await db.fetchval("SELECT COUNT(*) FROM users WHERE status='paused'") or 0,
        "blocked_users": await db.fetchval("SELECT COUNT(*) FROM users WHERE status='blocked'") or 0,
        "inactive_users": await db.fetchval("SELECT COUNT(*) FROM users WHERE status='inactive'") or 0,
        "total_leads": await db.fetchval("SELECT COUNT(*) FROM leads") or 0,
        "total_customers": await db.fetchval("SELECT COUNT(*) FROM customers") or 0,
        "total_conversations": await db.fetchval("SELECT COUNT(*) FROM conversations") or 0,
        "total_tickets": await db.fetchval("SELECT COUNT(*) FROM tickets") or 0,
        "total_products": await db.fetchval("SELECT COUNT(*) FROM company_products") or 0,
    }


@router.get("/platform/super-admin/users")
async def super_admin_list_users(request: Request):
    db = _db(request)
    await require_roles(request, ["super_admin"])
    users = rs(await db.fetch("SELECT * FROM users ORDER BY created_at DESC LIMIT 1000"))
    for u in users:
        u.pop("password_hash", None)
        cid = u.get("company_id", "")
        company = r(await db.fetchrow("SELECT name FROM companies WHERE id=$1 LIMIT 1", cid)) if cid else None
        u["company_name"] = (company or {}).get("name", "")
        u["leads_count"] = await db.fetchval("SELECT COUNT(*) FROM leads WHERE company_id=$1", cid) or 0 if cid else 0
        u["customers_count"] = (
            await db.fetchval("SELECT COUNT(*) FROM customers WHERE company_id=$1", cid) or 0 if cid else 0
        )
        u["conversations_count"] = (
            await db.fetchval("SELECT COUNT(*) FROM conversations WHERE company_id=$1", cid) or 0 if cid else 0
        )
        u["tickets_count"] = (
            await db.fetchval("SELECT COUNT(*) FROM tickets WHERE company_id=$1", cid) or 0 if cid else 0
        )
        u["products_count"] = (
            await db.fetchval("SELECT COUNT(*) FROM company_products WHERE company_id=$1", cid) or 0 if cid else 0
        )
    return users


@router.put("/platform/super-admin/users/{user_id}/status")
async def super_admin_update_user_status(user_id: str, request: Request):
    db = _db(request)
    cu = await require_roles(request, ["super_admin"])
    body = await request.json()
    status = body.get("status", "")
    allowed = ("active", "paused", "blocked", "inactive")
    if status not in allowed:
        raise HTTPException(400, f"Invalid status. Must be one of: {', '.join(allowed)}")
    if user_id == cu["sub"]:
        raise HTTPException(400, "Cannot change your own status")
    if not await db.fetchval("SELECT id FROM users WHERE id=$1", user_id):
        raise HTTPException(404, "User not found")
    await db.execute("UPDATE users SET status=$1,updated_at=NOW() WHERE id=$2", status, user_id)
    await _revoke_user_access_after_status_change(db, user_id, status)
    return {"status": "ok", "user_id": user_id, "new_status": status}


@router.delete("/platform/super-admin/users/{user_id}")
async def super_admin_delete_user(user_id: str, request: Request):
    db = _db(request)
    cu = await require_roles(request, ["super_admin"])
    if user_id == cu["sub"]:
        raise HTTPException(400, "Cannot delete your own account")
    target = r(await db.fetchrow("SELECT * FROM users WHERE id=$1 LIMIT 1", user_id))
    if not target:
        raise HTTPException(404, "User not found")
    await delete_user_account_records(db, target, bool(target.get("company_id")) and target.get("role") == "admin")
    return {"status": "deleted", "user_id": user_id}
