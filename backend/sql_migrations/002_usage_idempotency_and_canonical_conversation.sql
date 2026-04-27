-- Canonical conversation billing: single usage_type + idempotency (per-tenant dedupe).

ALTER TABLE usage_ledger ADD COLUMN IF NOT EXISTS usage_idempotency_key TEXT NOT NULL DEFAULT '';

-- Merge legacy channel rows into conversation_message (preserves totals, no double-count after app deploy).
UPDATE usage_ledger
SET usage_type = 'conversation_message'
WHERE usage_type IN (
    'whatsapp_message',
    'instagram_message',
    'email_message',
    'web_chat_message',
    'facebook_message',
    'messenger_message'
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_usage_ledger_company_conv_idem
    ON usage_ledger(company_id, usage_idempotency_key)
    WHERE usage_type = 'conversation_message' AND BTRIM(usage_idempotency_key) <> '';
