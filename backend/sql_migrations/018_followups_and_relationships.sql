-- Wave 2: proactive follow-up substrate.
-- Tables for the scheduler (ai_followups), customer-facing safety
-- (customer_engagement opt-out + cooldown), feedback capture, workflow
-- telemetry, and product relationships used by the upsell retrieval.

CREATE TABLE IF NOT EXISTS ai_followups (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    order_id        TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    customer_id     TEXT NOT NULL DEFAULT '',
    session_id      TEXT NOT NULL DEFAULT '',
    workflow_kind   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'scheduled',
    scheduled_for   TIMESTAMPTZ NOT NULL,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    outcome         TEXT NOT NULL DEFAULT '',
    outcome_notes   TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_ai_followups_workflow_kind
        CHECK (workflow_kind IN ('post_delivery_feedback','upsell')),
    CONSTRAINT chk_ai_followups_status
        CHECK (status IN ('scheduled','running','completed','customer_declined','expired','cancelled'))
);
CREATE INDEX IF NOT EXISTS idx_followups_due
    ON ai_followups(status, scheduled_for) WHERE status = 'scheduled';
CREATE UNIQUE INDEX IF NOT EXISTS uq_followups_one_active_per_order
    ON ai_followups(order_id) WHERE status IN ('scheduled','running');
CREATE UNIQUE INDEX IF NOT EXISTS uq_followups_idempotency_key
    ON ai_followups(idempotency_key) WHERE BTRIM(idempotency_key) <> '';

CREATE TABLE IF NOT EXISTS customer_feedback (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    customer_id  TEXT NOT NULL DEFAULT '',
    order_id     TEXT NOT NULL DEFAULT '',
    product_id   TEXT NOT NULL DEFAULT '',
    session_id   TEXT NOT NULL DEFAULT '',
    followup_id  TEXT NOT NULL DEFAULT '',
    sentiment    TEXT NOT NULL DEFAULT '',
    rating       INTEGER,
    raw_response TEXT NOT NULL DEFAULT '',
    ai_summary   TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_customer_feedback_rating
        CHECK (rating IS NULL OR (rating BETWEEN 1 AND 5))
);
CREATE INDEX IF NOT EXISTS idx_feedback_company_customer
    ON customer_feedback(company_id, customer_id, created_at);

CREATE TABLE IF NOT EXISTS customer_engagement (
    company_id        TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    customer_id       TEXT NOT NULL,
    opted_out         BOOLEAN NOT NULL DEFAULT FALSE,
    last_contacted_at TIMESTAMPTZ,
    followup_count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (company_id, customer_id)
);

CREATE TABLE IF NOT EXISTS automation_log (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    workflow_kind TEXT NOT NULL,
    followup_id   TEXT NOT NULL DEFAULT '',
    order_id      TEXT NOT NULL DEFAULT '',
    customer_id   TEXT NOT NULL DEFAULT '',
    outcome       TEXT NOT NULL DEFAULT '',
    duration_ms   INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_automation_log_company_created_at
    ON automation_log(company_id, created_at);

CREATE TABLE IF NOT EXISTS product_relationships (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    product_id    TEXT NOT NULL REFERENCES company_products(id) ON DELETE CASCADE,
    related_id    TEXT NOT NULL REFERENCES company_products(id) ON DELETE CASCADE,
    relation_kind TEXT NOT NULL DEFAULT 'complementary',
    weight        NUMERIC(4,2) NOT NULL DEFAULT 0.5,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_product_relationships
        UNIQUE (company_id, product_id, related_id, relation_kind),
    CONSTRAINT chk_product_relationship_distinct
        CHECK (product_id <> related_id),
    CONSTRAINT chk_product_relationship_kind
        CHECK (relation_kind IN ('complementary','accessory','upgrade','similar'))
);
CREATE INDEX IF NOT EXISTS idx_product_relationships_product
    ON product_relationships(company_id, product_id);

ALTER TABLE ai_followups ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_followups FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_ai_followups_tenant ON ai_followups;
CREATE POLICY p_ai_followups_tenant ON ai_followups
USING (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
)
WITH CHECK (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
);

ALTER TABLE customer_feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_feedback FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_customer_feedback_tenant ON customer_feedback;
CREATE POLICY p_customer_feedback_tenant ON customer_feedback
USING (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
)
WITH CHECK (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
);

ALTER TABLE customer_engagement ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_engagement FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_customer_engagement_tenant ON customer_engagement;
CREATE POLICY p_customer_engagement_tenant ON customer_engagement
USING (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
)
WITH CHECK (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
);

ALTER TABLE automation_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE automation_log FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_automation_log_tenant ON automation_log;
CREATE POLICY p_automation_log_tenant ON automation_log
USING (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
)
WITH CHECK (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
);

ALTER TABLE product_relationships ENABLE ROW LEVEL SECURITY;
ALTER TABLE product_relationships FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_product_relationships_tenant ON product_relationships;
CREATE POLICY p_product_relationships_tenant ON product_relationships
USING (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
)
WITH CHECK (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
);
