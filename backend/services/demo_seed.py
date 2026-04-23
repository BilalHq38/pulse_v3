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
