-- Mirror subscription/payment state onto companies for auth payloads and frontend billing guards.

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
