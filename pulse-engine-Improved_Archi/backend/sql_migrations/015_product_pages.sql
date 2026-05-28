-- Wave 1: dynamic product pages and public buy endpoint.
-- Adds SEO slugs to companies and products, a manual/auto product link,
-- and the order columns/constraints required by the public buy endpoint.

-- ---------------------------------------------------------------------------
-- Companies: SEO slug for /c/{company_slug}/product/{product_slug}.
-- ---------------------------------------------------------------------------
ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS slug TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_slug
    ON companies(slug) WHERE BTRIM(slug) <> '';

-- ---------------------------------------------------------------------------
-- Products: canonical link, SEO slug, stock and public-page toggle.
-- `stock_quantity` is NULL when the product does not track inventory.
-- ---------------------------------------------------------------------------
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

CREATE UNIQUE INDEX IF NOT EXISTS uq_company_products_company_slug
    ON company_products(company_id, slug) WHERE BTRIM(slug) <> '';

-- ---------------------------------------------------------------------------
-- Orders: allow the public-page 'pending' status and store an idempotency key
-- so retried submissions return the existing order instead of a 5xx.
-- ---------------------------------------------------------------------------
ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT NOT NULL DEFAULT '';

ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check;
ALTER TABLE orders ADD CONSTRAINT orders_status_check
    CHECK (status IN (
        'collecting_details','awaiting_confirmation','placed','admin_review',
        'pending','confirmed','cancelled','completed'
    ));

-- Phone-bearing public buys are deduped by (company, phone, product, minute).
-- date_trunc(text, timestamptz) is STABLE, so wrap created_at in AT TIME ZONE 'UTC'
-- to get a TIMESTAMP that date_trunc treats as IMMUTABLE for the index expression.
CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_public_dedupe
    ON orders(company_id, customer_phone, product_id, date_trunc('minute', (created_at AT TIME ZONE 'UTC')))
    WHERE source_channel = 'public_product_page' AND BTRIM(customer_phone) <> '';

-- Anonymous public buys are deduped by client-supplied idempotency_key.
CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_idempotency_key
    ON orders(company_id, idempotency_key)
    WHERE source_channel = 'public_product_page' AND BTRIM(idempotency_key) <> '';
