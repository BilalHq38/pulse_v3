-- Unified monthly conversation limits (all channels) + per-workspace seat cap.
-- Run once against existing databases created before this change.

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS monthly_conversation_limit INTEGER NOT NULL DEFAULT 0;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS max_users INTEGER NOT NULL DEFAULT 0;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = ANY (current_schemas(false))
      AND table_name = 'subscriptions'
      AND column_name = 'whatsapp_credit_limit'
  ) THEN
    EXECUTE $q$
      UPDATE subscriptions
      SET monthly_conversation_limit = whatsapp_credit_limit
      WHERE monthly_conversation_limit = 0
    $q$;
  END IF;
END $$;

UPDATE subscriptions SET monthly_conversation_limit = 250
WHERE plan_code = 'free' AND monthly_conversation_limit = 0;

UPDATE subscriptions SET monthly_conversation_limit = 2500
WHERE plan_code = 'pro' AND monthly_conversation_limit = 0;

UPDATE subscriptions SET monthly_conversation_limit = 10000
WHERE plan_code = 'enterprise' AND monthly_conversation_limit = 0;

UPDATE subscriptions SET max_users = 1
WHERE plan_code IN ('free', 'pro') AND max_users = 0;

UPDATE subscriptions SET max_users = 3
WHERE plan_code = 'enterprise' AND max_users = 0;

ALTER TABLE subscriptions DROP COLUMN IF EXISTS whatsapp_credit_limit;
