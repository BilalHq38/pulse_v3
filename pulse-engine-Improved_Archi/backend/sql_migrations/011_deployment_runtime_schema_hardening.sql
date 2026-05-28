-- Deployment schema hardening for runtime DDL removed from application hot paths.
-- Run this once before deploying code that only performs read-only schema checks.

ALTER TABLE oauth_states ADD COLUMN IF NOT EXISTS link_user_id TEXT;

ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_selected BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS billing_status TEXT NOT NULL DEFAULT 'active';

CREATE TABLE IF NOT EXISTS refresh_tokens (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL,
    token_version INTEGER NOT NULL DEFAULT 0,
    user_agent TEXT NOT NULL DEFAULT '',
    ip_address TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    revoke_reason TEXT NOT NULL DEFAULT '',
    rotated_to TEXT NOT NULL DEFAULT '',
    CONSTRAINT uq_refresh_tokens_hash UNIQUE (token_hash)
);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_active
    ON refresh_tokens(user_id, revoked_at, expires_at);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_company_active
    ON refresh_tokens(company_id, revoked_at, expires_at);

ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS preferred_channels JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS ai_static_fallback_message TEXT NOT NULL
    DEFAULT 'Thanks for your message. A team member will respond shortly.';

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS agent_type TEXT NOT NULL DEFAULT 'generic';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS ai_auto_paused BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS ai_paused_at TIMESTAMPTZ;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS ai_paused_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS ai_paused_error_type TEXT NOT NULL DEFAULT '';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS ai_paused_provider TEXT NOT NULL DEFAULT '';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS ai_paused_model TEXT NOT NULL DEFAULT '';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS ai_paused_scope TEXT NOT NULL DEFAULT '';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS ai_disabled_until TIMESTAMPTZ;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS ai_failure_count INT DEFAULT 0;

DELETE FROM embeddings a USING embeddings b
WHERE a.company_id=b.company_id
  AND a.source_type=b.source_type
  AND a.source_id=b.source_id
  AND a.chunk_index=b.chunk_index
  AND a.ctid < b.ctid;
