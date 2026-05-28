"""routers/customers.py - PostgreSQL version."""

import logging
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile

from channel_layer.channel_identity import company_default_phone_region
from core.phone_normalization import strict_normalize_to_e164_digits
from core.utils import make_id, now_ts
from services.ai_service.facade import calculate_churn_risk
from services.db_helpers import (
    create_notification,
    ensure_customer_profile,
    get_company_id,
    get_current_user_flexible,
    r,
    rs,
)
from services.lead_stage_service import transition_lead_stage
from shared.tabular_uploads import parse_tabular_upload, phone_region_from_upload_row, split_multi_value
from shared.webhook_task_runner import create_safe_detached_task

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


def _row_has_any(row: dict, *keys: str) -> bool:
    return any(key in row for key in keys)


def _get_first_value(row: dict, *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _normalize_customer_tags(raw_value: str) -> list[str]:
    tags: list[str] = []
    for item in split_multi_value(raw_value):
        normalized = str(item or "").strip()
        if normalized and normalized not in tags:
            tags.append(normalized)
    return tags


def _normalize_customer_channels(raw_value: str) -> list[str]:
    allowed = {"whatsapp", "email", "facebook", "instagram", "web_chat"}
    channels: list[str] = []
    for item in split_multi_value(raw_value):
        normalized = str(item or "").strip().lower().replace(" ", "_").replace("-", "_")
        if normalized in allowed and normalized not in channels:
            channels.append(normalized)
    return channels


async def _find_existing_customer_for_upload(db, company_id: str, *, phone: str = "", email: str = "") -> dict | None:
    if phone:
        customer = r(
            await db.fetchrow(
                "SELECT * FROM customers WHERE company_id=$1 AND phone=$2 AND lifecycle_stage!='lead' LIMIT 1",
                company_id,
                phone,
            )
        )
        if customer:
            return customer
    if email:
        return r(
            await db.fetchrow(
                "SELECT * FROM customers WHERE company_id=$1 AND email=$2 AND lifecycle_stage!='lead' LIMIT 1",
                company_id,
                email,
            )
        )
    return None


async def _sync_customer_links(
    db,
    customer_id: str,
    *,
    tags: list[str] | None = None,
    channels: list[str] | None = None,
) -> None:
    if tags is not None:
        await db.execute("DELETE FROM customer_tags WHERE customer_id=$1", customer_id)
        if tags:
            await db.executemany(
                "INSERT INTO customer_tags(customer_id,tag) VALUES($1,$2) ON CONFLICT DO NOTHING",
                [(customer_id, tag) for tag in tags],
            )
    if channels is not None:
        await db.execute("DELETE FROM customer_channels WHERE customer_id=$1", customer_id)
        if channels:
            await db.executemany(
                "INSERT INTO customer_channels(customer_id,channel) VALUES($1,$2) ON CONFLICT DO NOTHING",
                [(customer_id, channel) for channel in channels],
            )


async def _load_social_profiles(db, customer_id: str) -> dict:
    rows = await db.fetch(
        "SELECT platform,profile_id FROM customer_social_profiles WHERE customer_id=$1 ORDER BY platform",
        customer_id,
    )
    return {row["platform"]: row["profile_id"] for row in rows if row.get("platform")}


async def _fetch_customers(
    db,
    company_id: str,
    *,
    segment: str = "",
    search: str = "",
    limit: int = 500,
) -> list[dict]:
    if not company_id:
        return []

    sql = (
        "SELECT c.*, "
        "ARRAY(SELECT tag FROM customer_tags WHERE customer_id=c.id) AS tags, "
        "ARRAY(SELECT channel FROM customer_channels WHERE customer_id=c.id) AS channels "
        "FROM customers c WHERE c.company_id=$1 AND c.lifecycle_stage != 'lead'"
    )
    args = [company_id]
    if segment:
        args.append(segment)
        sql += f" AND c.segment=${len(args)}"
    if search:
        pattern = f"%{search}%"
        args.extend([pattern, pattern, pattern, pattern])
        sql += (
            f" AND (c.name ILIKE ${len(args) - 3} "
            f"OR c.email ILIKE ${len(args) - 2} "
            f"OR c.phone ILIKE ${len(args) - 1} "
            f"OR c.customer_company_name ILIKE ${len(args)})"
        )
    safe_limit = max(1, min(int(limit or 500), 500))
    args.append(safe_limit)
    sql += f" ORDER BY c.created_at DESC LIMIT ${len(args)}"

    customers = rs(await db.fetch(sql, *args))
    for customer in customers:
        customer["social_profiles"] = await _load_social_profiles(db, customer["id"])
        _serialize_customer(customer)
    return customers


@router.get("/customers")
async def list_customers(request: Request, segment: Optional[str] = None, search: Optional[str] = None):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    return await _fetch_customers(db, cid or "", segment=segment or "", search=search or "", limit=500)


@router.get("/customers/search")
async def search_customers(
    request: Request,
    q: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=500),
):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    query = (q or search or "").strip()
    return await _fetch_customers(db, cid or "", search=query, limit=limit)


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
                "Invalid phone number. Use a valid number in E.164 or international form (e.g. +92... or +44...), "
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
    from core.socket import connected_users, sio

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
                "Invalid phone number. Use a valid number in E.164 or international form (e.g. +92... or +44...), "
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


