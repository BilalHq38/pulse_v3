-- Minimal tenant-scoped orders module for customer order capture and admin review.

CREATE TABLE IF NOT EXISTS orders (
    id               TEXT PRIMARY KEY,
    company_id       TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    conversation_id  TEXT NOT NULL DEFAULT '',
    lead_id          TEXT NOT NULL DEFAULT '',
    customer_id      TEXT NOT NULL DEFAULT '',
    product_id       TEXT NOT NULL DEFAULT '',
    product_name     TEXT NOT NULL DEFAULT '',
    quantity         INTEGER DEFAULT 1 CHECK (quantity IS NULL OR quantity > 0),
    variant          TEXT NOT NULL DEFAULT '',
    size             TEXT NOT NULL DEFAULT '',
    color            TEXT NOT NULL DEFAULT '',
    customer_name    TEXT NOT NULL DEFAULT '',
    customer_email   TEXT NOT NULL DEFAULT '',
    customer_phone   TEXT NOT NULL DEFAULT '',
    delivery_address TEXT NOT NULL DEFAULT '',
    notes            TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'collecting_details'
        CHECK (status IN ('collecting_details','awaiting_confirmation','placed','admin_review','confirmed','cancelled','completed')),
    source_channel   TEXT NOT NULL DEFAULT 'web_chat',
    created_by       TEXT NOT NULL DEFAULT 'ai',
    raw_details      JSONB NOT NULL DEFAULT '{}'::jsonb,
    missing_fields   JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE orders ALTER COLUMN quantity DROP NOT NULL;
ALTER TABLE orders ALTER COLUMN quantity SET DEFAULT 1;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_email TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_orders_company_id ON orders(company_id);
CREATE INDEX IF NOT EXISTS idx_orders_company_conversation ON orders(company_id, conversation_id);
CREATE INDEX IF NOT EXISTS idx_orders_company_lead ON orders(company_id, lead_id);
CREATE INDEX IF NOT EXISTS idx_orders_company_customer ON orders(company_id, customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_company_status ON orders(company_id, status);
CREATE INDEX IF NOT EXISTS idx_orders_company_created_at ON orders(company_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_active_draft
    ON orders(company_id, conversation_id)
    WHERE status IN ('collecting_details','awaiting_confirmation') AND BTRIM(conversation_id) <> '';

ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_orders_tenant ON orders;
CREATE POLICY p_orders_tenant ON orders
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));
