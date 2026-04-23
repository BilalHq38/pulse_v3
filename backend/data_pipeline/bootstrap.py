from __future__ import annotations

import asyncio

_PIPELINE_SCHEMA = "analytics_service"

_SCHEMA_READY = False
_SCHEMA_LOCK = asyncio.Lock()

_DDL_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS raw_events (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
        source TEXT NOT NULL,
        event_type TEXT NOT NULL DEFAULT '',
        event_id TEXT NOT NULL,
        external_id TEXT NOT NULL DEFAULT '',
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
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
    """,
    "CREATE INDEX IF NOT EXISTS idx_raw_events_company_created_at ON raw_events(company_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_raw_events_company_status ON raw_events(company_id, processing_status)",
    """
    CREATE TABLE IF NOT EXISTS raw_messages (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
        source TEXT NOT NULL,
        event_id TEXT NOT NULL DEFAULT '',
        dedupe_key TEXT NOT NULL,
        canonical_message_id TEXT NOT NULL DEFAULT '',
        external_message_id TEXT NOT NULL DEFAULT '',
        conversation_id TEXT NOT NULL DEFAULT '',
        sender_type TEXT NOT NULL DEFAULT '',
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
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
    """,
    # Backfill + uniqueness for idempotency across sources (safe no-op if already applied).
    "ALTER TABLE raw_messages ADD COLUMN IF NOT EXISTS event_id TEXT NOT NULL DEFAULT ''",
    "UPDATE raw_messages SET event_id = dedupe_key WHERE (event_id IS NULL OR event_id = '') AND dedupe_key <> ''",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_messages_company_event_id ON raw_messages(company_id, event_id) WHERE BTRIM(event_id) <> ''",  # noqa: E501
    "CREATE INDEX IF NOT EXISTS idx_raw_messages_company_created_at ON raw_messages(company_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_raw_messages_company_conversation ON raw_messages(company_id, conversation_id, created_at)",  # noqa: E501
    "CREATE INDEX IF NOT EXISTS idx_raw_messages_company_message ON raw_messages(company_id, canonical_message_id)",
    "CREATE INDEX IF NOT EXISTS idx_raw_messages_company_status ON raw_messages(company_id, processing_status)",
    """
    CREATE TABLE IF NOT EXISTS raw_leads (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
        source TEXT NOT NULL,
        event_id TEXT NOT NULL DEFAULT '',
        dedupe_key TEXT NOT NULL,
        canonical_lead_id TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '',
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
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
    """,
    "ALTER TABLE raw_leads ADD COLUMN IF NOT EXISTS event_id TEXT NOT NULL DEFAULT ''",
    "UPDATE raw_leads SET event_id = dedupe_key WHERE (event_id IS NULL OR event_id = '') AND dedupe_key <> ''",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_leads_company_event_id ON raw_leads(company_id, event_id) WHERE BTRIM(event_id) <> ''",  # noqa: E501
    "CREATE INDEX IF NOT EXISTS idx_raw_leads_company_created_at ON raw_leads(company_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_raw_leads_company_lead ON raw_leads(company_id, canonical_lead_id)",
    "CREATE INDEX IF NOT EXISTS idx_raw_leads_company_status ON raw_leads(company_id, processing_status)",
    """
    CREATE TABLE IF NOT EXISTS analytics_events (
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
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_analytics_events_company_raw_kind UNIQUE (company_id, raw_table, raw_id, event_kind)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_analytics_events_company_date ON analytics_events(company_id, metric_date)",
    "CREATE INDEX IF NOT EXISTS idx_analytics_events_company_kind ON analytics_events(company_id, event_kind, occurred_at)",  # noqa: E501
    """
    CREATE TABLE IF NOT EXISTS lead_metrics (
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
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_lead_metrics_company_lead_date UNIQUE (company_id, lead_id, metric_date)
    )
    """,
    "ALTER TABLE lead_metrics DROP CONSTRAINT IF EXISTS fk_lead_metrics_lead_company",
    "CREATE INDEX IF NOT EXISTS idx_lead_metrics_company_date ON lead_metrics(company_id, metric_date)",
    "CREATE INDEX IF NOT EXISTS idx_lead_metrics_company_status ON lead_metrics(company_id, status, metric_date)",
    """
    CREATE TABLE IF NOT EXISTS conversation_metrics (
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
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_conversation_metrics_company_conversation_date UNIQUE (company_id, conversation_id, metric_date)
    )
    """,
    "ALTER TABLE conversation_metrics DROP CONSTRAINT IF EXISTS fk_conversation_metrics_conversation_company",
    "CREATE INDEX IF NOT EXISTS idx_conversation_metrics_company_date ON conversation_metrics(company_id, metric_date)",
    "CREATE INDEX IF NOT EXISTS idx_conversation_metrics_company_channel ON conversation_metrics(company_id, channel, metric_date)",  # noqa: E501
    """
    CREATE TABLE IF NOT EXISTS sentiment_logs (
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
    """,
    "CREATE INDEX IF NOT EXISTS idx_sentiment_logs_company_date ON sentiment_logs(company_id, occurred_at)",
    "CREATE INDEX IF NOT EXISTS idx_sentiment_logs_company_label ON sentiment_logs(company_id, sentiment_label, occurred_at)",  # noqa: E501
)

_RLS_TABLES = (
    "raw_events",
    "raw_messages",
    "raw_leads",
    "analytics_events",
    "lead_metrics",
    "conversation_metrics",
    "sentiment_logs",
)


async def ensure_pipeline_tables(db) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    async with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        pool = await db._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{_PIPELINE_SCHEMA}"')
            await conn.execute(f'SET search_path TO "{_PIPELINE_SCHEMA}", public')
            for statement in _DDL_STATEMENTS:
                await conn.execute(statement)
            for table_name in _RLS_TABLES:
                await conn.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
                await conn.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
                await conn.execute(f"DROP POLICY IF EXISTS p_{table_name}_tenant ON {table_name}")
                await conn.execute(
                    f"CREATE POLICY p_{table_name}_tenant ON {table_name} "
                    "USING (company_id = current_setting('app.current_company', true)) "
                    "WITH CHECK (company_id = current_setting('app.current_company', true))"
                )
        _SCHEMA_READY = True
