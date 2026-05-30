"""pending_signups table and stripe column compatibility

Extracts runtime DDL from services/public_signup_service.py into a proper
Alembic migration. Creates the pending_signups table and applies the Stripe
column compatibility migration (drop legacy unique constraint, make nullable).

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-05-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd3e4f5a6b7c8'
down_revision: Union[str, Sequence[str], None] = 'c2d3e4f5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.pending_signups (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            company_name TEXT NOT NULL DEFAULT '',
            company_industry TEXT NOT NULL DEFAULT '',
            timezone TEXT NOT NULL DEFAULT 'UTC',
            plan_code TEXT NOT NULL DEFAULT 'pro',
            status TEXT NOT NULL DEFAULT 'pending_payment',
            payment_status TEXT NOT NULL DEFAULT 'pending',
            stripe_checkout_session_id TEXT,
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            stripe_event_id TEXT,
            company_id TEXT NOT NULL DEFAULT '',
            user_id TEXT NOT NULL DEFAULT '',
            verification_email_sent_at TIMESTAMPTZ,
            verification_error TEXT NOT NULL DEFAULT '',
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_pending_signups_email UNIQUE (email)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_pending_signups_status ON public.pending_signups(status, updated_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_pending_signups_expires_at ON public.pending_signups(expires_at)")

    # Drop legacy unique constraint that blocked multiple pending rows per checkout session
    for constraint_name in ("uq_pending_signups_checkout", "pending_signups_stripe_checkout_session_id_key"):
        op.execute(f"ALTER TABLE public.pending_signups DROP CONSTRAINT IF EXISTS {constraint_name}")

    # Normalize empty-string Stripe columns to NULL and make them nullable
    for col in ("stripe_checkout_session_id", "stripe_customer_id", "stripe_subscription_id", "stripe_event_id"):
        op.execute(
            f"UPDATE public.pending_signups SET {col} = NULL "
            f"WHERE {col} IS NOT NULL AND BTRIM({col}::text) = ''"
        )
        op.execute(f"ALTER TABLE public.pending_signups ALTER COLUMN {col} DROP NOT NULL")

    # Partial unique index: only enforce uniqueness for non-null, non-empty session IDs
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_pending_signups_checkout_nonempty
        ON public.pending_signups (stripe_checkout_session_id)
        WHERE stripe_checkout_session_id IS NOT NULL
          AND BTRIM(stripe_checkout_session_id) <> ''
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.pending_signups CASCADE")
