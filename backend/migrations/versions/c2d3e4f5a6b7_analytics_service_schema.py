"""analytics_service schema tables with RLS

Extracts runtime DDL from data_pipeline/bootstrap.py into a proper
Alembic migration. Creates the analytics_service schema, all 7 tables,
14 indexes, and per-tenant RLS policies.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-05-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c2d3e4f5a6b7'
down_revision: Union[str, Sequence[str], None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SCHEMA = "analytics_service"
_RLS_TABLES = (
    "raw_events",
    "raw_messages",
    "raw_leads",
    "analytics_events",
    "lead_metrics",
    "conversation_metrics",
    "sentiment_logs",
)


def upgrade() -> None:
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{_SCHEMA}"')
    op.execute(f'SET search_path TO "{_SCHEMA}", public')

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.raw_events (
            id TEXT PRIMARY KEY,
            company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
            source TEXT NOT NULL,
            event_type TEXT NOT NULL DEFAULT '',
            event_id TEXT NOT NULL,
            external_id TEXT NOT NULL DEFAULT '',
            payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            payload_hash TEXT NOT NULL DEFAULT '',
            processing_status TEXT NOT NULL DEFAULT 'queued',
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            processed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_raw_events_company_event UNIQUE (company_id, source, event_id)
        )
    """)
    op.execute(f"CREATE INDEX IF NOT EXISTS idx_raw_events_company_created_at ON {_SCHEMA}.raw_events(company_id, created_at)")
    op.execute(f"CREATE INDEX IF NOT EXISTS idx_raw_events_company_status ON {_SCHEMA}.raw_events(company_id, processing_status)")

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.raw_messages (
            id TEXT PRIMARY KEY,
            company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
            source TEXT NOT NULL,
            event_id TEXT NOT NULL DEFAULT '',
            dedupe_key TEXT NOT NULL,
            canonical_message_id TEXT NOT NULL DEFAULT '',
            external_message_id TEXT NOT NULL DEFAULT '',
            conversation_id TEXT NOT NULL DEFAULT '',
            sender_type TEXT NOT NULL DEFAULT '',
            payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            payload_hash TEXT NOT NULL DEFAULT '',
            processing_status TEXT NOT NULL DEFAULT 'queued',
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            processed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_raw_messages_company_dedupe UNIQUE (company_id, dedupe_key)
        )
    """)
    op.execute(f"ALTER TABLE {_SCHEMA}.raw_messages ADD COLUMN IF NOT EXISTS event_id TEXT NOT NULL DEFAULT ''")
    op.execute(
        f"UPDATE {_SCHEMA}.raw_messages SET event_id = dedupe_key "
        "WHERE (event_id IS NULL OR event_id = '') AND dedupe_key <> ''"
    )
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_messages_company_event_id "
        f"ON {_SCHEMA}.raw_messages(company_id, event_id) WHERE BTRIM(event_id) <> ''"
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS idx_raw_messages_company_created_at ON {_SCHEMA}.raw_messages(company_id, created_at)")
    op.execute(f"CREATE INDEX IF NOT EXISTS idx_raw_messages_company_conversation ON {_SCHEMA}.raw_messages(company_id, conversation_id, created_at)")
    op.execute(f"CREATE INDEX IF NOT EXISTS idx_raw_messages_company_message ON {_SCHEMA}.raw_messages(company_id, canonical_message_id)")
    op.execute(f"CREATE INDEX IF NOT EXISTS idx_raw_messages_company_status ON {_SCHEMA}.raw_messages(company_id, processing_status)")

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.raw_leads (
            id TEXT PRIMARY KEY,
            company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
            source TEXT NOT NULL,
            event_id TEXT NOT NULL DEFAULT '',
            dedupe_key TEXT NOT NULL,
            canonical_lead_id TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            payload_hash TEXT NOT NULL DEFAULT '',
            processing_status TEXT NOT NULL DEFAULT 'queued',
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            processed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_raw_leads_company_dedupe UNIQUE (company_id, dedupe_key)
        )
    """)
    op.execute(f"ALTER TABLE {_SCHEMA}.raw_leads ADD COLUMN IF NOT EXISTS event_id TEXT NOT NULL DEFAULT ''")
    op.execute(
        f"UPDATE {_SCHEMA}.raw_leads SET event_id = dedupe_key "
        "WHERE (event_id IS NULL OR event_id = '') AND dedupe_key <> ''"
    )
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_leads_company_event_id "
        f"ON {_SCHEMA}.raw_leads(company_id, event_id) WHERE BTRIM(event_id) <> ''"
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS idx_raw_leads_company_created_at ON {_SCHEMA}.raw_leads(company_id, created_at)")
    op.execute(f"CREATE INDEX IF NOT EXISTS idx_raw_leads_company_lead ON {_SCHEMA}.raw_leads(company_id, canonical_lead_id)")
    op.execute(f"CREATE INDEX IF NOT EXISTS idx_raw_leads_company_status ON {_SCHEMA}.raw_leads(company_id, processing_status)")

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.analytics_events (
            id TEXT PRIMARY KEY,
            company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
            raw_table TEXT NOT NULL,
            raw_id TEXT NOT NULL,
            event_kind TEXT NOT NULL,
            event_source TEXT NOT NULL DEFAULT '',
            entity_type TEXT NOT NULL DEFAULT '',
            entity_id TEXT NOT NULL DEFAULT '',
            conversation_id TEXT NOT NULL DEFAULT '',
            lead_id TEXT NOT NULL DEFAULT '',
            customer_id TEXT NOT NULL DEFAULT '',
            metric_date DATE NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_analytics_events_company_raw_kind UNIQUE (company_id, raw_table, raw_id, event_kind)
        )
    """)
    op.execute(f"CREATE INDEX IF NOT EXISTS idx_analytics_events_company_date ON {_SCHEMA}.analytics_events(company_id, metric_date)")
    op.execute(f"CREATE INDEX IF NOT EXISTS idx_analytics_events_company_kind ON {_SCHEMA}.analytics_events(company_id, event_kind, occurred_at)")

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.lead_metrics (
            id TEXT PRIMARY KEY,
            company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
            lead_id TEXT NOT NULL,
            metric_date DATE NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            phase TEXT NOT NULL DEFAULT '',
            grade TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            current_score INTEGER NOT NULL DEFAULT 0,
            recommended_score INTEGER NOT NULL DEFAULT 0,
            recommended_grade TEXT NOT NULL DEFAULT '',
            scoring_reason TEXT NOT NULL DEFAULT '',
            next_action TEXT NOT NULL DEFAULT '',
            duplicate_count INTEGER NOT NULL DEFAULT 0,
            is_duplicate BOOLEAN NOT NULL DEFAULT FALSE,
            is_converted BOOLEAN NOT NULL DEFAULT FALSE,
            payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_lead_metrics_company_lead_date UNIQUE (company_id, lead_id, metric_date)
        )
    """)
    op.execute(f"ALTER TABLE {_SCHEMA}.lead_metrics DROP CONSTRAINT IF EXISTS fk_lead_metrics_lead_company")
    op.execute(f"CREATE INDEX IF NOT EXISTS idx_lead_metrics_company_date ON {_SCHEMA}.lead_metrics(company_id, metric_date)")
    op.execute(f"CREATE INDEX IF NOT EXISTS idx_lead_metrics_company_status ON {_SCHEMA}.lead_metrics(company_id, status, metric_date)")

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.conversation_metrics (
            id TEXT PRIMARY KEY,
            company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
            conversation_id TEXT NOT NULL,
            metric_date DATE NOT NULL,
            channel TEXT NOT NULL DEFAULT 'web_chat',
            status TEXT NOT NULL DEFAULT 'open',
            customer_id TEXT NOT NULL DEFAULT '',
            ai_handled BOOLEAN NOT NULL DEFAULT TRUE,
            escalated BOOLEAN NOT NULL DEFAULT FALSE,
            total_messages INTEGER NOT NULL DEFAULT 0,
            customer_messages INTEGER NOT NULL DEFAULT 0,
            agent_messages INTEGER NOT NULL DEFAULT 0,
            ai_messages INTEGER NOT NULL DEFAULT 0,
            system_messages INTEGER NOT NULL DEFAULT 0,
            unread_count INTEGER NOT NULL DEFAULT 0,
            avg_sentiment NUMERIC(5,4) NOT NULL DEFAULT 0,
            latest_sentiment_label TEXT NOT NULL DEFAULT 'neutral',
            latest_sentiment_score NUMERIC(5,4),
            latest_intent_type TEXT NOT NULL DEFAULT '',
            first_message_at TIMESTAMPTZ,
            last_message_at TIMESTAMPTZ,
            response_time_minutes NUMERIC(10,2) NOT NULL DEFAULT 0,
            payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_conversation_metrics_company_conversation_date UNIQUE (company_id, conversation_id, metric_date)
        )
    """)
    op.execute(f"ALTER TABLE {_SCHEMA}.conversation_metrics DROP CONSTRAINT IF EXISTS fk_conversation_metrics_conversation_company")
    op.execute(f"CREATE INDEX IF NOT EXISTS idx_conversation_metrics_company_date ON {_SCHEMA}.conversation_metrics(company_id, metric_date)")
    op.execute(f"CREATE INDEX IF NOT EXISTS idx_conversation_metrics_company_channel ON {_SCHEMA}.conversation_metrics(company_id, channel, metric_date)")

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.sentiment_logs (
            id TEXT PRIMARY KEY,
            company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
            raw_table TEXT NOT NULL,
            raw_id TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            entity_type TEXT NOT NULL DEFAULT '',
            entity_id TEXT NOT NULL DEFAULT '',
            conversation_id TEXT NOT NULL DEFAULT '',
            message_id TEXT NOT NULL DEFAULT '',
            sentiment_label TEXT NOT NULL DEFAULT 'neutral',
            sentiment_score NUMERIC(5,4),
            emotion TEXT NOT NULL DEFAULT '',
            confidence NUMERIC(5,4),
            intent_type TEXT NOT NULL DEFAULT '',
            analyzed_text TEXT NOT NULL DEFAULT '',
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_sentiment_logs_company_raw UNIQUE (company_id, raw_table, raw_id)
        )
    """)
    op.execute(f"CREATE INDEX IF NOT EXISTS idx_sentiment_logs_company_date ON {_SCHEMA}.sentiment_logs(company_id, occurred_at)")
    op.execute(f"CREATE INDEX IF NOT EXISTS idx_sentiment_logs_company_label ON {_SCHEMA}.sentiment_logs(company_id, sentiment_label, occurred_at)")

    # Enable RLS on all analytics tables
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {_SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {_SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS p_{table}_tenant ON {_SCHEMA}.{table}")
        op.execute(
            f"CREATE POLICY p_{table}_tenant ON {_SCHEMA}.{table} "
            "USING (company_id = current_setting('app.current_company', true)) "
            "WITH CHECK (company_id = current_setting('app.current_company', true))"
        )


def downgrade() -> None:
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
