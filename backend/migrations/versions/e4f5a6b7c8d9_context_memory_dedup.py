"""context_memory_dedup table

Extracts on-demand DDL from services/ai_service/memory_service.py into a
proper Alembic migration. Previously the table was created at runtime on
first write; now it is created deterministically during deployment.

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-05-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e4f5a6b7c8d9'
down_revision: Union[str, Sequence[str], None] = 'd3e4f5a6b7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS context_memory_dedup (
            company_id TEXT NOT NULL,
            convo_id TEXT NOT NULL DEFAULT '',
            message_id TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            memory_id TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (company_id, convo_id, message_id, memory_type)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_context_memory_dedup_company "
        "ON context_memory_dedup(company_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS context_memory_dedup")
