"""
Idempotent demo tenant + users when DEMO_MODE is enabled.

Credentials (override via env):
  DEMO_USER_EMAIL / DEMO_USER_PASSWORD     — tenant admin
  DEMO_SUPERADMIN_EMAIL / same password    — platform super_admin (uses SUPER_ADMIN_COMPANY_ID)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from core.utils import make_id
from models.reference_data import ensure_company_reference_data, resolve_role_id
from services.billing_helpers import (
    get_or_create_billing_customer,
    is_demo_mode,
    upsert_subscription,
)
from shared.auth.jwt import hash_password
from shared.billing_cache import invalidate_billing_cache

logger = logging.getLogger(__name__)

DEMO_TENANT_ID = (os.environ.get("DEMO_TENANT_ID") or "e1111111-1111-4111-8111-111111111111").strip()
DEMO_USER_EMAIL = (os.environ.get("DEMO_USER_EMAIL") or "demo@pulseengine.local").strip().lower()
DEMO_SUPER_EMAIL = (os.environ.get("DEMO_SUPERADMIN_EMAIL") or "superadmin@pulseengine.local").strip().lower()
DEMO_PASSWORD = (os.environ.get("DEMO_ACCOUNTS_PASSWORD") or "DemoPulse2026!").strip()
_PLATFORM_COMPANY_ID = (
    os.environ.get("SUPER_ADMIN_COMPANY_ID") or ""
).strip() or "00000000-0000-0000-0000-000000000001"
_DEMO_DUPLICATE_AVATAR = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 128'>"
    "<rect width='128' height='128' rx='28' fill='%230f172a'/>"
    "<circle cx='64' cy='44' r='22' fill='%23f8fafc'/>"
    "<path d='M26 110c6-23 27-36 38-36s32 13 38 36' fill='%2322c55e'/>"
    "</svg>"
)


async def _ensure_duplicate_demo_customers(db, company_id: str) -> None:
    sample_rows = (
        {
            "email": "jordan.blake.duplicate.1@pulseengine.local",
            "phone": "+15550001101",
            "name": "Jordan Blake",
            "segment": "enterprise",
        },
        {
            "email": "jordan.blake.duplicate.2@pulseengine.local",
            "phone": "+15550001102",
            "name": "Jordan Blake",
            "segment": "enterprise",
        },
    )
    for sample in sample_rows:
        existing = await db.fetchrow(
            "SELECT id FROM customers WHERE company_id=$1 AND email=$2 LIMIT 1",
            company_id,
            sample["email"],
        )
        if existing:
            await db.execute(
                "UPDATE customers SET name=$1,phone=$2,segment=$3,avatar=$4,lifecycle_stage='lead',updated_at=NOW() "
                "WHERE id=$5 AND company_id=$6",
                sample["name"],
                sample["phone"],
                sample["segment"],
                _DEMO_DUPLICATE_AVATAR,
                existing["id"],
                company_id,
            )
            continue
        await db.execute(
            "INSERT INTO customers(id,company_id,lead_id,name,email,phone,customer_company_name,segment,avatar,lifecycle_stage,lifetime_value,avg_sentiment,recent_tickets,complaint_count,days_since_last_contact,total_conversations,created_at,updated_at) "  # noqa: E501
            "VALUES($1,$2,'',$3,$4,$5,'Duplicate Test Co',$6,$7,'lead',0,0,0,0,0,0,NOW(),NOW())",
            make_id(),
            company_id,
            sample["name"],
            sample["email"],
            sample["phone"],
            sample["segment"],
            _DEMO_DUPLICATE_AVATAR,
        )


async def ensure_demo_accounts(db) -> None:
    if not is_demo_mode():
        return
    if not DEMO_PASSWORD:
        logger.warning("demo_seed skipped: DEMO_ACCOUNTS_PASSWORD empty")
        return

    pwd_hash = hash_password(DEMO_PASSWORD)

    # ── Demo tenant company + admin ─────────────────────────────────────────
    await db.execute(
        "INSERT INTO companies(id,name,is_active,created_at,updated_at) "
        "VALUES($1,$2,TRUE,NOW(),NOW()) "
        "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name,is_active=TRUE,updated_at=NOW()",
        DEMO_TENANT_ID,
        "Demo Workspace",
    )
    await db.execute(
        "INSERT INTO company_settings(id,company_id,created_at,updated_at) "
        "VALUES($1,$2,NOW(),NOW()) ON CONFLICT (company_id) DO NOTHING",
        DEMO_TENANT_ID,
        DEMO_TENANT_ID,
    )
    await ensure_company_reference_data(db, DEMO_TENANT_ID)

    admin_role_id = await resolve_role_id(db, "admin")
    demo_user_id = make_id()
    await db.execute(
        """
        INSERT INTO users(
            id,email,password_hash,name,role,role_id,sub_role,status,avatar,company_id,phone,
            onboarding_completed,plan_selected,billing_status,auth_provider,email_verified,created_at,updated_at
        ) VALUES (
            $1,$2,$3,'Demo Admin','admin',$4,'','active','',$5,'',
            TRUE,TRUE,'active','email',TRUE,NOW(),NOW()
        )
        ON CONFLICT (company_id, email) DO UPDATE SET
            password_hash=EXCLUDED.password_hash,
            name=EXCLUDED.name,
            role='admin',
            role_id=EXCLUDED.role_id,
            company_id=EXCLUDED.company_id,
            onboarding_completed=TRUE,
            plan_selected=TRUE,
            billing_status='active',
            email_verified=TRUE,
            status='active',
            updated_at=NOW()
        """,
        demo_user_id,
        DEMO_USER_EMAIL,
        pwd_hash,
        admin_role_id,
        DEMO_TENANT_ID,
    )

    bc = await get_or_create_billing_customer(
        db,
        DEMO_TENANT_ID,
        billing_email=DEMO_USER_EMAIL,
        billing_name="Demo Workspace",
        payment_status="active",
    )
    period_end = datetime.now(timezone.utc) + timedelta(days=365)
    await upsert_subscription(
        db,
        DEMO_TENANT_ID,
        billing_customer_id=str(bc.get("id") or ""),
        plan_code="pro",
        status="trialing",
        stripe_subscription_id="",
        billing_interval="month",
        current_period_start=datetime.now(timezone.utc),
        current_period_end=period_end,
    )
    await invalidate_billing_cache(DEMO_TENANT_ID)
    await _ensure_duplicate_demo_customers(db, DEMO_TENANT_ID)

    # ── Platform super_admin (same company row as env bootstrap) ────────────
    await db.execute(
        "INSERT INTO companies(id,name,is_active,created_at,updated_at) "
        "VALUES($1,$2,TRUE,NOW(),NOW()) "
        "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name,is_active=TRUE,updated_at=NOW()",
        _PLATFORM_COMPANY_ID,
        "Pulse Engine Platform",
    )
    await db.execute(
        "INSERT INTO company_settings(id,company_id,created_at,updated_at) "
        "VALUES($1,$2,NOW(),NOW()) ON CONFLICT (company_id) DO NOTHING",
        _PLATFORM_COMPANY_ID,
        _PLATFORM_COMPANY_ID,
    )

    super_role_id = await resolve_role_id(db, "super_admin")
    super_user_id = make_id()
    await db.execute(
        """
        INSERT INTO users(
            id,email,password_hash,name,role,role_id,sub_role,status,avatar,company_id,phone,
            onboarding_completed,plan_selected,billing_status,auth_provider,email_verified,created_at,updated_at
        ) VALUES (
            $1,$2,$3,'Demo Super Admin','super_admin',$4,'','active','',$5,'',
            TRUE,TRUE,'active','email',TRUE,NOW(),NOW()
        )
        ON CONFLICT (company_id, email) DO UPDATE SET
            password_hash=EXCLUDED.password_hash,
            name=EXCLUDED.name,
            role='super_admin',
            role_id=EXCLUDED.role_id,
            company_id=EXCLUDED.company_id,
            onboarding_completed=TRUE,
            plan_selected=TRUE,
            billing_status='active',
            email_verified=TRUE,
            status='active',
            updated_at=NOW()
        """,
        super_user_id,
        DEMO_SUPER_EMAIL,
        pwd_hash,
        super_role_id,
        _PLATFORM_COMPANY_ID,
    )

    logger.info(
        "demo_seed ready tenant=%s user=%s super=%s (DEMO_MODE)",
        DEMO_TENANT_ID,
        DEMO_USER_EMAIL,
        DEMO_SUPER_EMAIL,
    )
