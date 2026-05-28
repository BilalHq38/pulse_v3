-- Identity service schema hardening for runtime DDL removed from app startup/workers.
-- Run before deploying identity service code that performs read-only schema checks.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS consent_ledger (
    consent_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(128) NOT NULL,
    platform_user_id TEXT NOT NULL,
    platform VARCHAR(32) NOT NULL,
    consent_given BOOLEAN NOT NULL DEFAULT TRUE,
    consent_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    consent_version VARCHAR(64) NOT NULL DEFAULT '',
    consent_method VARCHAR(32) NOT NULL DEFAULT '',
    consent_token VARCHAR(128) UNIQUE NOT NULL,
    ip_hash VARCHAR(128),
    granular_consent JSONB NOT NULL DEFAULT '{}'::jsonb,
    data_retention_days INTEGER NOT NULL DEFAULT 365,
    revoked_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS unified_customers (
    customer_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id UUID UNIQUE DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    profile_confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    primary_identity TEXT,
    primary_name TEXT,
    primary_phone_hash VARCHAR(128),
    primary_email_hash VARCHAR(128),
    embedding_vector JSONB,
    signal_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
    consent_id UUID REFERENCES consent_ledger(consent_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_unified_customers_id ON unified_customers(id);
CREATE INDEX IF NOT EXISTS idx_unified_customers_customer_id ON unified_customers(customer_id);
CREATE INDEX IF NOT EXISTS idx_unified_customers_tenant ON unified_customers(tenant_id);
CREATE INDEX IF NOT EXISTS idx_unified_customers_active ON unified_customers(is_active);
CREATE INDEX IF NOT EXISTS idx_unified_customers_phone_hash ON unified_customers(primary_phone_hash);
CREATE INDEX IF NOT EXISTS idx_unified_customers_email_hash ON unified_customers(primary_email_hash);

CREATE TABLE IF NOT EXISTS identity_mappings (
    mapping_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id UUID UNIQUE DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(128) NOT NULL,
    customer_id UUID NOT NULL REFERENCES unified_customers(customer_id) ON DELETE CASCADE,
    platform VARCHAR(32) NOT NULL,
    platform_user_id TEXT NOT NULL,
    platform_username TEXT,
    phone TEXT,
    email TEXT,
    name TEXT,
    fingerprint TEXT,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    confidence_score DOUBLE PRECISION,
    is_primary_platform BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_identity_mapping_platform_user UNIQUE (tenant_id, platform, platform_user_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_identity_mappings_id ON identity_mappings(id);
CREATE INDEX IF NOT EXISTS idx_identity_mappings_mapping_id ON identity_mappings(mapping_id);
CREATE INDEX IF NOT EXISTS idx_identity_mappings_tenant ON identity_mappings(tenant_id);
CREATE INDEX IF NOT EXISTS idx_identity_mappings_customer ON identity_mappings(customer_id);
CREATE INDEX IF NOT EXISTS idx_identity_mappings_platform ON identity_mappings(platform);

CREATE TABLE IF NOT EXISTS device_fingerprints (
    fingerprint_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(128) NOT NULL,
    customer_id UUID NOT NULL REFERENCES unified_customers(customer_id) ON DELETE CASCADE,
    fingerprint_hash VARCHAR(128) NOT NULL,
    signals_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    match_count INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_device_fingerprints_tenant ON device_fingerprints(tenant_id);
CREATE INDEX IF NOT EXISTS idx_device_fingerprints_customer ON device_fingerprints(customer_id);
CREATE INDEX IF NOT EXISTS idx_device_fingerprints_hash ON device_fingerprints(fingerprint_hash);

CREATE TABLE IF NOT EXISTS resolution_audit_log (
    resolution_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(128) NOT NULL,
    customer_id UUID REFERENCES unified_customers(customer_id) ON DELETE SET NULL,
    input_signals JSONB NOT NULL DEFAULT '{}'::jsonb,
    score_breakdown JSONB NOT NULL DEFAULT '{}'::jsonb,
    match_type VARCHAR(32) NOT NULL DEFAULT '',
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    decision TEXT NOT NULL DEFAULT '',
    decision_reason TEXT NOT NULL DEFAULT '',
    processing_ms INTEGER NOT NULL DEFAULT 0,
    merge_performed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_resolution_audit_log_tenant ON resolution_audit_log(tenant_id);
CREATE INDEX IF NOT EXISTS idx_resolution_audit_log_customer ON resolution_audit_log(customer_id);

CREATE TABLE IF NOT EXISTS profile_merge_history (
    merge_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(128) NOT NULL,
    source_customer_id UUID REFERENCES unified_customers(customer_id) ON DELETE SET NULL,
    target_customer_id UUID REFERENCES unified_customers(customer_id) ON DELETE SET NULL,
    merge_reason TEXT NOT NULL DEFAULT '',
    merged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    merged_by VARCHAR(32) NOT NULL DEFAULT 'auto'
);
CREATE INDEX IF NOT EXISTS idx_profile_merge_history_tenant ON profile_merge_history(tenant_id);
CREATE INDEX IF NOT EXISTS idx_profile_merge_history_source ON profile_merge_history(source_customer_id);
CREATE INDEX IF NOT EXISTS idx_profile_merge_history_target ON profile_merge_history(target_customer_id);

CREATE TABLE IF NOT EXISTS merged_profile_records (
    record_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(128) NOT NULL,
    unified_customer_id UUID REFERENCES unified_customers(customer_id) ON DELETE SET NULL,
    source_customer_id UUID REFERENCES unified_customers(customer_id) ON DELETE SET NULL,
    target_customer_id UUID REFERENCES unified_customers(customer_id) ON DELETE SET NULL,
    source_platforms JSONB NOT NULL DEFAULT '[]'::jsonb,
    target_platforms JSONB NOT NULL DEFAULT '[]'::jsonb,
    original_identities JSONB NOT NULL DEFAULT '{}'::jsonb,
    unified_identity_mapping JSONB NOT NULL DEFAULT '{}'::jsonb,
    merge_history JSONB NOT NULL DEFAULT '{}'::jsonb,
    merge_reason TEXT NOT NULL DEFAULT '',
    merged_by VARCHAR(32) NOT NULL DEFAULT 'auto',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_merged_profile_records_tenant ON merged_profile_records(tenant_id);
CREATE INDEX IF NOT EXISTS idx_merged_profile_records_unified ON merged_profile_records(unified_customer_id);
CREATE INDEX IF NOT EXISTS idx_merged_profile_records_source ON merged_profile_records(source_customer_id);
CREATE INDEX IF NOT EXISTS idx_merged_profile_records_target ON merged_profile_records(target_customer_id);

CREATE TABLE IF NOT EXISTS review_queue (
    review_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(128) NOT NULL,
    resolution_id UUID REFERENCES resolution_audit_log(resolution_id) ON DELETE CASCADE,
    source_customer_id UUID REFERENCES unified_customers(customer_id) ON DELETE SET NULL,
    candidate_customer_id UUID REFERENCES unified_customers(customer_id) ON DELETE SET NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    source VARCHAR(64) NOT NULL DEFAULT 'internal',
    reason TEXT NOT NULL DEFAULT '',
    recommended_action VARCHAR(32) NOT NULL DEFAULT 'review',
    score_breakdown JSONB NOT NULL DEFAULT '{}'::jsonb,
    independent_signals JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    review_notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_review_queue_tenant ON review_queue(tenant_id);
CREATE INDEX IF NOT EXISTS idx_review_queue_status ON review_queue(status);
CREATE INDEX IF NOT EXISTS idx_review_queue_source ON review_queue(source);

CREATE TABLE IF NOT EXISTS identity_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(128) NOT NULL DEFAULT 'demo_tenant',
    customer_id UUID REFERENCES unified_customers(customer_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    data_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_identity_history_customer ON identity_history(customer_id);
CREATE INDEX IF NOT EXISTS idx_identity_history_timestamp ON identity_history(timestamp);
CREATE INDEX IF NOT EXISTS idx_identity_history_tenant ON identity_history(tenant_id);

CREATE TABLE IF NOT EXISTS consent_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(128) NOT NULL DEFAULT 'demo_tenant',
    customer_id UUID REFERENCES unified_customers(customer_id) ON DELETE SET NULL,
    consent_given BOOLEAN NOT NULL DEFAULT FALSE,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_consent_records_customer ON consent_records(customer_id);
CREATE INDEX IF NOT EXISTS idx_consent_records_timestamp ON consent_records(timestamp);
CREATE INDEX IF NOT EXISTS idx_consent_records_tenant ON consent_records(tenant_id);

CREATE TABLE IF NOT EXISTS identity_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(128) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    aggregate_customer_id UUID,
    idempotency_key VARCHAR(191),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    error_message TEXT,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_identity_events_tenant ON identity_events(tenant_id);
CREATE INDEX IF NOT EXISTS idx_identity_events_event_type ON identity_events(event_type);
CREATE INDEX IF NOT EXISTS idx_identity_events_created_at ON identity_events(created_at);
CREATE INDEX IF NOT EXISTS idx_identity_events_status ON identity_events(status);
CREATE INDEX IF NOT EXISTS idx_identity_events_aggregate_customer ON identity_events(aggregate_customer_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_identity_events_tenant_idempotency
    ON identity_events(tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS event_outbox (
    outbox_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID NOT NULL UNIQUE REFERENCES identity_events(event_id) ON DELETE CASCADE,
    tenant_id VARCHAR(128) NOT NULL,
    stream_name VARCHAR(128) NOT NULL DEFAULT 'identity.events',
    idempotency_key VARCHAR(191),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ,
    last_error TEXT,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_event_outbox_tenant ON event_outbox(tenant_id);
CREATE INDEX IF NOT EXISTS idx_event_outbox_status ON event_outbox(status);
CREATE INDEX IF NOT EXISTS idx_event_outbox_next_retry ON event_outbox(next_retry_at);
CREATE INDEX IF NOT EXISTS idx_event_outbox_created_at ON event_outbox(created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_event_outbox_tenant_idempotency
    ON event_outbox(tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS dead_letter_queue (
    id TEXT PRIMARY KEY,
    task_name TEXT NOT NULL DEFAULT '',
    event_id TEXT NOT NULL DEFAULT '',
    trace_id TEXT NOT NULL DEFAULT '',
    company_id TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    source_queue TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    status TEXT NOT NULL DEFAULT 'failed',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_dlq_status ON dead_letter_queue(status);
CREATE INDEX IF NOT EXISTS idx_dlq_company_id ON dead_letter_queue(company_id);
CREATE INDEX IF NOT EXISTS idx_dlq_created_at ON dead_letter_queue(created_at);
