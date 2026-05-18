-- Persist full campaign drafts and identity merge snapshots.

ALTER TABLE IF EXISTS email_campaigns
    ADD COLUMN IF NOT EXISTS attachments JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE IF EXISTS email_campaigns
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS merged_profile_records (
    record_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                 TEXT NOT NULL CHECK (BTRIM(tenant_id) <> ''),
    unified_customer_id       UUID REFERENCES unified_customers(customer_id) ON DELETE SET NULL,
    source_customer_id        UUID REFERENCES unified_customers(customer_id) ON DELETE SET NULL,
    target_customer_id        UUID REFERENCES unified_customers(customer_id) ON DELETE SET NULL,
    source_platforms          JSONB NOT NULL DEFAULT '[]'::jsonb,
    target_platforms          JSONB NOT NULL DEFAULT '[]'::jsonb,
    original_identities       JSONB NOT NULL DEFAULT '{}'::jsonb,
    unified_identity_mapping  JSONB NOT NULL DEFAULT '{}'::jsonb,
    merge_history             JSONB NOT NULL DEFAULT '{}'::jsonb,
    merge_reason              TEXT NOT NULL DEFAULT '',
    merged_by                 TEXT NOT NULL DEFAULT 'auto',
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_merged_profile_records_tenant ON merged_profile_records(tenant_id);
CREATE INDEX IF NOT EXISTS idx_merged_profile_records_unified ON merged_profile_records(unified_customer_id);
CREATE INDEX IF NOT EXISTS idx_merged_profile_records_source ON merged_profile_records(source_customer_id);
CREATE INDEX IF NOT EXISTS idx_merged_profile_records_target ON merged_profile_records(target_customer_id);

ALTER TABLE merged_profile_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE merged_profile_records FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_merged_profile_records_tenant ON merged_profile_records;
CREATE POLICY p_merged_profile_records_tenant ON merged_profile_records
USING (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)))
WITH CHECK (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)));
