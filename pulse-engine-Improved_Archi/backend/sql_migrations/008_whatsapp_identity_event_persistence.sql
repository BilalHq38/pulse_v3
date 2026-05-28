-- WhatsApp bridge identity, event dedupe, group, profile picture persistence.

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
    ON messages(company_id, provider_event_id)
    WHERE BTRIM(provider_event_id) <> '';

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
    ON whatsapp_event_dedup(company_id, idempotency_key)
    WHERE BTRIM(idempotency_key) <> '';

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
