-- Conversation AI pause state used by inbox AI toggle/manual-response flows.

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS agent_type TEXT NOT NULL DEFAULT 'generic';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS ai_auto_paused BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS ai_paused_at TIMESTAMPTZ;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS ai_paused_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS ai_paused_error_type TEXT NOT NULL DEFAULT '';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS ai_paused_provider TEXT NOT NULL DEFAULT '';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS ai_paused_model TEXT NOT NULL DEFAULT '';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS ai_paused_scope TEXT NOT NULL DEFAULT '';