CREATE UNIQUE INDEX IF NOT EXISTS uq_embeddings_company_source_chunk
    ON embeddings(company_id, source_type, source_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_embeddings_company_source_lookup
    ON embeddings(company_id, source_type, source_id);
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector') THEN
        CREATE INDEX IF NOT EXISTS idx_embeddings_vector_cosine
            ON embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
    END IF;
END $$;

ALTER TABLE message_attachments ADD COLUMN IF NOT EXISTS conversation_id TEXT NOT NULL DEFAULT '';
ALTER TABLE message_attachments ADD COLUMN IF NOT EXISTS customer_id TEXT NOT NULL DEFAULT '';
ALTER TABLE message_attachments ADD COLUMN IF NOT EXISTS channel TEXT NOT NULL DEFAULT '';
ALTER TABLE message_attachments ADD COLUMN IF NOT EXISTS original_filename TEXT NOT NULL DEFAULT '';
ALTER TABLE message_attachments ADD COLUMN IF NOT EXISTS mime_type TEXT NOT NULL DEFAULT '';
ALTER TABLE message_attachments ADD COLUMN IF NOT EXISTS storage_url TEXT NOT NULL DEFAULT '';
ALTER TABLE message_attachments ADD COLUMN IF NOT EXISTS thumbnail_url TEXT NOT NULL DEFAULT '';
ALTER TABLE message_attachments ADD COLUMN IF NOT EXISTS provider_media_id TEXT NOT NULL DEFAULT '';
ALTER TABLE message_attachments ADD COLUMN IF NOT EXISTS raw_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE message_attachments ADD COLUMN IF NOT EXISTS image_analysis_status TEXT NOT NULL DEFAULT 'skipped';
ALTER TABLE message_attachments ADD COLUMN IF NOT EXISTS image_analysis_summary TEXT NOT NULL DEFAULT '';
ALTER TABLE message_attachments ADD COLUMN IF NOT EXISTS image_detected_objects JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE message_attachments ADD COLUMN IF NOT EXISTS image_ocr_text TEXT NOT NULL DEFAULT '';
ALTER TABLE message_attachments ADD COLUMN IF NOT EXISTS image_analysis_model TEXT NOT NULL DEFAULT '';
ALTER TABLE message_attachments ADD COLUMN IF NOT EXISTS image_analysis_error TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_message_attachments_conversation_id ON message_attachments(conversation_id);
CREATE INDEX IF NOT EXISTS idx_message_attachments_customer_id ON message_attachments(customer_id);
CREATE INDEX IF NOT EXISTS idx_message_attachments_image_analysis_status ON message_attachments(image_analysis_status);

CREATE TABLE IF NOT EXISTS message_reactions (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL DEFAULT '',
    conversation_id TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    provider_message_id TEXT NOT NULL DEFAULT '',
    target_provider_message_id TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    actor_type TEXT NOT NULL DEFAULT 'customer',
    actor_id TEXT NOT NULL DEFAULT '',
    emoji TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT 'added',
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_message_reactions_conversation_id ON message_reactions(conversation_id);
CREATE INDEX IF NOT EXISTS idx_message_reactions_message_id ON message_reactions(message_id);
CREATE INDEX IF NOT EXISTS idx_message_reactions_target_provider
    ON message_reactions(company_id, channel, target_provider_message_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_message_reactions_provider_event
    ON message_reactions(company_id, channel, provider_message_id) WHERE BTRIM(provider_message_id) <> '';

CREATE TABLE IF NOT EXISTS unprocessed_events (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    event_id TEXT NOT NULL,
    raw_payload TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT NOT NULL DEFAULT '',
    resolved_company_id TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_unprocessed_events_channel_event
    ON unprocessed_events(channel, event_id);
CREATE INDEX IF NOT EXISTS idx_unprocessed_events_status_retry
    ON unprocessed_events(status, next_retry_at);

ALTER TABLE messages ADD COLUMN IF NOT EXISTS idempotency_key TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_company_idempotency_key_nonempty
    ON messages(company_id, idempotency_key) WHERE BTRIM(idempotency_key) <> '';

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS is_group BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS group_id TEXT NOT NULL DEFAULT '';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS whatsapp_account_id TEXT NOT NULL DEFAULT '';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS identity_key TEXT NOT NULL DEFAULT '';

ALTER TABLE messages ADD COLUMN IF NOT EXISTS provider_event_id TEXT NOT NULL DEFAULT '';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS message_direction TEXT NOT NULL DEFAULT '';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT '';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS whatsapp_identity_id TEXT NOT NULL DEFAULT '';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS whatsapp_group_id TEXT NOT NULL DEFAULT '';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS whatsapp_group_name TEXT NOT NULL DEFAULT '';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS whatsapp_participant_id TEXT NOT NULL DEFAULT '';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS whatsapp_participant_name TEXT NOT NULL DEFAULT '';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS raw_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_company_provider_event_nonempty
    ON messages(company_id, provider_event_id) WHERE BTRIM(provider_event_id) <> '';

CREATE TABLE IF NOT EXISTS whatsapp_identity_mappings (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    channel TEXT NOT NULL DEFAULT 'whatsapp',
    account_id TEXT NOT NULL DEFAULT '',
    bridge_scope TEXT NOT NULL DEFAULT '',
    identity_type TEXT NOT NULL DEFAULT '',
    identity_value TEXT NOT NULL DEFAULT '',
    identity_value_normalized TEXT NOT NULL DEFAULT '',
    canonical_phone TEXT NOT NULL DEFAULT '',
    remote_jid TEXT NOT NULL DEFAULT '',
    lid_jid TEXT NOT NULL DEFAULT '',
    chat_id TEXT NOT NULL DEFAULT '',
    contact_id TEXT NOT NULL DEFAULT '',
    customer_id TEXT NOT NULL DEFAULT '',
    conversation_id TEXT NOT NULL DEFAULT '',
    group_id TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    profile_picture_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'resolved',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_identity_alias
    ON whatsapp_identity_mappings(company_id, channel, account_id, identity_type, identity_value_normalized)
    WHERE BTRIM(identity_value_normalized) <> '';
CREATE INDEX IF NOT EXISTS idx_whatsapp_identity_customer ON whatsapp_identity_mappings(company_id, customer_id);
CREATE INDEX IF NOT EXISTS idx_whatsapp_identity_conversation ON whatsapp_identity_mappings(company_id, conversation_id);
CREATE INDEX IF NOT EXISTS idx_whatsapp_identity_lid ON whatsapp_identity_mappings(company_id, lid_jid);
CREATE INDEX IF NOT EXISTS idx_whatsapp_identity_phone ON whatsapp_identity_mappings(company_id, canonical_phone);

CREATE TABLE IF NOT EXISTS whatsapp_event_dedup (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    channel TEXT NOT NULL DEFAULT 'whatsapp',
    account_id TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL DEFAULT '',
    provider_event_id TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL DEFAULT '',
    payload_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'seen',
    attempts INTEGER NOT NULL DEFAULT 1,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_event_provider
    ON whatsapp_event_dedup(company_id, channel, account_id, event_type, provider_event_id)
    WHERE BTRIM(provider_event_id) <> '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_event_idempotency
    ON whatsapp_event_dedup(company_id, idempotency_key) WHERE BTRIM(idempotency_key) <> '';

CREATE TABLE IF NOT EXISTS whatsapp_pending_messages (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    channel TEXT NOT NULL DEFAULT 'whatsapp',
    account_id TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL DEFAULT '',
    provider_event_id TEXT NOT NULL DEFAULT '',
    raw_identity TEXT NOT NULL DEFAULT '',
    identity_type TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending',
    customer_id TEXT NOT NULL DEFAULT '',
    conversation_id TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_pending_provider
    ON whatsapp_pending_messages(company_id, channel, account_id, provider_event_id)
    WHERE BTRIM(provider_event_id) <> '';

CREATE TABLE IF NOT EXISTS whatsapp_group_participants (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    account_id TEXT NOT NULL DEFAULT '',
    group_id TEXT NOT NULL DEFAULT '',
    participant_jid TEXT NOT NULL DEFAULT '',
    participant_phone TEXT NOT NULL DEFAULT '',
    participant_customer_id TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    profile_picture_url TEXT NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_group_participant
    ON whatsapp_group_participants(company_id, account_id, group_id, participant_jid)
    WHERE BTRIM(group_id) <> '' AND BTRIM(participant_jid) <> '';

ALTER TABLE IF EXISTS email_campaigns ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE IF EXISTS email_campaigns ADD COLUMN IF NOT EXISTS attachments JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE IF EXISTS email_campaigns ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE customer_channels ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE customer_channels ADD COLUMN IF NOT EXISTS is_visible BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE customer_channels ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE customer_channels ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE customer_social_profiles ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE customer_social_profiles ADD COLUMN IF NOT EXISTS is_visible BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE customer_social_profiles ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE customer_social_profiles ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
CREATE INDEX IF NOT EXISTS idx_customer_channels_visible_channel
    ON customer_channels(channel, is_active, is_visible);
CREATE INDEX IF NOT EXISTS idx_customer_social_profiles_visible_platform
    ON customer_social_profiles(platform, is_active, is_visible);

CREATE TABLE IF NOT EXISTS dead_letter_queue (
    id TEXT PRIMARY KEY,
    task_name TEXT NOT NULL DEFAULT '',
    event_id TEXT NOT NULL DEFAULT '',
    trace_id TEXT NOT NULL DEFAULT '',
    company_id TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    source_queue TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    status TEXT NOT NULL DEFAULT 'failed',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_dlq_status ON dead_letter_queue(status);
CREATE INDEX IF NOT EXISTS idx_dlq_company_id ON dead_letter_queue(company_id);
CREATE INDEX IF NOT EXISTS idx_dlq_created_at ON dead_letter_queue(created_at);
