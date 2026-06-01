-- Apply all pending migrations

-- 015: Product pages (slugs, links, stock)
ALTER TABLE company_products
    ADD COLUMN IF NOT EXISTS links               TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS link_source         TEXT NOT NULL DEFAULT 'auto',
    ADD COLUMN IF NOT EXISTS slug                TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS public_page_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS stock_quantity      INTEGER;

ALTER TABLE company_products
    DROP CONSTRAINT IF EXISTS company_products_link_source_check;
ALTER TABLE company_products
    ADD CONSTRAINT company_products_link_source_check
    CHECK (link_source IN ('auto', 'manual'));

ALTER TABLE companies ADD COLUMN IF NOT EXISTS slug TEXT NOT NULL DEFAULT '';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS plan TEXT NOT NULL DEFAULT 'free';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS subscription_status TEXT NOT NULL DEFAULT 'inactive';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS billing_status TEXT NOT NULL DEFAULT 'inactive';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT NOT NULL DEFAULT '';

ALTER TABLE orders ADD COLUMN IF NOT EXISTS idempotency_key TEXT NOT NULL DEFAULT '';

ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check;
ALTER TABLE orders ADD CONSTRAINT orders_status_check
    CHECK (status IN (
        'collecting_details','awaiting_confirmation','placed','admin_review',
        'pending','confirmed','cancelled','completed'
    ));

-- 015: orders add price
ALTER TABLE orders ADD COLUMN IF NOT EXISTS total_price NUMERIC(12,2);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS unit_price  NUMERIC(12,2);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS currency    TEXT NOT NULL DEFAULT 'USD';

-- 016: customers budget
ALTER TABLE customers ADD COLUMN IF NOT EXISTS budget NUMERIC(12,2);
ALTER TABLE customers ADD COLUMN IF NOT EXISTS budget_currency TEXT NOT NULL DEFAULT 'USD';

-- 016: order lifecycle
ALTER TABLE orders ADD COLUMN IF NOT EXISTS confirmed_at  TIMESTAMP WITH TIME ZONE;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS cancelled_at  TIMESTAMP WITH TIME ZONE;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS completed_at  TIMESTAMP WITH TIME ZONE;

-- 017: conversation engine tables
CREATE TABLE IF NOT EXISTS ai_conversation_turns (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    customer_id     TEXT NOT NULL DEFAULT '',
    turn_index      INTEGER NOT NULL DEFAULT 0,
    user_message    TEXT NOT NULL DEFAULT '',
    ai_response     TEXT NOT NULL DEFAULT '',
    sources_used    TEXT[] NOT NULL DEFAULT '{}',
    product_links   JSONB NOT NULL DEFAULT '[]',
    confidence      FLOAT NOT NULL DEFAULT 0,
    active_template TEXT NOT NULL DEFAULT '',
    token_usage     JSONB NOT NULL DEFAULT '{}',
    mode            TEXT NOT NULL DEFAULT 'reactive',
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_conv_turns_session
    ON ai_conversation_turns(company_id, session_id, turn_index);

CREATE TABLE IF NOT EXISTS ai_conversation_summaries (
    id                    TEXT PRIMARY KEY,
    company_id            TEXT NOT NULL,
    session_id            TEXT NOT NULL,
    summary               TEXT NOT NULL DEFAULT '',
    covers_through_turn   INTEGER NOT NULL DEFAULT 0,
    created_at            TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, session_id)
);

-- Response templates for conversation engine
CREATE TABLE IF NOT EXISTS response_templates (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL,
    name         TEXT NOT NULL,
    style_prompt TEXT NOT NULL DEFAULT '',
    is_default   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, name)
);

CREATE INDEX IF NOT EXISTS idx_response_templates_company
    ON response_templates(company_id, is_default);

-- 017: orders confirmed_at
ALTER TABLE orders ADD COLUMN IF NOT EXISTS product_id TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS product_name TEXT NOT NULL DEFAULT '';

