"""company billing summary columns

Revision ID: a7b8c9d0e1f2
Revises: f5a6b7c8d9e0
Create Date: 2026-05-31

"""
from typing import Sequence, Union

from alembic import op


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS plan TEXT NOT NULL DEFAULT 'free'")
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS subscription_status TEXT NOT NULL DEFAULT 'inactive'")
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS billing_status TEXT NOT NULL DEFAULT 'inactive'")
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT NOT NULL DEFAULT ''")
    op.execute(
        """
        UPDATE companies c
        SET
            plan = COALESCE(s.plan_code, c.plan, 'free'),
            subscription_status = COALESCE(s.status, c.subscription_status, 'inactive'),
            billing_status = COALESCE(bc.payment_status, c.billing_status, 'inactive'),
            stripe_subscription_id = COALESCE(s.stripe_subscription_id, c.stripe_subscription_id, ''),
            updated_at = NOW()
        FROM subscriptions s
        LEFT JOIN billing_customers bc ON bc.company_id = s.company_id
        WHERE s.company_id = c.id
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS stripe_subscription_id")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS billing_status")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS subscription_status")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS plan")