@router.post("/customers/bulk-upload")
async def bulk_upload_customers(request: Request, file: UploadFile = File(...)):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    rows = parse_tabular_upload(file.filename or "", await file.read())
    tenant_phone_region = await company_default_phone_region(db, cid)
    summary = {
        "total_rows": len(rows),
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
    }

    for entry in rows:
        row_number = entry["row_number"]
        row = entry["data"]
        try:
            name = _get_first_value(row, "name", "full_name", "customer_name")
            email = _get_first_value(row, "email", "email_address", "e_mail").lower()
            raw_phone = _get_first_value(row, "phone", "phone_number", "mobile", "mobile_number", "whatsapp")
            company_name = _get_first_value(
                row,
                "company",
                "customer_company_name",
                "company_name",
                "organization",
            )
            segment_value = _get_first_value(row, "segment", "customer_segment")
            tags = _normalize_customer_tags(_get_first_value(row, "tags", "labels"))
            channels = _normalize_customer_channels(_get_first_value(row, "channels", "preferred_channels"))

            if not any([name, email, raw_phone]):
                summary["skipped"] += 1
                continue

            normalized_phone = ""
            if raw_phone:
                phone_region = phone_region_from_upload_row(row) or tenant_phone_region
                normalized_phone = strict_normalize_to_e164_digits(raw_phone, fallback_region=phone_region or None) or ""
                if not normalized_phone:
                    raise HTTPException(
                        400,
                        "Invalid phone number. Use a valid international number such as +1..., +44..., or +92..., "
                        "or include a country/region code for local numbers.",
                    )

            existing = await _find_existing_customer_for_upload(
                db,
                cid,
                phone=normalized_phone,
                email=email,
            )

            if existing:
                update_fields: dict[str, str] = {}
                if name:
                    update_fields["name"] = name
                if email:
                    update_fields["email"] = email
                if normalized_phone:
                    update_fields["phone"] = normalized_phone
                if company_name:
                    update_fields["customer_company_name"] = company_name
                if segment_value:
                    update_fields["segment"] = segment_value
                if update_fields:
                    update_fields["updated_at"] = now_ts()
                    columns = list(update_fields.keys())
                    set_parts = ", ".join(f"{key}=${index + 3}" for index, key in enumerate(columns))
                    await db.execute(
                        f"UPDATE customers SET {set_parts} WHERE id=$1 AND company_id=$2",
                        existing["id"],
                        cid,
                        *[update_fields[column] for column in columns],
                    )
                await _sync_customer_links(
                    db,
                    existing["id"],
                    tags=tags if _row_has_any(row, "tags", "labels") else None,
                    channels=channels if _row_has_any(row, "channels", "preferred_channels") else None,
                )
                summary["updated"] += 1
                continue

            customer_id = make_id()
            await db.execute(
                "INSERT INTO customers(id,company_id,lead_id,name,email,phone,customer_company_name,segment,avatar,lifecycle_stage,lifetime_value,avg_sentiment,recent_tickets,complaint_count,days_since_last_contact,total_conversations,created_at,updated_at) VALUES($1,$2,'',$3,$4,$5,$6,$7,'','customer',0,0,0,0,0,0,NOW(),NOW())",  # noqa: E501
                customer_id,
                cid,
                name,
                email,
                normalized_phone,
                company_name,
                segment_value or "general",
            )
            await _sync_customer_links(
                db,
                customer_id,
                tags=tags if _row_has_any(row, "tags", "labels") else None,
                channels=channels if _row_has_any(row, "channels", "preferred_channels") else None,
            )
            created_customer = r(
                await db.fetchrow("SELECT * FROM customers WHERE id=$1 AND company_id=$2", customer_id, cid)
            )
            await ensure_customer_profile(db, created_customer)
            summary["created"] += 1
        except HTTPException as exc:
            summary["errors"].append({"row": row_number, "error": str(exc.detail)})
        except Exception as exc:  # pragma: no cover - defensive import path
            logger.exception("customer bulk upload failed row=%s", row_number)
            summary["errors"].append({"row": row_number, "error": str(exc)})

    return summary


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
    lead = r(
        await db.fetchrow(
            "SELECT l.* FROM leads l JOIN customers c ON c.lead_id=l.id "
            "WHERE c.id=$1 AND c.company_id=$2 AND l.company_id=$2 LIMIT 1",
            body["customer_id"],
            cid,
        )
    )
    if lead:
        try:
            await transition_lead_stage(
                db,
                lead,
                "won",
                reason="Successful payment or purchase recorded",
                source="payment",
                confidence=1.0,
                changed_by_user_id=cu.get("sub", ""),
                event_id=f"purchase:{purchase_id}",
                automatic=True,
            )
        except Exception as exc:
            logger.warning("purchase lead stage transition failed purchase_id=%s lead_id=%s: %s", purchase_id, lead.get("id"), exc)
    create_safe_detached_task(
        db,
        db.execute(
            "INSERT INTO journey_tracking(id,company_id,user_id,customer_id,purchase_id,phase,started_at,completed_at,created_at) VALUES($1,$2,$3,$4,$5,'purchase',NOW(),NOW(),NOW())",  # noqa: E501
            make_id(),
            cid,
            cu.get("sub", ""),
            body["customer_id"],
            purchase_id,
        ),
        name=f"customer-purchase-journey-{purchase_id}",
        company_id=cid,
        channel="customer",
        event_id=purchase_id,
    )
    return r(await db.fetchrow("SELECT * FROM purchases WHERE id=$1", purchase_id))