-- 018: followups and relationships
CREATE TABLE IF NOT EXISTS product_relationships (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL,
    product_id    TEXT NOT NULL,
    related_id    TEXT NOT NULL,
    relation_kind TEXT NOT NULL DEFAULT 'complementary',
    weight        FLOAT NOT NULL DEFAULT 0.5,
    created_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, product_id, related_id)
);

CREATE TABLE IF NOT EXISTS followup_tasks (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    customer_id     TEXT NOT NULL DEFAULT '',
    order_id        TEXT NOT NULL DEFAULT '',
    conversation_id TEXT NOT NULL DEFAULT '',
    kind            TEXT NOT NULL DEFAULT 'post_delivery_feedback',
    status          TEXT NOT NULL DEFAULT 'pending',
    scheduled_for   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    executed_at     TIMESTAMP WITH TIME ZONE,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_followup_tasks_scheduled
    ON followup_tasks(company_id, status, scheduled_for);

-- 019: company settings use engine
ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS use_conversation_engine BOOLEAN NOT NULL DEFAULT TRUE;

-- 020: default conversation engine
UPDATE company_settings SET use_conversation_engine = TRUE WHERE use_conversation_engine IS DISTINCT FROM TRUE;

-- 021: Fix schema gaps from partial migrations applied to the existing database.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS quantity         INTEGER DEFAULT 1 CHECK (quantity IS NULL OR quantity > 0);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_address TEXT NOT NULL DEFAULT '';

ALTER TABLE company_settings
    ADD COLUMN IF NOT EXISTS ai_use_conversation_engine BOOLEAN NOT NULL DEFAULT TRUE;
UPDATE company_settings
   SET ai_use_conversation_engine = TRUE
 WHERE ai_use_conversation_engine IS DISTINCT FROM TRUE;

ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check;
ALTER TABLE orders ADD CONSTRAINT orders_status_check
    CHECK (status IN (
        'collecting_details','awaiting_confirmation','placed','admin_review',
        'pending','confirmed','shipped','delivered','completed','cancelled'
    ));

CREATE TABLE IF NOT EXISTS order_lifecycle_events (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    order_id     TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    from_status  TEXT NOT NULL DEFAULT '',
    to_status    TEXT NOT NULL,
    actor_type   TEXT NOT NULL DEFAULT 'system',
    actor_id     TEXT NOT NULL DEFAULT '',
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_order_lifecycle_actor_type
        CHECK (actor_type IN ('system','admin','ai'))
);

CREATE INDEX IF NOT EXISTS idx_order_lifecycle_company_order
    ON order_lifecycle_events(company_id, order_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_order_lifecycle_to_status
    ON order_lifecycle_events(company_id, to_status, occurred_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_order_lifecycle_event_idempotent
    ON order_lifecycle_events(order_id, to_status, actor_id);

ALTER TABLE order_lifecycle_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_lifecycle_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_order_lifecycle_events_tenant ON order_lifecycle_events;
CREATE POLICY p_order_lifecycle_events_tenant ON order_lifecycle_events
USING (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
)
WITH CHECK (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
);

-- 022: Mirror subscription/payment state onto companies.
ALTER TABLE companies ADD COLUMN IF NOT EXISTS plan TEXT NOT NULL DEFAULT 'free';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS subscription_status TEXT NOT NULL DEFAULT 'inactive';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS billing_status TEXT NOT NULL DEFAULT 'inactive';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT NOT NULL DEFAULT '';

UPDATE companies c
SET
    plan = COALESCE(s.plan_code, c.plan, 'free'),
    subscription_status = COALESCE(s.status, c.subscription_status, 'inactive'),
    billing_status = COALESCE(bc.payment_status, c.billing_status, 'inactive'),
    stripe_subscription_id = COALESCE(s.stripe_subscription_id, c.stripe_subscription_id, ''),
    updated_at = NOW()
FROM subscriptions s
LEFT JOIN billing_customers bc ON bc.company_id = s.company_id
WHERE s.company_id = c.id;

-- 023: Runtime schema and provider hardening.
CREATE SCHEMA IF NOT EXISTS agent_orchestrator;

CREATE TABLE IF NOT EXISTS agent_orchestrator.global_memory (
    id TEXT PRIMARY KEY,
    memory_key TEXT NOT NULL UNIQUE,
    company_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL DEFAULT '',
    customer_id TEXT NOT NULL DEFAULT '',
    lead_id TEXT NOT NULL DEFAULT '',
    identity_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    conversation_history JSONB NOT NULL DEFAULT '[]'::jsonb,
    knowledge_context TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    shared_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_orchestrator.workflows (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    trace_id TEXT NOT NULL DEFAULT '',
    workflow_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    entity_type TEXT NOT NULL DEFAULT '',
    entity_id TEXT NOT NULL DEFAULT '',
    conversation_id TEXT NOT NULL DEFAULT '',
    customer_id TEXT NOT NULL DEFAULT '',
    lead_id TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    current_agent TEXT NOT NULL DEFAULT '',
    routing_mode TEXT NOT NULL DEFAULT 'rule_based',
    intent TEXT NOT NULL DEFAULT '',
    lead_status TEXT NOT NULL DEFAULT '',
    requested_by TEXT NOT NULL DEFAULT '',
    input_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    shared_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    final_output JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS agent_orchestrator.workflow_executions (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    trace_id TEXT NOT NULL DEFAULT '',
    agent_name TEXT NOT NULL,
    status TEXT NOT NULL,
    routing_decision JSONB NOT NULL DEFAULT '{}'::jsonb,
    input_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT NOT NULL DEFAULT '',
    duration_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS agent_orchestrator.workflow_transitions (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    trace_id TEXT NOT NULL DEFAULT '',
    from_agent TEXT NOT NULL DEFAULT '',
    to_agent TEXT NOT NULL DEFAULT '',
    decision_reason TEXT NOT NULL DEFAULT '',
    decision_mode TEXT NOT NULL DEFAULT 'rule_based',
    state_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_orchestrator.agent_memory (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    trace_id TEXT NOT NULL DEFAULT '',
    agent_name TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    memory_value JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_agent_memory_workflow_agent_key UNIQUE (workflow_id, agent_name, memory_key)
);

CREATE INDEX IF NOT EXISTS idx_global_memory_company
    ON agent_orchestrator.global_memory(company_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflows_company_status
    ON agent_orchestrator.workflows(company_id, status, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_workflows_message_idempotency
    ON agent_orchestrator.workflows(company_id, entity_type, entity_id)
    WHERE workflow_kind='message' AND entity_type='conversation_message';
CREATE UNIQUE INDEX IF NOT EXISTS uq_workflows_entity_idempotency
    ON agent_orchestrator.workflows(company_id, workflow_kind, entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_workflows_trace
    ON agent_orchestrator.workflows(trace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_executions_workflow
    ON agent_orchestrator.workflow_executions(workflow_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_executions_company
    ON agent_orchestrator.workflow_executions(company_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_transitions_workflow
    ON agent_orchestrator.workflow_transitions(workflow_id, created_at DESC);

ALTER TABLE company_faqs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
UPDATE company_faqs
   SET updated_at = COALESCE(updated_at, created_at, NOW())
 WHERE updated_at IS NULL;
ALTER TABLE company_faqs ALTER COLUMN updated_at SET DEFAULT NOW();
ALTER TABLE company_faqs ALTER COLUMN updated_at SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_company_faqs_company_updated_at
    ON company_faqs(company_id, updated_at DESC);

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS sentiment_score NUMERIC(5,4);
UPDATE conversations SET sentiment_score = 0 WHERE sentiment_score IS NULL;
ALTER TABLE conversations ALTER COLUMN sentiment_score SET DEFAULT 0;
ALTER TABLE conversations ALTER COLUMN sentiment_score SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_embeddings_company_source_lookup
    ON embeddings(company_id, source_type, source_id);

CREATE TABLE IF NOT EXISTS context_memory_dedup (
    company_id TEXT NOT NULL,
    convo_id TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    memory_id TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (company_id, convo_id, message_id, memory_type)
);
CREATE INDEX IF NOT EXISTS idx_context_memory_dedup_company
    ON context_memory_dedup(company_id, created_at DESC);
