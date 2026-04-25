"""routers/customers.py — PostgreSQL version."""

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from services.ai_service.facade import calculate_churn_risk
from core.phone_normalization import strict_normalize_to_e164_digits
from core.utils import make_id, now_ts
from shared.database import create_detached_task
from services.db_helpers import (
    r,
    rs,
    get_current_user_flexible,
    get_company_id,
    ensure_customer_profile,
    create_notification,
)

logger = logging.getLogger(__name__)
router = APIRouter()

CUSTOMER_UPDATE_FIELDS = {
    "name",
    "email",
    "phone",
    "segment",
    "avatar",
    "lifecycle_stage",
    "customer_company_name",
    "lifetime_value",
    "avg_sentiment",
    "recent_tickets",
    "complaint_count",
    "days_since_last_contact",
    "total_conversations",
    "status",
    "notes",
    "source",
}


def _db(req):
    return req.app.state.db


def _serialize_customer(customer: dict | None) -> dict | None:
    if not customer:
        return customer
    customer["company"] = customer.get("customer_company_name", "")
    return customer


async def _load_social_profiles(db, customer_id: str) -> dict:
    rows = await db.fetch(
        "SELECT platform,profile_id FROM customer_social_profiles WHERE customer_id=$1 ORDER BY platform",
        customer_id,
    )
    return {row["platform"]: row["profile_id"] for row in rows if row.get("platform")}


@router.get("/customers")
async def list_customers(request: Request, segment: Optional[str] = None, search: Optional[str] = None):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    sql = "SELECT c.*,ARRAY(SELECT tag FROM customer_tags WHERE customer_id=c.id) AS tags,ARRAY(SELECT channel FROM customer_channels WHERE customer_id=c.id) AS channels FROM customers c WHERE c.company_id=$1 AND c.lifecycle_stage!='lead'"  # noqa: E501
    args = [cid]
    if segment:
        args.append(segment)
        sql += f" AND c.segment=${len(args)}"
    if search:
        args.append(f"%{search}%")
        name_idx = len(args)
        args.append(f"%{search}%")
        email_idx = len(args)
        sql += f" AND (c.name ILIKE ${name_idx} OR c.email ILIKE ${email_idx})"
    sql += " ORDER BY c.created_at DESC LIMIT 500"
    customers = rs(await db.fetch(sql, *args))
    for customer in customers:
        customer["social_profiles"] = await _load_social_profiles(db, customer["id"])
        _serialize_customer(customer)
    return customers


