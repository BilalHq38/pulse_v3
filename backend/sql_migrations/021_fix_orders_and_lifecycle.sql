-- 021: Fix schema gaps from partial migrations applied to the existing database.
-- Adds missing orders columns, creates order_lifecycle_events, and ensures the
-- canonical ai_use_conversation_engine column exists with the correct name.

ALTER TABLE orders ADD COLUMN IF NOT EXISTS quantity         INTEGER DEFAULT 1 CHECK (quantity IS NULL OR quantity > 0);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_address TEXT NOT NULL DEFAULT '';

-- Ensure the canonical engine-flag column name is present (019 migration used a different name).
ALTER TABLE company_settings
    ADD COLUMN IF NOT EXISTS ai_use_conversation_engine BOOLEAN NOT NULL DEFAULT TRUE;
UPDATE company_settings
   SET ai_use_conversation_engine = TRUE
 WHERE ai_use_conversation_engine IS DISTINCT FROM TRUE;

-- Expand status set to include shipped/delivered for Wave-2 lifecycle.
ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check;
ALTER TABLE orders ADD CONSTRAINT orders_status_check
    CHECK (status IN (
        'collecting_details','awaiting_confirmation','placed','admin_review',
        'pending','confirmed','shipped','delivered','completed','cancelled'
    ));

-- order_lifecycle_events: per-transition audit log consumed by follow-up scheduler.
-- The unique index on (order_id, to_status, actor_id) makes idempotent replays a no-op.
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
