-- Add product price fields to orders table so price can be captured at order time.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS product_price TEXT NOT NULL DEFAULT '';
ALTER TABLE orders ADD COLUMN IF NOT EXISTS product_price_currency TEXT NOT NULL DEFAULT 'USD';
