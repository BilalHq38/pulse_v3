"""Cleanup for legacy demo accounts.

Demo account creation is intentionally disabled for production stability. The
startup hook now removes only the known demo tenant/user rows that older builds
created, leaving the configured primary super admin untouched.
"""

from __future__ import annotations

import logging
import os

from shared.billing_cache import invalidate_billing_cache

logger = logging.getLogger(__name__)

DEMO_TENANT_ID = (os.environ.get("DEMO_TENANT_ID") or "e1111111-1111-4111-8111-111111111111").strip()
DEMO_USER_EMAIL = (os.environ.get("DEMO_USER_EMAIL") or "demo@pulseengine.local").strip().lower()
DEMO_SUPER_EMAIL = (os.environ.get("DEMO_SUPERADMIN_EMAIL") or "superadmin@pulseengine.local").strip().lower()


async def ensure_demo_accounts(db) -> None:
    primary_super_email = (
        os.environ.get("SUPER_ADMIN_EMAIL") or os.environ.get("IDENTITY_ADMIN_EMAIL") or ""
    ).strip().lower()
    await db.execute("DELETE FROM companies WHERE id=$1", DEMO_TENANT_ID)
    await db.execute("DELETE FROM users WHERE LOWER(email)=LOWER($1)", DEMO_USER_EMAIL)
    if DEMO_SUPER_EMAIL and DEMO_SUPER_EMAIL != primary_super_email:
        await db.execute("DELETE FROM users WHERE LOWER(email)=LOWER($1)", DEMO_SUPER_EMAIL)
    await invalidate_billing_cache(DEMO_TENANT_ID)
    logger.info("legacy demo accounts cleaned tenant=%s", DEMO_TENANT_ID)