@router.get("/customers/{customer_id}")
async def get_customer(customer_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    customer = r(
        await db.fetchrow(
            "SELECT * FROM customers WHERE id=$1 AND company_id=$2 LIMIT 1",
            customer_id,
            cid,
        )
    )
    if not customer:
        raise HTTPException(404, "Customer not found")
    customer["conversation_count"] = (
        await db.fetchval(
            "SELECT COUNT(*) FROM conversations WHERE customer_id=$1 AND company_id=$2",
            customer_id,
            cid,
        )
        or 0
    )
    customer["churn_risk"] = calculate_churn_risk(customer)
    customer["tags"] = [
        row["tag"] for row in await db.fetch("SELECT tag FROM customer_tags WHERE customer_id=$1", customer_id)
    ]
    customer["channels"] = [
        row["channel"]
        for row in await db.fetch("SELECT channel FROM customer_channels WHERE customer_id=$1", customer_id)
    ]
    customer["social_profiles"] = await _load_social_profiles(db, customer_id)
    return _serialize_customer(customer)


@router.post("/customers")
async def create_customer(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    body = await request.json()
    if "company" in body and "customer_company_name" not in body:
        body["customer_company_name"] = body.pop("company")
    email = (body.get("email", "") or "").strip().lower()
    _raw = (body.get("phone", "") or "").strip()
    phone = ""
    if _raw:
        phone = strict_normalize_to_e164_digits(_raw) or ""
        if not phone:
            raise HTTPException(
                400,
                "Invalid phone number. Use a valid number in E.164 or international form (e.g. +92… or +44…), "
                "or a valid local number for WHATSAPP_DEFAULT_COUNTRY.",
            )
    name = (body.get("name", "") or "").strip()
    if not any([name, email, phone]):
        raise HTTPException(400, "Customer name, email, or phone is required")
    cust_id = make_id()
    await db.execute(
        "INSERT INTO customers(id,company_id,lead_id,name,email,phone,customer_company_name,segment,avatar,lifecycle_stage,lifetime_value,avg_sentiment,recent_tickets,complaint_count,days_since_last_contact,total_conversations,created_at,updated_at) VALUES($1,$2,'',$3,$4,$5,$6,$7,'','customer',0,0,0,0,0,0,NOW(),NOW())",  # noqa: E501
        cust_id,
        cid,
        name,
        email,
        phone,
        body.get("customer_company_name", body.get("company", "")),
        body.get("segment", "general"),
    )
    for tag in body.get("tags") or []:
        await db.execute(
            "INSERT INTO customer_tags(customer_id,tag) VALUES($1,$2) ON CONFLICT DO NOTHING",
            cust_id,
            tag,
        )
    for ch in body.get("channels") or []:
        await db.execute(
            "INSERT INTO customer_channels(customer_id,channel) VALUES($1,$2) ON CONFLICT DO NOTHING",
            cust_id,
            ch,
        )
    if isinstance(body.get("social_profiles"), dict):
        await db.executemany(
            "INSERT INTO customer_social_profiles(customer_id,platform,profile_id) VALUES($1,$2,$3)",
            [
                (cust_id, str(platform).strip().lower(), str(url).strip())
                for platform, url in body["social_profiles"].items()
                if str(url).strip()
            ],
        )
    cust = r(await db.fetchrow("SELECT * FROM customers WHERE id=$1 AND company_id=$2", cust_id, cid))
    if cust:
        cust["social_profiles"] = await _load_social_profiles(db, cust_id)
        _serialize_customer(cust)
    await ensure_customer_profile(db, cust)
    from core.socket import sio, connected_users

    await create_notification(
        db,
        sio,
        connected_users,
        cu,
        "Customer added",
        f"{body.get('name', '')} was created",
        "customer",
    )
    return cust


@router.put("/customers/{customer_id}")
async def update_customer(customer_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    body = await request.json()
    if "company" in body and "customer_company_name" not in body:
        body["customer_company_name"] = body.pop("company")
    body.pop("_id", None)
    tags = body.pop("tags", None)
    channels = body.pop("channels", None)
    social_profiles = body.pop("social_profiles", None)
    safe_body = {k: v for k, v in body.items() if k in CUSTOMER_UPDATE_FIELDS}
    if body and not safe_body:
        raise HTTPException(400, "No valid fields provided")
    if "phone" in safe_body and (safe_body.get("phone") or "").strip():
        _up = str(safe_body["phone"] or "").strip()
        out = strict_normalize_to_e164_digits(_up) or ""
        if not out:
            raise HTTPException(
                400,
                "Invalid phone number. Use a valid number in E.164 or international form (e.g. +92… or +44…), "
                "or a valid local number for WHATSAPP_DEFAULT_COUNTRY.",
            )
        safe_body["phone"] = out
    safe_body["updated_at"] = now_ts()
    if safe_body:
        columns = list(safe_body.keys())
        set_parts = ", ".join(f"{k}=${i + 3}" for i, k in enumerate(columns))
        values = [safe_body[col] for col in columns]
        await db.execute(
            f"UPDATE customers SET {set_parts} WHERE id=$1 AND company_id=$2",
            customer_id,
            cid,
            *values,
        )
    if tags is not None:
        await db.execute("DELETE FROM customer_tags WHERE customer_id=$1", customer_id)
        if tags:
            await db.executemany(
                "INSERT INTO customer_tags(customer_id,tag) VALUES($1,$2) ON CONFLICT DO NOTHING",
                [(customer_id, t) for t in tags],
            )
    if channels is not None:
        await db.execute("DELETE FROM customer_channels WHERE customer_id=$1", customer_id)
        if channels:
            await db.executemany(
                "INSERT INTO customer_channels(customer_id,channel) VALUES($1,$2) ON CONFLICT DO NOTHING",
                [(customer_id, ch) for ch in channels],
            )
    if social_profiles is not None:
        await db.execute("DELETE FROM customer_social_profiles WHERE customer_id=$1", customer_id)
        if isinstance(social_profiles, dict) and social_profiles:
            await db.executemany(
                "INSERT INTO customer_social_profiles(customer_id,platform,profile_id) VALUES($1,$2,$3)",
                [
                    (customer_id, str(platform).strip().lower(), str(url).strip())
                    for platform, url in social_profiles.items()
                    if str(url).strip()
                ],
            )
    customer = r(await db.fetchrow("SELECT * FROM customers WHERE id=$1 AND company_id=$2", customer_id, cid))
    if customer:
        customer["social_profiles"] = await _load_social_profiles(db, customer_id)
        _serialize_customer(customer)
    return customer


@router.delete("/customers/{customer_id}")
async def delete_customer(customer_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    await db.execute("DELETE FROM customers WHERE id=$1 AND company_id=$2", customer_id, cid)
    return {"status": "deleted"}


@router.get("/customers/{customer_id}/profile")
async def get_customer_profile(customer_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    if not await db.fetchval("SELECT id FROM customers WHERE id=$1 AND company_id=$2", customer_id, cid):
        raise HTTPException(404, "Customer not found")
    await ensure_customer_profile(db, {"id": customer_id, "company_id": cid})
    profile = r(await db.fetchrow("SELECT * FROM customer_profiles WHERE customer_id=$1", customer_id)) or {}
    prefs = rs(
        await db.fetch(
            "SELECT pref_key,pref_value FROM customer_profile_preferences WHERE customer_id=$1",
            customer_id,
        )
    )
    profile["preferences"] = {p["pref_key"]: p["pref_value"] for p in prefs}
    return profile


@router.put("/customers/{customer_id}/profile")
async def update_customer_profile(customer_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    body = await request.json()
    if not await db.fetchval("SELECT id FROM customers WHERE id=$1 AND company_id=$2", customer_id, cid):
        raise HTTPException(404, "Customer not found")
    await ensure_customer_profile(db, {"id": customer_id, "company_id": cid})
    engagement = body.get("engagement_level", "general") or "general"
    await db.execute(
        "UPDATE customer_profiles SET engagement_level=$1,last_interaction=NOW(),updated_at=NOW() WHERE customer_id=$2",
        engagement,
        customer_id,
    )
    if body.get("preferences"):
        await db.execute("DELETE FROM customer_profile_preferences WHERE customer_id=$1", customer_id)
        await db.executemany(
            "INSERT INTO customer_profile_preferences(customer_id,pref_key,pref_value) VALUES($1,$2,$3)",
            [(customer_id, k, str(v)) for k, v in body["preferences"].items()],
        )
    return r(await db.fetchrow("SELECT * FROM customer_profiles WHERE customer_id=$1", customer_id))


@router.get("/purchases")
async def list_purchases(request: Request, customer_id: Optional[str] = None):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    if customer_id:
        return rs(
            await db.fetch(
                "SELECT * FROM purchases WHERE company_id=$1 AND customer_id=$2 ORDER BY purchase_date DESC LIMIT 500",
                cid,
                customer_id,
            )
        )
    return rs(
        await db.fetch(
            "SELECT * FROM purchases WHERE company_id=$1 ORDER BY purchase_date DESC LIMIT 500",
            cid,
        )
    )


@router.post("/purchases")
async def create_purchase(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    body = await request.json()
    if not await db.fetchval(
        "SELECT id FROM customers WHERE id=$1 AND company_id=$2",
        body.get("customer_id", ""),
        cid,
    ):
        raise HTTPException(404, "Customer not found")
    purchase_id = make_id()
    await db.execute(
        "INSERT INTO purchases(id,customer_id,company_id,amount,currency,product_category,product_name,product_sku,purchase_date,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW(),NOW())",  # noqa: E501
        purchase_id,
        body["customer_id"],
        cid,
        float(body.get("amount", 0)),
        body.get("currency", "USD"),
        body.get("product_category", "general"),
        (body.get("product_details", {}) or {}).get("name", ""),
        (body.get("product_details", {}) or {}).get("sku", ""),
        body.get("purchase_date") or now_ts(),
    )
    await db.execute(
        "UPDATE customers SET lifetime_value=lifetime_value+$1,updated_at=NOW(),lifecycle_stage='customer' WHERE id=$2",
        float(body.get("amount", 0)),
        body["customer_id"],
    )
    create_detached_task(
        db.execute(
            "INSERT INTO journey_tracking(id,company_id,user_id,customer_id,purchase_id,phase,started_at,completed_at,created_at) VALUES($1,$2,$3,$4,$5,'purchase',NOW(),NOW(),NOW())",  # noqa: E501
            make_id(),
            cid,
            cu.get("sub", ""),
            body["customer_id"],
            purchase_id,
        )
    )
    return r(await db.fetchrow("SELECT * FROM purchases WHERE id=$1", purchase_id))
