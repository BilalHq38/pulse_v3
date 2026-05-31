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
