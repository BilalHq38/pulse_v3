-- Wave 2: conversation memory, response templates, and KB summary cache.
-- Tables here are the storage substrate for the conversation engine landing
-- in Wave 3. Each table has RLS so the engine can run under the normal tenant
-- session-config (`app.current_company`) without leaking across companies.

CREATE TABLE IF NOT EXISTS ai_conversation_turns (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    session_id      TEXT NOT NULL,
    customer_id     TEXT NOT NULL DEFAULT '',
    turn_index      INTEGER NOT NULL,
    user_message    TEXT NOT NULL,
    ai_response     TEXT NOT NULL,
    sources_used    JSONB NOT NULL DEFAULT '[]'::jsonb,
    product_links   JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence      NUMERIC(5,4),
    active_template TEXT NOT NULL DEFAULT '',
    token_usage     JSONB NOT NULL DEFAULT '{}'::jsonb,
    mode            TEXT NOT NULL DEFAULT 'reactive',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_ai_conversation_turns_mode
        CHECK (mode IN ('reactive','proactive'))
);
CREATE INDEX IF NOT EXISTS idx_ai_turns_session
    ON ai_conversation_turns(company_id, session_id, turn_index);
CREATE INDEX IF NOT EXISTS idx_ai_turns_customer
    ON ai_conversation_turns(company_id, customer_id);

-- Cold storage for the 90-day archival job (data_pipeline schedulers).
-- LIKE ... INCLUDING ALL copies columns, defaults, CHECK and NOT NULL
-- constraints, and indexes; it does not copy the FK to companies, which is
-- intentional — archived rows survive a company hard-delete for GDPR audit.
CREATE TABLE IF NOT EXISTS ai_conversation_turns_archive
    (LIKE ai_conversation_turns INCLUDING ALL);

CREATE TABLE IF NOT EXISTS ai_conversation_summaries (
    id                  TEXT PRIMARY KEY,
    company_id          TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    session_id          TEXT NOT NULL,
    summary             TEXT NOT NULL,
    covers_through_turn INTEGER NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_summary_session UNIQUE (company_id, session_id)
);

CREATE TABLE IF NOT EXISTS response_templates (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    style_prompt TEXT NOT NULL,
    is_default   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_response_templates_company_name UNIQUE (company_id, name)
);
-- At most one default template per company.
CREATE UNIQUE INDEX IF NOT EXISTS uq_response_templates_one_default
    ON response_templates(company_id) WHERE is_default;

-- Pre-computed KB summary used by the request-time compression layer so the
-- hot path never makes a Gemini call to compress an article.
ALTER TABLE knowledge_base
    ADD COLUMN IF NOT EXISTS summary              TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS summary_generated_at TIMESTAMPTZ;

ALTER TABLE ai_conversation_turns ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_conversation_turns FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_ai_conversation_turns_tenant ON ai_conversation_turns;
CREATE POLICY p_ai_conversation_turns_tenant ON ai_conversation_turns
USING (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
)
WITH CHECK (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
);

ALTER TABLE ai_conversation_turns_archive ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_conversation_turns_archive FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_ai_conversation_turns_archive_tenant ON ai_conversation_turns_archive;
CREATE POLICY p_ai_conversation_turns_archive_tenant ON ai_conversation_turns_archive
USING (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
)
WITH CHECK (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
);

ALTER TABLE ai_conversation_summaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_conversation_summaries FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_ai_conversation_summaries_tenant ON ai_conversation_summaries;
CREATE POLICY p_ai_conversation_summaries_tenant ON ai_conversation_summaries
USING (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
)
WITH CHECK (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
);

ALTER TABLE response_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE response_templates FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_response_templates_tenant ON response_templates;
CREATE POLICY p_response_templates_tenant ON response_templates
USING (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
)
WITH CHECK (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
);
