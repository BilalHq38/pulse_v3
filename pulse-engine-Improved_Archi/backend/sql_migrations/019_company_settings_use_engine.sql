-- Wave 3b: per-company opt-in for the new conversation engine.
-- When TRUE, /api/webhooks/web-chat will route the response-generation step
-- through services.conversation_engine.run_turn and keep the legacy workflow
-- only for metadata (sentiment / intent / qualification / escalation).

ALTER TABLE company_settings
    ADD COLUMN IF NOT EXISTS ai_use_conversation_engine BOOLEAN NOT NULL DEFAULT FALSE;
