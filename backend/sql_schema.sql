-- PostgreSQL schema for Pulse Engine
-- Single source of truth for a tenant-isolated, asyncpg-backed backend.

DO $$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS vector;
    EXCEPTION
        WHEN undefined_file THEN
            RAISE NOTICE 'pgvector not installed, skipping...';
        WHEN feature_not_supported THEN
            RAISE NOTICE 'pgvector extension not available on this PostgreSQL instance, skipping...';
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'pgvector extension unavailable due to permissions, skipping...';
    END;
END
$$;

-- Legacy auth/session tables replaced by sessions + security_events.
DROP TABLE IF EXISTS login_history;
DROP TABLE IF EXISTS auth_logs;
DROP TABLE IF EXISTS login_sessions;
DROP TABLE IF EXISTS user_sessions;

-- ============================================================================
-- Company and tenant root
-- ============================================================================

CREATE TABLE IF NOT EXISTS companies (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL CHECK (BTRIM(name) <> ''),
    is_active  BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,
    enterprise_team_gate_met BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_companies_active ON companies(is_active);
CREATE INDEX IF NOT EXISTS idx_companies_created_at ON companies(created_at);
ALTER TABLE companies ADD COLUMN IF NOT EXISTS enterprise_team_gate_met BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS deleted_companies (
    id         TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_deleted_companies_deleted_at ON deleted_companies(deleted_at);

CREATE TABLE IF NOT EXISTS roles (
    id          TEXT PRIMARY KEY,
    role_name   TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    perm_scope  TEXT NOT NULL DEFAULT 'company',
    perm_all    BOOLEAN NOT NULL DEFAULT FALSE,
    is_system   BOOLEAN NOT NULL DEFAULT FALSE,
    raw_data    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_roles_name UNIQUE (role_name)
);
ALTER TABLE roles ADD COLUMN IF NOT EXISTS is_system BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE roles ADD COLUMN IF NOT EXISTS raw_data JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS company_settings (
    id                      TEXT PRIMARY KEY,
    company_id              TEXT NOT NULL UNIQUE REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    industry                TEXT NOT NULL DEFAULT '',
    tagline                 TEXT NOT NULL DEFAULT '',
    description             TEXT NOT NULL DEFAULT '',
    logo_url                TEXT NOT NULL DEFAULT '',
    phone                   TEXT NOT NULL DEFAULT '',
    support_email           TEXT NOT NULL DEFAULT '',
    website_address         TEXT NOT NULL DEFAULT '',
    address_line1           TEXT NOT NULL DEFAULT '',
    address_info            TEXT NOT NULL DEFAULT '',
    city                    TEXT NOT NULL DEFAULT '',
    state                   TEXT NOT NULL DEFAULT '',
    country                 TEXT NOT NULL DEFAULT '',
    postal_code             TEXT NOT NULL DEFAULT '',
    social_linkedin         TEXT NOT NULL DEFAULT '',
    social_twitter          TEXT NOT NULL DEFAULT '',
    social_facebook         TEXT NOT NULL DEFAULT '',
    social_instagram        TEXT NOT NULL DEFAULT '',
    timezone                TEXT NOT NULL DEFAULT 'UTC',
    language                TEXT NOT NULL DEFAULT 'en',
    locale_information      TEXT NOT NULL DEFAULT 'en',
    date_format             TEXT NOT NULL DEFAULT 'YYYY-MM-DD',
    currency                TEXT NOT NULL DEFAULT 'USD',
    bh_start                TEXT NOT NULL DEFAULT '09:00',
    bh_end                  TEXT NOT NULL DEFAULT '18:00',
    bh_days                 TEXT NOT NULL DEFAULT 'Mon,Tue,Wed,Thu,Fri',
    ai_enabled              BOOLEAN NOT NULL DEFAULT TRUE,
    ai_confidence_threshold NUMERIC(4,2) NOT NULL DEFAULT 0.70,
    auto_assign             BOOLEAN NOT NULL DEFAULT TRUE,
    active_llm_engine_id    TEXT NOT NULL DEFAULT '',
    preferred_channels      JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_company_settings_company_id ON company_settings(company_id);
CREATE INDEX IF NOT EXISTS idx_company_settings_created_at ON company_settings(company_id, created_at);

ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS preferred_channels JSONB NOT NULL DEFAULT '[]'::jsonb;
-- Per-tenant default region (ISO 3166-1 alpha-2) for parsing local phone numbers; empty = use env WHATSAPP_DEFAULT_COUNTRY only.
ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS default_phone_region TEXT NOT NULL DEFAULT '';

-- ============================================================================
-- Users and authentication
-- ============================================================================

CREATE TABLE IF NOT EXISTS users (
    id                   TEXT PRIMARY KEY,
    company_id           TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    email                TEXT NOT NULL,
    password_hash        TEXT,
    name                 TEXT NOT NULL DEFAULT '',
    role                 TEXT NOT NULL DEFAULT 'company_agent',
    role_id              TEXT NOT NULL DEFAULT '',
    sub_role             TEXT NOT NULL DEFAULT '',
    status               TEXT NOT NULL DEFAULT 'active',
    avatar               TEXT NOT NULL DEFAULT '',
    phone                TEXT NOT NULL DEFAULT '',
    onboarding_completed BOOLEAN NOT NULL DEFAULT FALSE,
    plan_selected        BOOLEAN NOT NULL DEFAULT TRUE,
    billing_status       TEXT NOT NULL DEFAULT 'active',
    auth_provider        TEXT NOT NULL DEFAULT 'email',
    email_verified       BOOLEAN NOT NULL DEFAULT FALSE,
    last_login           TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_users_email UNIQUE (email),
    CONSTRAINT uq_users_company_row UNIQUE (company_id, id)
);
CREATE INDEX IF NOT EXISTS idx_users_company_id ON users(company_id);
CREATE INDEX IF NOT EXISTS idx_users_company_status ON users(company_id, status);
CREATE INDEX IF NOT EXISTS idx_users_company_created_at ON users(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_users_company_email ON users(company_id, email);

-- Tenant enrollment gates (onboarding wizard + plan selection); existing rows default to full access.
ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_selected BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS billing_status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS user_oauth_providers (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider    TEXT NOT NULL,
    provider_id TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_user_oauth UNIQUE (user_id, provider)
);
CREATE INDEX IF NOT EXISTS idx_user_oauth_company_id ON user_oauth_providers(company_id);
CREATE INDEX IF NOT EXISTS idx_user_oauth_user_id ON user_oauth_providers(user_id);
CREATE INDEX IF NOT EXISTS idx_user_oauth_created_at ON user_oauth_providers(company_id, created_at);

CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_token TEXT NOT NULL,
    ip_address    TEXT NOT NULL DEFAULT '',
    user_agent    TEXT NOT NULL DEFAULT '',
    device        TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'active',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    last_seen_at  TIMESTAMPTZ,
    expires_at    TIMESTAMPTZ NOT NULL,
    logout_time   TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_sessions_token UNIQUE (session_token)
);
CREATE INDEX IF NOT EXISTS idx_sessions_company_id ON sessions(company_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_company_status ON sessions(company_id, status);
CREATE INDEX IF NOT EXISTS idx_sessions_company_created_at ON sessions(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_active_lookup ON sessions(company_id, user_id, is_active);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash    TEXT NOT NULL,
    token_version INTEGER NOT NULL DEFAULT 0,
    user_agent    TEXT NOT NULL DEFAULT '',
    ip_address    TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at    TIMESTAMPTZ NOT NULL,
    last_used_at  TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ,
    revoke_reason TEXT NOT NULL DEFAULT '',
    rotated_to    TEXT NOT NULL DEFAULT '',
    CONSTRAINT uq_refresh_tokens_hash UNIQUE (token_hash)
);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_active ON refresh_tokens(user_id, revoked_at, expires_at);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_company_active ON refresh_tokens(company_id, revoked_at, expires_at);

CREATE TABLE IF NOT EXISTS security_events (
    id         TEXT PRIMARY KEY,
    company_id TEXT REFERENCES companies(id) ON DELETE CASCADE CHECK (company_id IS NULL OR BTRIM(company_id) <> ''),
    user_id    TEXT REFERENCES users(id) ON DELETE CASCADE,
    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    email      TEXT NOT NULL DEFAULT '',
    ip_address TEXT NOT NULL DEFAULT '',
    device     TEXT NOT NULL DEFAULT '',
    success    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_security_events_company_id ON security_events(company_id);
CREATE INDEX IF NOT EXISTS idx_security_events_user_id ON security_events(user_id);
CREATE INDEX IF NOT EXISTS idx_security_events_session_id ON security_events(session_id);
CREATE INDEX IF NOT EXISTS idx_security_events_company_event ON security_events(company_id, event_type);
CREATE INDEX IF NOT EXISTS idx_security_events_company_created_at ON security_events(company_id, created_at);

CREATE TABLE IF NOT EXISTS password_resets (
    id         TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email      TEXT NOT NULL,
    token      TEXT NOT NULL,
    used       BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_password_resets_token UNIQUE (token)
);
CREATE INDEX IF NOT EXISTS idx_password_resets_company_id ON password_resets(company_id);
CREATE INDEX IF NOT EXISTS idx_password_resets_company_created_at ON password_resets(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_password_resets_user_id ON password_resets(user_id);

CREATE TABLE IF NOT EXISTS email_verifications (
    id         TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email      TEXT NOT NULL,
    token      TEXT NOT NULL,
    used       BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_email_verifications_token UNIQUE (token)
);
CREATE INDEX IF NOT EXISTS idx_email_verifications_company_id ON email_verifications(company_id);
CREATE INDEX IF NOT EXISTS idx_email_verifications_company_created_at ON email_verifications(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_email_verifications_user_id ON email_verifications(user_id);

CREATE TABLE IF NOT EXISTS email_verification_resend_controls (
    user_id             TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    company_id          TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    email               TEXT NOT NULL DEFAULT '',
    resend_count        INTEGER NOT NULL DEFAULT 0,
    resend_available_at TIMESTAMPTZ,
    lock_until          TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_email_verification_resend_controls_company_id ON email_verification_resend_controls(company_id);

CREATE TABLE IF NOT EXISTS pending_signups (
    id                          TEXT PRIMARY KEY,
    email                       TEXT NOT NULL,
    password_hash               TEXT NOT NULL,
    name                        TEXT NOT NULL DEFAULT '',
    company_name                TEXT NOT NULL DEFAULT '',
    company_industry            TEXT NOT NULL DEFAULT '',
    timezone                    TEXT NOT NULL DEFAULT 'UTC',
    plan_code                   TEXT NOT NULL DEFAULT 'pro',
    status                      TEXT NOT NULL DEFAULT 'pending_payment',
    payment_status              TEXT NOT NULL DEFAULT 'pending',
    stripe_checkout_session_id  TEXT,
    stripe_customer_id          TEXT,
    stripe_subscription_id      TEXT,
    stripe_event_id             TEXT,
    company_id                  TEXT NOT NULL DEFAULT '',
    user_id                     TEXT NOT NULL DEFAULT '',
    verification_email_sent_at  TIMESTAMPTZ,
    verification_error          TEXT NOT NULL DEFAULT '',
    expires_at                  TIMESTAMPTZ NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_pending_signups_email UNIQUE (email)
);
CREATE INDEX IF NOT EXISTS idx_pending_signups_status ON pending_signups(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pending_signups_expires_at ON pending_signups(expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pending_signups_checkout_nonempty
    ON pending_signups (stripe_checkout_session_id)
    WHERE stripe_checkout_session_id IS NOT NULL
      AND BTRIM(stripe_checkout_session_id) <> '';

CREATE TABLE IF NOT EXISTS account_deletion_verifications (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email       TEXT NOT NULL,
    code_hash   TEXT NOT NULL,
    used        BOOLEAN NOT NULL DEFAULT FALSE,
    used_reason TEXT,
    used_at     TIMESTAMPTZ,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_account_delete_verifications_company_id ON account_deletion_verifications(company_id);
CREATE INDEX IF NOT EXISTS idx_account_delete_verifications_user_id ON account_deletion_verifications(user_id);

CREATE TABLE IF NOT EXISTS deleted_accounts (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    deleted_user_id TEXT NOT NULL,
    name            TEXT NOT NULL DEFAULT '',
    email           TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_deleted_accounts_company_id ON deleted_accounts(company_id);
CREATE INDEX IF NOT EXISTS idx_deleted_accounts_deleted_at ON deleted_accounts(company_id, deleted_at);

CREATE TABLE IF NOT EXISTS invitations (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    email       TEXT NOT NULL,
    token       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    role        TEXT NOT NULL DEFAULT 'company_agent',
    sub_role    TEXT NOT NULL DEFAULT '',
    invitee_status TEXT NOT NULL DEFAULT 'active',
    expires_at  TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_invitations_token UNIQUE (token)
);
ALTER TABLE invitations ADD COLUMN IF NOT EXISTS sub_role TEXT NOT NULL DEFAULT '';
ALTER TABLE invitations ADD COLUMN IF NOT EXISTS invitee_status TEXT NOT NULL DEFAULT 'active';
CREATE INDEX IF NOT EXISTS idx_invitations_company_id ON invitations(company_id);
CREATE INDEX IF NOT EXISTS idx_invitations_company_status ON invitations(company_id, status);
CREATE INDEX IF NOT EXISTS idx_invitations_company_created_at ON invitations(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_invitations_company_email_status ON invitations(company_id, email, status);

CREATE TABLE IF NOT EXISTS oauth_states (
    id              TEXT PRIMARY KEY,
    provider        TEXT NOT NULL,
    state_hash      TEXT NOT NULL,
    next_path       TEXT NOT NULL DEFAULT '',
    frontend_origin TEXT NOT NULL DEFAULT '',
    used            BOOLEAN NOT NULL DEFAULT FALSE,
    used_at         TIMESTAMPTZ,
    used_reason     TEXT,
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_oauth_states_provider_hash UNIQUE (provider, state_hash)
);
CREATE INDEX IF NOT EXISTS idx_oauth_states_expires_at ON oauth_states(expires_at);

ALTER TABLE oauth_states ADD COLUMN IF NOT EXISTS link_user_id TEXT;

CREATE TABLE IF NOT EXISTS api_keys (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    name            TEXT NOT NULL,
    key_prefix      TEXT NOT NULL,
    key_hash        TEXT NOT NULL,
    created_by      TEXT NOT NULL DEFAULT '',
    created_by_name TEXT NOT NULL DEFAULT '',
    last_used       TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'active',
    revoked_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_api_keys_company_id ON api_keys(company_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_company_status ON api_keys(company_id, status);
CREATE INDEX IF NOT EXISTS idx_api_keys_company_created_at ON api_keys(company_id, created_at);

-- ============================================================================
-- Reference tables
-- ============================================================================

CREATE TABLE IF NOT EXISTS lead_statuses (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    status_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    order_index INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lead_statuses_company_name UNIQUE (company_id, status_name)
);
CREATE INDEX IF NOT EXISTS idx_lead_statuses_company_id ON lead_statuses(company_id);
CREATE INDEX IF NOT EXISTS idx_lead_statuses_company_created_at ON lead_statuses(company_id, created_at);

CREATE TABLE IF NOT EXISTS sources (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'channel',
    platform    TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_sources_company_name UNIQUE (company_id, source_name)
);
CREATE INDEX IF NOT EXISTS idx_sources_company_id ON sources(company_id);
CREATE INDEX IF NOT EXISTS idx_sources_company_created_at ON sources(company_id, created_at);

CREATE TABLE IF NOT EXISTS channels (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    channel_name TEXT NOT NULL,
    channel_type TEXT NOT NULL DEFAULT 'messaging',
    platform     TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_channels_company_platform UNIQUE (company_id, platform)
);
CREATE INDEX IF NOT EXISTS idx_channels_company_id ON channels(company_id);
CREATE INDEX IF NOT EXISTS idx_channels_company_created_at ON channels(company_id, created_at);

CREATE TABLE IF NOT EXISTS ticket_statuses (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    status_name TEXT NOT NULL,
    color_code  TEXT NOT NULL DEFAULT '#94a3b8',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ticket_statuses_company_name UNIQUE (company_id, status_name)
);
CREATE INDEX IF NOT EXISTS idx_ticket_statuses_company_id ON ticket_statuses(company_id);
CREATE INDEX IF NOT EXISTS idx_ticket_statuses_company_created_at ON ticket_statuses(company_id, created_at);

CREATE TABLE IF NOT EXISTS channel_settings (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    channel         TEXT NOT NULL,
    display_name    TEXT NOT NULL DEFAULT '',
    enabled         BOOLEAN NOT NULL DEFAULT FALSE,
    api_key         TEXT NOT NULL DEFAULT '',
    api_secret      TEXT NOT NULL DEFAULT '',
    webhook_url     TEXT NOT NULL DEFAULT '',
    page_id         TEXT NOT NULL DEFAULT '',
    phone_number_id TEXT NOT NULL DEFAULT '',
    access_token    TEXT NOT NULL DEFAULT '',
    -- Email channel support (per-tenant IMAP/SMTP)
    imap_host       TEXT NOT NULL DEFAULT '',
    imap_port       INTEGER NOT NULL DEFAULT 993,
    imap_user       TEXT NOT NULL DEFAULT '',
    imap_pass_enc   TEXT NOT NULL DEFAULT '',
    smtp_host       TEXT NOT NULL DEFAULT '',
    smtp_port       INTEGER NOT NULL DEFAULT 587,
    smtp_user       TEXT NOT NULL DEFAULT '',
    smtp_pass_enc   TEXT NOT NULL DEFAULT '',
    widget_color    TEXT NOT NULL DEFAULT '#2563eb',
    welcome_message TEXT NOT NULL DEFAULT 'Hi! How can we help you today?',
    verify_token    TEXT NOT NULL DEFAULT '',
    email_address          TEXT NOT NULL DEFAULT '',
    email_provider         TEXT NOT NULL DEFAULT 'smtp_imap',
    email_send_enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    email_receive_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_channel_settings_company_channel UNIQUE (company_id, channel)
);
CREATE INDEX IF NOT EXISTS idx_channel_settings_company_id ON channel_settings(company_id);
CREATE INDEX IF NOT EXISTS idx_channel_settings_company_created_at ON channel_settings(company_id, created_at);
ALTER TABLE channel_settings ADD COLUMN IF NOT EXISTS email_address TEXT NOT NULL DEFAULT '';
ALTER TABLE channel_settings ADD COLUMN IF NOT EXISTS email_provider TEXT NOT NULL DEFAULT 'smtp_imap';
ALTER TABLE channel_settings ADD COLUMN IF NOT EXISTS email_send_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE channel_settings ADD COLUMN IF NOT EXISTS email_receive_enabled BOOLEAN NOT NULL DEFAULT TRUE;

CREATE TABLE IF NOT EXISTS templates (
    id         TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    name       TEXT NOT NULL,
    content    TEXT NOT NULL,
    category   TEXT NOT NULL DEFAULT 'general',
    channel    TEXT NOT NULL DEFAULT 'all',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_templates_company_id ON templates(company_id);
CREATE INDEX IF NOT EXISTS idx_templates_company_created_at ON templates(company_id, created_at);

-- ============================================================================
-- Leads
-- ============================================================================

CREATE TABLE IF NOT EXISTS leads (
    id             TEXT PRIMARY KEY,
    company_id     TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    name           TEXT NOT NULL,
    father_name    TEXT NOT NULL DEFAULT '',
    email          TEXT NOT NULL DEFAULT '',
    phone          TEXT NOT NULL DEFAULT '',
    customer_company_name TEXT NOT NULL DEFAULT '',
    address        TEXT NOT NULL DEFAULT '',
    city           TEXT NOT NULL DEFAULT '',
    state          TEXT NOT NULL DEFAULT '',
    country        TEXT NOT NULL DEFAULT '',
    source         TEXT NOT NULL DEFAULT 'web_chat',
    source_id      TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'new',
    status_id      TEXT NOT NULL DEFAULT '',
    score          INTEGER NOT NULL DEFAULT 50,
    grade          TEXT NOT NULL DEFAULT 'warm',
    phase          TEXT NOT NULL DEFAULT 'awareness',
    notes          TEXT NOT NULL DEFAULT '',
    assigned_to    TEXT NOT NULL DEFAULT '',
    assigned_name  TEXT NOT NULL DEFAULT '',
    scoring_reason TEXT NOT NULL DEFAULT '',
    next_action    TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_leads_company_row UNIQUE (company_id, id)
);
CREATE INDEX IF NOT EXISTS idx_leads_company_id ON leads(company_id);
CREATE INDEX IF NOT EXISTS idx_leads_company_status ON leads(company_id, status);
CREATE INDEX IF NOT EXISTS idx_leads_company_created_at ON leads(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_leads_company_grade ON leads(company_id, grade);
CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(phone);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);

CREATE TABLE IF NOT EXISTS leads_clean (
    id                TEXT PRIMARY KEY,
    company_id        TEXT NOT NULL,
    name              TEXT NOT NULL DEFAULT '',
    email_normalized  TEXT NOT NULL DEFAULT '',
    phone_digits      TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'new',
    source            TEXT NOT NULL DEFAULT 'web_chat',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cleaned_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_leads_clean_company ON leads_clean(company_id);
CREATE INDEX IF NOT EXISTS idx_leads_clean_email ON leads_clean(company_id, email_normalized);
CREATE INDEX IF NOT EXISTS idx_leads_clean_phone ON leads_clean(company_id, phone_digits);

CREATE TABLE IF NOT EXISTS lead_activities (
    id         TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    lead_id    TEXT NOT NULL,
    type       TEXT NOT NULL DEFAULT 'note',
    content    TEXT NOT NULL DEFAULT '',
    stage      TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_lead_activities_lead_company FOREIGN KEY (company_id, lead_id) REFERENCES leads(company_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_lead_activities_company_id ON lead_activities(company_id);
CREATE INDEX IF NOT EXISTS idx_lead_activities_company_lead ON lead_activities(company_id, lead_id);
CREATE INDEX IF NOT EXISTS idx_lead_activities_company_created_at ON lead_activities(company_id, created_at);

CREATE TABLE IF NOT EXISTS lead_nurture_messages (
    id         TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    lead_id    TEXT NOT NULL,
    message    TEXT NOT NULL,
    phase      TEXT NOT NULL DEFAULT 'awareness',
    sent       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_lead_nurture_messages_lead_company FOREIGN KEY (company_id, lead_id) REFERENCES leads(company_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_lead_nurture_messages_company_id ON lead_nurture_messages(company_id);
CREATE INDEX IF NOT EXISTS idx_lead_nurture_messages_company_lead ON lead_nurture_messages(company_id, lead_id);
CREATE INDEX IF NOT EXISTS idx_lead_nurture_messages_company_created_at ON lead_nurture_messages(company_id, created_at);

CREATE TABLE IF NOT EXISTS lead_channels (
    lead_id  TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    channel  TEXT NOT NULL,
    PRIMARY KEY (lead_id, channel)
);

-- ============================================================================
-- Customers
-- ============================================================================

CREATE TABLE IF NOT EXISTS customers (
    id                      TEXT PRIMARY KEY,
    company_id              TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    lead_id                 TEXT NOT NULL DEFAULT '',
    name                    TEXT NOT NULL,
    father_name             TEXT NOT NULL DEFAULT '',
    email                   TEXT NOT NULL DEFAULT '',
    phone                   TEXT NOT NULL DEFAULT '',
    customer_company_name   TEXT NOT NULL DEFAULT '',
    address                 TEXT NOT NULL DEFAULT '',
    city                    TEXT NOT NULL DEFAULT '',
    state                   TEXT NOT NULL DEFAULT '',
    country                 TEXT NOT NULL DEFAULT '',
    segment                 TEXT NOT NULL DEFAULT 'general',
    avatar                  TEXT NOT NULL DEFAULT '',
    lifecycle_stage         TEXT NOT NULL DEFAULT 'customer',
    lifetime_value          NUMERIC(14,2) NOT NULL DEFAULT 0,
    avg_sentiment           NUMERIC(5,4) NOT NULL DEFAULT 0,
    recent_tickets          INTEGER NOT NULL DEFAULT 0,
    complaint_count         INTEGER NOT NULL DEFAULT 0,
    days_since_last_contact INTEGER NOT NULL DEFAULT 0,
    total_conversations     INTEGER NOT NULL DEFAULT 0,
    long_term_summary       TEXT NOT NULL DEFAULT '',
    historical_sentiment    TEXT NOT NULL DEFAULT '',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_customers_company_row UNIQUE (company_id, id)
);
CREATE INDEX IF NOT EXISTS idx_customers_company_id ON customers(company_id);
CREATE INDEX IF NOT EXISTS idx_customers_company_lifecycle ON customers(company_id, lifecycle_stage);
CREATE INDEX IF NOT EXISTS idx_customers_company_created_at ON customers(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_customers_company_lookup ON customers(company_id, phone, email);
CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);
CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email);
-- Case-insensitive email + digits-only phone accelerators used by the
-- channel-layer identity resolver and the web-chat webhook fallback.
CREATE INDEX IF NOT EXISTS idx_customers_company_email_lower
    ON customers(company_id, LOWER(email));
CREATE INDEX IF NOT EXISTS idx_customers_company_phone_digits
    ON customers(company_id, regexp_replace(phone, '\D', '', 'g'));

CREATE TABLE IF NOT EXISTS customer_tags (
    customer_id TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    tag         TEXT NOT NULL,
    PRIMARY KEY (customer_id, tag)
);

CREATE TABLE IF NOT EXISTS customer_channels (
    customer_id TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    channel     TEXT NOT NULL,
    PRIMARY KEY (customer_id, channel)
);

CREATE TABLE IF NOT EXISTS customer_social_profiles (
    customer_id TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    platform    TEXT NOT NULL,
    profile_id  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (customer_id, platform)
);

CREATE TABLE IF NOT EXISTS customer_profiles (
    id               TEXT PRIMARY KEY,
    company_id       TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    customer_id      TEXT NOT NULL,
    engagement_level TEXT NOT NULL DEFAULT 'general',
    last_interaction TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_customer_profiles_customer UNIQUE (customer_id),
    CONSTRAINT fk_customer_profiles_customer_company FOREIGN KEY (company_id, customer_id) REFERENCES customers(company_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_customer_profiles_company_id ON customer_profiles(company_id);
CREATE INDEX IF NOT EXISTS idx_customer_profiles_company_created_at ON customer_profiles(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_customer_profiles_customer_id ON customer_profiles(customer_id);

CREATE TABLE IF NOT EXISTS customer_profile_preferences (
    customer_id TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    pref_key    TEXT NOT NULL,
    pref_value  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (customer_id, pref_key)
);

CREATE TABLE IF NOT EXISTS purchases (
    id               TEXT PRIMARY KEY,
    company_id       TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    customer_id      TEXT NOT NULL,
    amount           NUMERIC(14,2) NOT NULL DEFAULT 0,
    currency         TEXT NOT NULL DEFAULT 'USD',
    product_category TEXT NOT NULL DEFAULT 'general',
    product_name     TEXT NOT NULL DEFAULT '',
    product_sku      TEXT NOT NULL DEFAULT '',
    purchase_date    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_purchases_customer_company FOREIGN KEY (company_id, customer_id) REFERENCES customers(company_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_purchases_company_id ON purchases(company_id);
CREATE INDEX IF NOT EXISTS idx_purchases_company_created_at ON purchases(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_purchases_customer_id ON purchases(customer_id);

CREATE TABLE IF NOT EXISTS external_purchases (
    id             TEXT PRIMARY KEY,
    company_id     TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    customer_id    TEXT NOT NULL DEFAULT '',
    customer_phone TEXT NOT NULL DEFAULT '',
    customer_name  TEXT NOT NULL DEFAULT '',
    product_name   TEXT NOT NULL DEFAULT '',
    cost           NUMERIC(14,2) NOT NULL DEFAULT 0,
    currency       TEXT NOT NULL DEFAULT 'USD',
    purchased_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by     TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_external_purchases_company_id ON external_purchases(company_id);
CREATE INDEX IF NOT EXISTS idx_external_purchases_company_created_at ON external_purchases(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_external_purchases_phone ON external_purchases(customer_phone);

-- ============================================================================
-- Conversations and messages
-- ============================================================================

CREATE TABLE IF NOT EXISTS conversations (
    id                TEXT PRIMARY KEY,
    company_id        TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    customer_id       TEXT NOT NULL,
    customer_name     TEXT NOT NULL DEFAULT '',
    customer_avatar   TEXT NOT NULL DEFAULT '',
    channel           TEXT NOT NULL DEFAULT 'web_chat',
    channel_id        TEXT NOT NULL DEFAULT '',
    subject           TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'open',
    priority          TEXT NOT NULL DEFAULT 'medium',
    assigned_to       TEXT NOT NULL DEFAULT '',
    assigned_name     TEXT NOT NULL DEFAULT '',
    ai_handled        BOOLEAN NOT NULL DEFAULT TRUE,
    agent_type        TEXT NOT NULL DEFAULT 'generic',
    sentiment_score   NUMERIC(5,4) NOT NULL DEFAULT 0,
    sentiment_label   TEXT NOT NULL DEFAULT 'neutral',
    message_count     INTEGER NOT NULL DEFAULT 0,
    last_message      TEXT NOT NULL DEFAULT '',
    last_message_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    unread_count      INTEGER NOT NULL DEFAULT 0,
    escalation_notice TEXT,
    escalated_at      TIMESTAMPTZ,
    escalated_to      TEXT,
    escalated_to_name TEXT,
    session_id        TEXT NOT NULL DEFAULT '',
    page_url          TEXT NOT NULL DEFAULT '',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_conversations_company_row UNIQUE (company_id, id),
    CONSTRAINT fk_conversations_customer_company FOREIGN KEY (company_id, customer_id) REFERENCES customers(company_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_conversations_company_id ON conversations(company_id);
CREATE INDEX IF NOT EXISTS idx_conversations_company_status ON conversations(company_id, status);
CREATE INDEX IF NOT EXISTS idx_conversations_company_channel ON conversations(company_id, channel);
CREATE INDEX IF NOT EXISTS idx_conversations_company_created_at ON conversations(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_company_customer ON conversations(company_id, customer_id);
CREATE INDEX IF NOT EXISTS idx_conversations_last_message_at ON conversations(last_message_at);
CREATE INDEX IF NOT EXISTS idx_conversations_assigned_to ON conversations(assigned_to);

CREATE TABLE IF NOT EXISTS conversation_tags (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    tag             TEXT NOT NULL,
    PRIMARY KEY (conversation_id, tag)
);

CREATE TABLE IF NOT EXISTS messages (
    id                   TEXT PRIMARY KEY,
    company_id           TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    conversation_id      TEXT NOT NULL,
    content              TEXT NOT NULL,
    sender_type          TEXT NOT NULL DEFAULT 'agent',
    sender_id            TEXT NOT NULL DEFAULT '',
    sender_name          TEXT NOT NULL DEFAULT '',
    sentiment_score      NUMERIC(5,4),
    sentiment_emotion    TEXT,
    sentiment_confidence NUMERIC(5,4),
    intent_type          TEXT,
    intent_confidence    NUMERIC(5,4),
    ai_confidence        NUMERIC(5,4),
    external_message_id  TEXT NOT NULL DEFAULT '',
    idempotency_key      TEXT NOT NULL DEFAULT '',
    delivery_status      TEXT NOT NULL DEFAULT 'pending',
    sent_at              TIMESTAMPTZ,
    delivered_at         TIMESTAMPTZ,
    read_at              TIMESTAMPTZ,
    failed_at            TIMESTAMPTZ,
    is_alert             BOOLEAN NOT NULL DEFAULT FALSE,
    read                 BOOLEAN NOT NULL DEFAULT FALSE,
    edited_at            TIMESTAMPTZ,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_messages_company_row UNIQUE (company_id, id),
    CONSTRAINT fk_messages_conversation_company FOREIGN KEY (company_id, conversation_id) REFERENCES conversations(company_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_company_id ON messages(company_id);
CREATE INDEX IF NOT EXISTS idx_messages_company_created_at ON messages(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_company_conversation ON messages(company_id, conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_company_external_message_id ON messages(company_id, external_message_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_company_idempotency_key_nonempty
    ON messages(company_id, idempotency_key)
    WHERE BTRIM(idempotency_key) <> '';
CREATE INDEX IF NOT EXISTS idx_messages_sentiment ON messages(company_id, sentiment_score);

CREATE TABLE IF NOT EXISTS message_attachments (
    id         TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL DEFAULT '',
    customer_id TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    file_type  TEXT NOT NULL DEFAULT 'unknown',
    file_url   TEXT NOT NULL DEFAULT '',
    file_name  TEXT NOT NULL DEFAULT '',
    file_size  INTEGER NOT NULL DEFAULT 0,
    original_filename TEXT NOT NULL DEFAULT '',
    mime_type TEXT NOT NULL DEFAULT '',
    storage_url TEXT NOT NULL DEFAULT '',
    thumbnail_url TEXT NOT NULL DEFAULT '',
    provider_media_id TEXT NOT NULL DEFAULT '',
    raw_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    image_analysis_status TEXT NOT NULL DEFAULT 'skipped',
    image_analysis_summary TEXT NOT NULL DEFAULT '',
    image_detected_objects JSONB NOT NULL DEFAULT '[]'::jsonb,
    image_ocr_text TEXT NOT NULL DEFAULT '',
    image_analysis_model TEXT NOT NULL DEFAULT '',
    image_analysis_error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_message_attachments_company_id ON message_attachments(company_id);
CREATE INDEX IF NOT EXISTS idx_message_attachments_message_id ON message_attachments(message_id);
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
CREATE INDEX IF NOT EXISTS idx_message_reactions_target_provider ON message_reactions(company_id,channel,target_provider_message_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_message_reactions_provider_event
    ON message_reactions(company_id,channel,provider_message_id)
    WHERE BTRIM(provider_message_id) <> '';

CREATE TABLE IF NOT EXISTS conversation_logs (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    convo_id    TEXT NOT NULL,
    user_id     TEXT NOT NULL DEFAULT '',
    action_type TEXT NOT NULL,
    field_name  TEXT NOT NULL DEFAULT '',
    old_value   TEXT,
    new_value   TEXT,
    logged_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conversation_logs_company_id ON conversation_logs(company_id);
CREATE INDEX IF NOT EXISTS idx_conversation_logs_convo_id ON conversation_logs(convo_id);
CREATE INDEX IF NOT EXISTS idx_conversation_logs_company_created_at ON conversation_logs(company_id, created_at);

CREATE TABLE IF NOT EXISTS chat_histories (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    conversation_id TEXT NOT NULL DEFAULT '',
    customer_id     TEXT NOT NULL DEFAULT '',
    customer_name   TEXT NOT NULL DEFAULT '',
    channel         TEXT NOT NULL DEFAULT 'web_chat',
    sender_type     TEXT NOT NULL DEFAULT 'agent',
    sender_name     TEXT NOT NULL DEFAULT '',
    content         TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_histories_company_id ON chat_histories(company_id);
CREATE INDEX IF NOT EXISTS idx_chat_histories_conversation_id ON chat_histories(conversation_id);
CREATE INDEX IF NOT EXISTS idx_chat_histories_customer_id ON chat_histories(customer_id);

-- ============================================================================
-- Tickets
-- ============================================================================

CREATE TABLE IF NOT EXISTS tickets (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    ticket_number   TEXT NOT NULL,
    conversation_id TEXT NOT NULL DEFAULT '',
    customer_id     TEXT NOT NULL DEFAULT '',
    subject         TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    priority        TEXT NOT NULL DEFAULT 'medium',
    category        TEXT NOT NULL DEFAULT 'general',
    status          TEXT NOT NULL DEFAULT 'open',
    status_id       TEXT NOT NULL DEFAULT '',
    assigned_to     TEXT NOT NULL DEFAULT '',
    assigned_name   TEXT NOT NULL DEFAULT '',
    resolution      TEXT NOT NULL DEFAULT '',
    sla_deadline    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tickets_company_row UNIQUE (company_id, id),
    CONSTRAINT uq_tickets_ticket_number UNIQUE (ticket_number)
);
CREATE INDEX IF NOT EXISTS idx_tickets_company_id ON tickets(company_id);
CREATE INDEX IF NOT EXISTS idx_tickets_company_status ON tickets(company_id, status);
CREATE INDEX IF NOT EXISTS idx_tickets_company_created_at ON tickets(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tickets_assigned_to ON tickets(assigned_to);

CREATE TABLE IF NOT EXISTS ticket_notes (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    ticket_id   TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    content     TEXT NOT NULL,
    author_id   TEXT NOT NULL DEFAULT '',
    author_name TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ticket_notes_company_id ON ticket_notes(company_id);
CREATE INDEX IF NOT EXISTS idx_ticket_notes_ticket_id ON ticket_notes(ticket_id);

-- ============================================================================
-- Knowledge, products, FAQs, onboarding
-- ============================================================================

CREATE TABLE IF NOT EXISTS knowledge_base (
    id                 TEXT PRIMARY KEY,
    company_id         TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    title              TEXT NOT NULL,
    content            TEXT NOT NULL,
    category           TEXT NOT NULL DEFAULT 'general',
    key_points         TEXT NOT NULL DEFAULT '',
    is_prebuilt        BOOLEAN NOT NULL DEFAULT FALSE,
    ai_context_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    author_id          TEXT NOT NULL DEFAULT '',
    author_name        TEXT NOT NULL DEFAULT '',
    views              INTEGER NOT NULL DEFAULT 0,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_knowledge_base_company_row UNIQUE (company_id, id)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_base_company_id ON knowledge_base(company_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_base_company_category ON knowledge_base(company_id, category);
CREATE INDEX IF NOT EXISTS idx_knowledge_base_company_created_at ON knowledge_base(company_id, created_at);

CREATE TABLE IF NOT EXISTS knowledge_base_tags (
    kb_id TEXT NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
    tag   TEXT NOT NULL,
    PRIMARY KEY (kb_id, tag)
);

CREATE TABLE IF NOT EXISTS company_products (
    id             TEXT PRIMARY KEY,
    company_id     TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    name           TEXT NOT NULL,
    product_title  TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT '',
    price          TEXT NOT NULL DEFAULT '',
    price_currency TEXT NOT NULL DEFAULT 'USD',
    category       TEXT NOT NULL DEFAULT 'general',
    product_type   TEXT NOT NULL DEFAULT 'standard',
    status         TEXT NOT NULL DEFAULT 'active',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_company_products_company_row UNIQUE (company_id, id)
);
CREATE INDEX IF NOT EXISTS idx_company_products_company_id ON company_products(company_id);
CREATE INDEX IF NOT EXISTS idx_company_products_company_status ON company_products(company_id, status);
CREATE INDEX IF NOT EXISTS idx_company_products_company_created_at ON company_products(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_company_products_company_name ON company_products(company_id, name);
CREATE INDEX IF NOT EXISTS idx_company_products_company_name_lower ON company_products(company_id, LOWER(name));
CREATE INDEX IF NOT EXISTS idx_company_products_company_category ON company_products(company_id, category);

CREATE TABLE IF NOT EXISTS product_images (
    id         TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES company_products(id) ON DELETE CASCADE,
    image_url  TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_product_images_product_id ON product_images(product_id);

CREATE TABLE IF NOT EXISTS product_features (
    id         TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES company_products(id) ON DELETE CASCADE,
    feature    TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_product_features_product_id ON product_features(product_id);

CREATE TABLE IF NOT EXISTS company_faqs (
    id         TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    question   TEXT NOT NULL,
    answer     TEXT NOT NULL,
    category   TEXT NOT NULL DEFAULT 'general',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_company_faqs_company_id ON company_faqs(company_id);
CREATE INDEX IF NOT EXISTS idx_company_faqs_company_created_at ON company_faqs(company_id, created_at);

CREATE TABLE IF NOT EXISTS onboarding_docs (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    title       TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT 'general',
    file_name   TEXT NOT NULL DEFAULT '',
    file_type   TEXT NOT NULL DEFAULT '',
    file_data   TEXT,
    file_size   INTEGER NOT NULL DEFAULT 0,
    author_id   TEXT NOT NULL DEFAULT '',
    author_name TEXT NOT NULL DEFAULT '',
    views       INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_onboarding_docs_company_row UNIQUE (company_id, id)
);
CREATE INDEX IF NOT EXISTS idx_onboarding_docs_company_id ON onboarding_docs(company_id);
CREATE INDEX IF NOT EXISTS idx_onboarding_docs_company_created_at ON onboarding_docs(company_id, created_at);

CREATE TABLE IF NOT EXISTS onboarding_doc_tags (
    doc_id TEXT NOT NULL REFERENCES onboarding_docs(id) ON DELETE CASCADE,
    tag    TEXT NOT NULL,
    PRIMARY KEY (doc_id, tag)
);

-- ============================================================================
-- AI, memory, embeddings
-- ============================================================================

CREATE TABLE IF NOT EXISTS llm_engines (
    id           TEXT PRIMARY KEY,
    model_name   TEXT NOT NULL,
    provider     TEXT NOT NULL,
    api_endpoint TEXT NOT NULL DEFAULT '',
    temperature  NUMERIC(4,2) NOT NULL DEFAULT 0.7,
    max_tokens   INTEGER NOT NULL DEFAULT 2048,
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    version      TEXT NOT NULL DEFAULT 'current',
    last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ai_agents (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    llm_id        TEXT NOT NULL DEFAULT '',
    mcp_server_id TEXT NOT NULL DEFAULT '',
    agent_type    TEXT NOT NULL DEFAULT 'support',
    api_key_ref   TEXT NOT NULL DEFAULT '',
    provider      TEXT NOT NULL DEFAULT '',
    version       TEXT NOT NULL DEFAULT 'current',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ai_agents_company_row UNIQUE (company_id, id)
);
CREATE INDEX IF NOT EXISTS idx_ai_agents_company_id ON ai_agents(company_id);
CREATE INDEX IF NOT EXISTS idx_ai_agents_company_status ON ai_agents(company_id, is_active);
CREATE INDEX IF NOT EXISTS idx_ai_agents_company_created_at ON ai_agents(company_id, created_at);

CREATE TABLE IF NOT EXISTS ai_agent_config (
    agent_id   TEXT NOT NULL REFERENCES ai_agents(id) ON DELETE CASCADE,
    config_key TEXT NOT NULL,
    config_val TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (agent_id, config_key)
);

CREATE TABLE IF NOT EXISTS ai_sessions (
    id         TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    convo_id   TEXT NOT NULL DEFAULT '',
    llm_id     TEXT NOT NULL DEFAULT '',
    agent_id   TEXT NOT NULL DEFAULT '',
    prompt     TEXT NOT NULL DEFAULT '',
    response   TEXT NOT NULL DEFAULT '',
    confidence NUMERIC(5,4),
    source     TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ai_sessions_company_id ON ai_sessions(company_id);
CREATE INDEX IF NOT EXISTS idx_ai_sessions_company_created_at ON ai_sessions(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_sessions_convo_id ON ai_sessions(convo_id);

CREATE TABLE IF NOT EXISTS training_data (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    input_text    TEXT NOT NULL,
    output_text   TEXT NOT NULL,
    data_category TEXT NOT NULL DEFAULT 'general',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_training_data_company_id ON training_data(company_id);
CREATE INDEX IF NOT EXISTS idx_training_data_company_created_at ON training_data(company_id, created_at);

CREATE TABLE IF NOT EXISTS context_memories (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    convo_id        TEXT NOT NULL DEFAULT '',
    entity_id       TEXT NOT NULL DEFAULT '',
    entity_type     TEXT NOT NULL DEFAULT 'customer',
    memory_content  TEXT NOT NULL,
    memory_type     TEXT NOT NULL DEFAULT 'summary',
    relevance_score NUMERIC(5,4) NOT NULL DEFAULT 0.5,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_context_memories_key
    ON context_memories(company_id, entity_id, entity_type, memory_type, convo_id);
CREATE INDEX IF NOT EXISTS idx_context_memories_company_id ON context_memories(company_id);
CREATE INDEX IF NOT EXISTS idx_context_memories_company_created_at ON context_memories(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_context_memories_entity_id ON context_memories(entity_id);
CREATE INDEX IF NOT EXISTS idx_context_memories_lookup
    ON context_memories(company_id, entity_id, memory_type, updated_at DESC, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_context_memories_entity_type
    ON context_memories(company_id, entity_id, memory_type);
CREATE INDEX IF NOT EXISTS idx_context_memories_convo_id
    ON context_memories(company_id, convo_id);

-- Dead-letter queue for failed external/pipeline events.
CREATE TABLE IF NOT EXISTS dead_letter_queue (
    id            TEXT PRIMARY KEY,
    task_name     TEXT NOT NULL DEFAULT '',
    event_id      TEXT NOT NULL DEFAULT '',
    trace_id      TEXT NOT NULL DEFAULT '',
    company_id    TEXT NOT NULL DEFAULT '',
    channel       TEXT NOT NULL DEFAULT '',
    source_queue  TEXT NOT NULL DEFAULT '',
    event_type    TEXT NOT NULL DEFAULT '',
    payload       TEXT NOT NULL DEFAULT '',
    error         TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    retry_count   INTEGER NOT NULL DEFAULT 0,
    max_retries   INTEGER NOT NULL DEFAULT 3,
    status        TEXT NOT NULL DEFAULT 'failed',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_dlq_status ON dead_letter_queue(status);
CREATE INDEX IF NOT EXISTS idx_dlq_company_id ON dead_letter_queue(company_id);
CREATE INDEX IF NOT EXISTS idx_dlq_created_at ON dead_letter_queue(created_at);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        EXECUTE $ddl$
        CREATE TABLE IF NOT EXISTS embeddings (
            id          TEXT PRIMARY KEY,
            company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
            source_type TEXT NOT NULL DEFAULT '',
            source_id   TEXT NOT NULL DEFAULT '',
            chunk_index INTEGER NOT NULL DEFAULT 0,
            content     TEXT NOT NULL DEFAULT '',
            embedding   vector(768),
            metadata    TEXT NOT NULL DEFAULT '',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        $ddl$;
    ELSE
        EXECUTE $ddl$
        CREATE TABLE IF NOT EXISTS embeddings (
            id          TEXT PRIMARY KEY,
            company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
            source_type TEXT NOT NULL DEFAULT '',
            source_id   TEXT NOT NULL DEFAULT '',
            chunk_index INTEGER NOT NULL DEFAULT 0,
            content     TEXT NOT NULL DEFAULT '',
            embedding   TEXT NOT NULL DEFAULT '',
            metadata    TEXT NOT NULL DEFAULT '',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        $ddl$;
        RAISE NOTICE 'pgvector not installed, using TEXT embedding column and skipping vector index.';
    END IF;
END
$$;
CREATE INDEX IF NOT EXISTS idx_embeddings_company_id ON embeddings(company_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_company_created_at ON embeddings(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_embeddings_source ON embeddings(source_type, source_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_embeddings_company_source_chunk
    ON embeddings(company_id, source_type, source_id, chunk_index);
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')
       AND EXISTS (
           SELECT 1
           FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'embeddings'
             AND column_name = 'embedding'
             AND udt_name = 'vector'
       ) THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_embeddings_vector_cosine ON embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)';
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS embedding_jobs (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    job_type      TEXT NOT NULL DEFAULT 'index',
    source_type   TEXT NOT NULL DEFAULT '',
    source_id     TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT NOT NULL DEFAULT '',
    started_at    TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_embedding_jobs_company_id ON embedding_jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_embedding_jobs_company_status ON embedding_jobs(company_id, status);
CREATE INDEX IF NOT EXISTS idx_embedding_jobs_company_created_at ON embedding_jobs(company_id, created_at);

CREATE TABLE IF NOT EXISTS sentiment_analyses (
    id               TEXT PRIMARY KEY,
    company_id       TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    agent_id         TEXT NOT NULL DEFAULT '',
    entity_id        TEXT NOT NULL DEFAULT '',
    entity_type      TEXT NOT NULL DEFAULT 'conversation',
    analyzed_text    TEXT NOT NULL DEFAULT '',
    sentiment_score  NUMERIC(5,4) NOT NULL DEFAULT 0,
    sentiment_label  TEXT NOT NULL DEFAULT 'neutral',
    confidence_score NUMERIC(5,4) NOT NULL DEFAULT 0,
    emotion_joy      NUMERIC(5,4) NOT NULL DEFAULT 0,
    emotion_anger    NUMERIC(5,4) NOT NULL DEFAULT 0,
    emotion_sadness  NUMERIC(5,4) NOT NULL DEFAULT 0,
    emotion_fear     NUMERIC(5,4) NOT NULL DEFAULT 0,
    emotion_surprise NUMERIC(5,4) NOT NULL DEFAULT 0,
    analyzed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sentiment_analyses_company_id ON sentiment_analyses(company_id);
CREATE INDEX IF NOT EXISTS idx_sentiment_analyses_company_created_at ON sentiment_analyses(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sentiment_analyses_entity_id ON sentiment_analyses(entity_id);

CREATE TABLE IF NOT EXISTS user_ai_memories (
    id         TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    user_id    TEXT NOT NULL DEFAULT '',
    role       TEXT NOT NULL DEFAULT 'agent',
    content    TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_user_ai_memories_company_id ON user_ai_memories(company_id);
CREATE INDEX IF NOT EXISTS idx_user_ai_memories_company_created_at ON user_ai_memories(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_user_ai_memories_user_id ON user_ai_memories(user_id);

-- ============================================================================
-- MCP, webhooks, social
-- ============================================================================

CREATE TABLE IF NOT EXISTS mcp_servers (
    id             TEXT PRIMARY KEY,
    endpoint       TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'active',
    region         TEXT NOT NULL DEFAULT '',
    last_heartbeat TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_mcp_servers_endpoint UNIQUE (endpoint)
);

CREATE TABLE IF NOT EXISTS mcp_server_capabilities (
    server_id TEXT NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
    cap_key   TEXT NOT NULL,
    cap_value TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (server_id, cap_key)
);

CREATE TABLE IF NOT EXISTS mcp_clients (
    id             TEXT PRIMARY KEY,
    company_id     TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    server_id      TEXT NOT NULL DEFAULT '',
    user_id        TEXT NOT NULL DEFAULT '',
    client_type    TEXT NOT NULL DEFAULT 'internal',
    client_name    TEXT NOT NULL,
    platform       TEXT NOT NULL DEFAULT '',
    version        TEXT NOT NULL DEFAULT 'v1',
    last_connected TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mcp_clients_company_id ON mcp_clients(company_id);
CREATE INDEX IF NOT EXISTS idx_mcp_clients_company_created_at ON mcp_clients(company_id, created_at);

CREATE TABLE IF NOT EXISTS mcp_client_config (
    client_id  TEXT NOT NULL REFERENCES mcp_clients(id) ON DELETE CASCADE,
    config_key TEXT NOT NULL,
    config_val TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (client_id, config_key)
);

CREATE TABLE IF NOT EXISTS webhook_handlers (
    id                     TEXT PRIMARY KEY,
    company_id             TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    client_id              TEXT NOT NULL DEFAULT '',
    platform               TEXT NOT NULL,
    webhook_url            TEXT NOT NULL,
    verification_token_ref TEXT NOT NULL DEFAULT '',
    is_active              BOOLEAN NOT NULL DEFAULT TRUE,
    last_triggered         TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_webhook_handlers_company_row UNIQUE (company_id, id)
);
CREATE INDEX IF NOT EXISTS idx_webhook_handlers_company_id ON webhook_handlers(company_id);
CREATE INDEX IF NOT EXISTS idx_webhook_handlers_company_status ON webhook_handlers(company_id, is_active);
CREATE INDEX IF NOT EXISTS idx_webhook_handlers_company_created_at ON webhook_handlers(company_id, created_at);

CREATE TABLE IF NOT EXISTS webhook_events (
    id                TEXT PRIMARY KEY,
    company_id        TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    handler_id        TEXT NOT NULL DEFAULT '',
    event_type        TEXT NOT NULL,
    external_id       TEXT NOT NULL DEFAULT '',
    processing_status TEXT NOT NULL DEFAULT 'received',
    raw_payload       TEXT NOT NULL DEFAULT '',
    received_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_webhook_events_company_id ON webhook_events(company_id);
CREATE INDEX IF NOT EXISTS idx_webhook_events_company_status ON webhook_events(company_id, processing_status);
CREATE INDEX IF NOT EXISTS idx_webhook_events_company_created_at ON webhook_events(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_webhook_events_handler_id ON webhook_events(handler_id);

CREATE TABLE IF NOT EXISTS unprocessed_events (
    id                  TEXT PRIMARY KEY,
    channel             TEXT NOT NULL,
    event_id            TEXT NOT NULL,
    raw_payload         TEXT NOT NULL DEFAULT '',
    metadata            TEXT NOT NULL DEFAULT '',
    reason              TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'pending',
    retry_count         INTEGER NOT NULL DEFAULT 0,
    next_retry_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error          TEXT NOT NULL DEFAULT '',
    resolved_company_id TEXT NOT NULL DEFAULT '',
    resolved_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_unprocessed_events_channel_event UNIQUE (channel, event_id)
);
CREATE INDEX IF NOT EXISTS idx_unprocessed_events_status_retry ON unprocessed_events(status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_unprocessed_events_channel_status ON unprocessed_events(channel, status);

CREATE TABLE IF NOT EXISTS social_accounts (
    id               TEXT PRIMARY KEY,
    company_id       TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    platform         TEXT NOT NULL,
    account_handle   TEXT NOT NULL DEFAULT '',
    access_token_ref TEXT NOT NULL DEFAULT '',
    page_id          TEXT NOT NULL DEFAULT '',
    app_id           TEXT NOT NULL DEFAULT '',
    phone_number_id  TEXT NOT NULL DEFAULT '',
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    last_sync        TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_social_accounts_company_row UNIQUE (company_id, id)
);
CREATE INDEX IF NOT EXISTS idx_social_accounts_company_id ON social_accounts(company_id);
CREATE INDEX IF NOT EXISTS idx_social_accounts_company_status ON social_accounts(company_id, is_active);
CREATE INDEX IF NOT EXISTS idx_social_accounts_company_created_at ON social_accounts(company_id, created_at);

CREATE TABLE IF NOT EXISTS social_posts (
    id               TEXT PRIMARY KEY,
    company_id       TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    account_id       TEXT NOT NULL,
    platform         TEXT NOT NULL DEFAULT '',
    post_type        TEXT NOT NULL DEFAULT 'text',
    content          TEXT NOT NULL DEFAULT '',
    post_url         TEXT NOT NULL DEFAULT '',
    engagement_count INTEGER NOT NULL DEFAULT 0,
    comments_count   INTEGER NOT NULL DEFAULT 0,
    sentiment        NUMERIC(5,4) NOT NULL DEFAULT 0,
    posted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_social_posts_account_company FOREIGN KEY (company_id, account_id) REFERENCES social_accounts(company_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_social_posts_company_id ON social_posts(company_id);
CREATE INDEX IF NOT EXISTS idx_social_posts_company_created_at ON social_posts(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_social_posts_account_id ON social_posts(account_id);

-- ============================================================================
-- Analytics
-- ============================================================================

CREATE TABLE IF NOT EXISTS analytics_reports (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    report_type  TEXT NOT NULL,
    period       TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_analytics_reports_company_row UNIQUE (company_id, id)
);
CREATE INDEX IF NOT EXISTS idx_analytics_reports_company_id ON analytics_reports(company_id);
CREATE INDEX IF NOT EXISTS idx_analytics_reports_company_created_at ON analytics_reports(company_id, created_at);

CREATE TABLE IF NOT EXISTS analytics_report_data (
    report_id   TEXT NOT NULL REFERENCES analytics_reports(id) ON DELETE CASCADE,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    field_name  TEXT NOT NULL,
    field_value TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (report_id, field_name)
);
CREATE INDEX IF NOT EXISTS idx_analytics_report_data_company_id ON analytics_report_data(company_id);

CREATE TABLE IF NOT EXISTS metrics (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    report_id    TEXT NOT NULL,
    metric_name  TEXT NOT NULL,
    metric_value NUMERIC(18,4) NOT NULL DEFAULT 0,
    unit         TEXT NOT NULL DEFAULT 'count',
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_metrics_report_company FOREIGN KEY (company_id, report_id) REFERENCES analytics_reports(company_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_metrics_company_id ON metrics(company_id);
CREATE INDEX IF NOT EXISTS idx_metrics_company_created_at ON metrics(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_metrics_report_id ON metrics(report_id);

CREATE TABLE IF NOT EXISTS customer_interaction_summaries (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    customer_id     TEXT NOT NULL,
    customer_name   TEXT NOT NULL DEFAULT '',
    conversation_id TEXT NOT NULL,
    summary_date    DATE NOT NULL,
    summary_text    TEXT NOT NULL DEFAULT '',
    total_messages  INTEGER NOT NULL DEFAULT 0,
    avg_sentiment   NUMERIC(5,4) NOT NULL DEFAULT 0,
    escalated       BOOLEAN NOT NULL DEFAULT FALSE,
    ai_handled      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_customer_interaction_summaries_customer_company FOREIGN KEY (company_id, customer_id) REFERENCES customers(company_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_customer_interaction_summaries_conversation_company FOREIGN KEY (company_id, conversation_id) REFERENCES conversations(company_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_customer_interaction_summaries_company_id ON customer_interaction_summaries(company_id);
CREATE INDEX IF NOT EXISTS idx_customer_interaction_summaries_company_created_at ON customer_interaction_summaries(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_customer_interaction_summaries_summary_date ON customer_interaction_summaries(summary_date);

CREATE TABLE IF NOT EXISTS daily_summaries (
    id                 TEXT PRIMARY KEY,
    company_id         TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    summary_date       DATE NOT NULL,
    summary_text       TEXT NOT NULL DEFAULT '',
    total_interactions INTEGER NOT NULL DEFAULT 0,
    total_messages     INTEGER NOT NULL DEFAULT 0,
    avg_sentiment      NUMERIC(5,4) NOT NULL DEFAULT 0,
    escalations        INTEGER NOT NULL DEFAULT 0,
    ai_handled_count   INTEGER NOT NULL DEFAULT 0,
    generated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_daily_summaries_company_date UNIQUE (company_id, summary_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_summaries_company_id ON daily_summaries(company_id);
CREATE INDEX IF NOT EXISTS idx_daily_summaries_company_created_at ON daily_summaries(company_id, created_at);

CREATE TABLE IF NOT EXISTS raw_events (
    id                TEXT PRIMARY KEY,
    company_id        TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    source            TEXT NOT NULL,
    event_type        TEXT NOT NULL DEFAULT '',
    event_id          TEXT NOT NULL,
    external_id       TEXT NOT NULL DEFAULT '',
    payload           JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    payload_hash      TEXT NOT NULL DEFAULT '',
    processing_status TEXT NOT NULL DEFAULT 'queued',
    retry_count       INTEGER NOT NULL DEFAULT 0,
    last_error        TEXT NOT NULL DEFAULT '',
    occurred_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_raw_events_company_event UNIQUE (company_id, source, event_id)
);
CREATE INDEX IF NOT EXISTS idx_raw_events_company_created_at ON raw_events(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_raw_events_company_status ON raw_events(company_id, processing_status);

CREATE TABLE IF NOT EXISTS raw_messages (
    id                   TEXT PRIMARY KEY,
    company_id           TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    source               TEXT NOT NULL,
    dedupe_key           TEXT NOT NULL,
    canonical_message_id TEXT NOT NULL DEFAULT '',
    external_message_id  TEXT NOT NULL DEFAULT '',
    conversation_id      TEXT NOT NULL DEFAULT '',
    sender_type          TEXT NOT NULL DEFAULT '',
    payload              JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata             JSONB NOT NULL DEFAULT '{}'::jsonb,
    payload_hash         TEXT NOT NULL DEFAULT '',
    processing_status    TEXT NOT NULL DEFAULT 'queued',
    retry_count          INTEGER NOT NULL DEFAULT 0,
    last_error           TEXT NOT NULL DEFAULT '',
    occurred_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at         TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_raw_messages_company_dedupe UNIQUE (company_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_raw_messages_company_created_at ON raw_messages(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_raw_messages_company_conversation ON raw_messages(company_id, conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_raw_messages_company_message ON raw_messages(company_id, canonical_message_id);
CREATE INDEX IF NOT EXISTS idx_raw_messages_company_status ON raw_messages(company_id, processing_status);

CREATE TABLE IF NOT EXISTS raw_leads (
    id                TEXT PRIMARY KEY,
    company_id        TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    source            TEXT NOT NULL,
    dedupe_key        TEXT NOT NULL,
    canonical_lead_id TEXT NOT NULL DEFAULT '',
    email             TEXT NOT NULL DEFAULT '',
    phone             TEXT NOT NULL DEFAULT '',
    payload           JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    payload_hash      TEXT NOT NULL DEFAULT '',
    processing_status TEXT NOT NULL DEFAULT 'queued',
    retry_count       INTEGER NOT NULL DEFAULT 0,
    last_error        TEXT NOT NULL DEFAULT '',
    occurred_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_raw_leads_company_dedupe UNIQUE (company_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_raw_leads_company_created_at ON raw_leads(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_raw_leads_company_lead ON raw_leads(company_id, canonical_lead_id);
CREATE INDEX IF NOT EXISTS idx_raw_leads_company_status ON raw_leads(company_id, processing_status);

CREATE TABLE IF NOT EXISTS analytics_events (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    raw_table       TEXT NOT NULL,
    raw_id          TEXT NOT NULL,
    event_kind      TEXT NOT NULL,
    event_source    TEXT NOT NULL DEFAULT '',
    entity_type     TEXT NOT NULL DEFAULT '',
    entity_id       TEXT NOT NULL DEFAULT '',
    conversation_id TEXT NOT NULL DEFAULT '',
    lead_id         TEXT NOT NULL DEFAULT '',
    customer_id     TEXT NOT NULL DEFAULT '',
    metric_date     DATE NOT NULL,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_analytics_events_company_raw_kind UNIQUE (company_id, raw_table, raw_id, event_kind)
);
CREATE INDEX IF NOT EXISTS idx_analytics_events_company_date ON analytics_events(company_id, metric_date);
CREATE INDEX IF NOT EXISTS idx_analytics_events_company_kind ON analytics_events(company_id, event_kind, occurred_at);

CREATE TABLE IF NOT EXISTS lead_metrics (
    id                TEXT PRIMARY KEY,
    company_id        TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    lead_id           TEXT NOT NULL,
    metric_date       DATE NOT NULL,
    source            TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT '',
    phase             TEXT NOT NULL DEFAULT '',
    grade             TEXT NOT NULL DEFAULT '',
    name              TEXT NOT NULL DEFAULT '',
    email             TEXT NOT NULL DEFAULT '',
    phone             TEXT NOT NULL DEFAULT '',
    current_score     INTEGER NOT NULL DEFAULT 0,
    recommended_score INTEGER NOT NULL DEFAULT 0,
    recommended_grade TEXT NOT NULL DEFAULT '',
    scoring_reason    TEXT NOT NULL DEFAULT '',
    next_action       TEXT NOT NULL DEFAULT '',
    duplicate_count   INTEGER NOT NULL DEFAULT 0,
    is_duplicate      BOOLEAN NOT NULL DEFAULT FALSE,
    is_converted      BOOLEAN NOT NULL DEFAULT FALSE,
    payload           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lead_metrics_company_lead_date UNIQUE (company_id, lead_id, metric_date)
);
ALTER TABLE lead_metrics DROP CONSTRAINT IF EXISTS fk_lead_metrics_lead_company;
CREATE INDEX IF NOT EXISTS idx_lead_metrics_company_date ON lead_metrics(company_id, metric_date);
CREATE INDEX IF NOT EXISTS idx_lead_metrics_company_status ON lead_metrics(company_id, status, metric_date);

CREATE TABLE IF NOT EXISTS conversation_metrics (
    id                     TEXT PRIMARY KEY,
    company_id             TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    conversation_id        TEXT NOT NULL,
    metric_date            DATE NOT NULL,
    channel                TEXT NOT NULL DEFAULT 'web_chat',
    status                 TEXT NOT NULL DEFAULT 'open',
    customer_id            TEXT NOT NULL DEFAULT '',
    ai_handled             BOOLEAN NOT NULL DEFAULT TRUE,
    escalated              BOOLEAN NOT NULL DEFAULT FALSE,
    total_messages         INTEGER NOT NULL DEFAULT 0,
    customer_messages      INTEGER NOT NULL DEFAULT 0,
    agent_messages         INTEGER NOT NULL DEFAULT 0,
    ai_messages            INTEGER NOT NULL DEFAULT 0,
    system_messages        INTEGER NOT NULL DEFAULT 0,
    unread_count           INTEGER NOT NULL DEFAULT 0,
    avg_sentiment          NUMERIC(5,4) NOT NULL DEFAULT 0,
    latest_sentiment_label TEXT NOT NULL DEFAULT 'neutral',
    latest_sentiment_score NUMERIC(5,4),
    latest_intent_type     TEXT NOT NULL DEFAULT '',
    first_message_at       TIMESTAMPTZ,
    last_message_at        TIMESTAMPTZ,
    response_time_minutes  NUMERIC(10,2) NOT NULL DEFAULT 0,
    payload                JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_conversation_metrics_company_conversation_date UNIQUE (company_id, conversation_id, metric_date)
);
ALTER TABLE conversation_metrics DROP CONSTRAINT IF EXISTS fk_conversation_metrics_conversation_company;
CREATE INDEX IF NOT EXISTS idx_conversation_metrics_company_date ON conversation_metrics(company_id, metric_date);
CREATE INDEX IF NOT EXISTS idx_conversation_metrics_company_channel ON conversation_metrics(company_id, channel, metric_date);

CREATE TABLE IF NOT EXISTS sentiment_logs (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    raw_table       TEXT NOT NULL,
    raw_id          TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT '',
    entity_type     TEXT NOT NULL DEFAULT '',
    entity_id       TEXT NOT NULL DEFAULT '',
    conversation_id TEXT NOT NULL DEFAULT '',
    message_id      TEXT NOT NULL DEFAULT '',
    sentiment_label TEXT NOT NULL DEFAULT 'neutral',
    sentiment_score NUMERIC(5,4),
    emotion         TEXT NOT NULL DEFAULT '',
    confidence      NUMERIC(5,4),
    intent_type     TEXT NOT NULL DEFAULT '',
    analyzed_text   TEXT NOT NULL DEFAULT '',
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_sentiment_logs_company_raw UNIQUE (company_id, raw_table, raw_id)
);
CREATE INDEX IF NOT EXISTS idx_sentiment_logs_company_date ON sentiment_logs(company_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_sentiment_logs_company_label ON sentiment_logs(company_id, sentiment_label, occurred_at);

-- ============================================================================
-- Notifications, journeys, system logs
-- ============================================================================

CREATE TABLE IF NOT EXISTS notifications (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    title        TEXT NOT NULL,
    body         TEXT NOT NULL DEFAULT '',
    type         TEXT NOT NULL DEFAULT 'info',
    user_id      TEXT NOT NULL DEFAULT '',
    created_by   TEXT NOT NULL DEFAULT '',
    is_read      BOOLEAN NOT NULL DEFAULT FALSE,
    reminder_key TEXT,
    action_url   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notifications_company_id ON notifications(company_id);
CREATE INDEX IF NOT EXISTS idx_notifications_company_created_at ON notifications(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications(user_id, is_read);

CREATE TABLE IF NOT EXISTS notification_settings (
    user_id                      TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    company_id                   TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    notify_new_message           BOOLEAN NOT NULL DEFAULT TRUE,
    notify_new_lead              BOOLEAN NOT NULL DEFAULT TRUE,
    notify_new_ticket            BOOLEAN NOT NULL DEFAULT TRUE,
    notify_ticket_updated        BOOLEAN NOT NULL DEFAULT TRUE,
    notify_conversation_assigned BOOLEAN NOT NULL DEFAULT TRUE,
    notify_system_updates        BOOLEAN NOT NULL DEFAULT TRUE,
    email_digest                 BOOLEAN NOT NULL DEFAULT FALSE,
    email_digest_frequency       TEXT NOT NULL DEFAULT 'daily',
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notification_settings_company_id ON notification_settings(company_id);

CREATE TABLE IF NOT EXISTS journey_tracking (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    user_id      TEXT NOT NULL DEFAULT '',
    lead_id      TEXT NOT NULL DEFAULT '',
    customer_id  TEXT NOT NULL DEFAULT '',
    purchase_id  TEXT NOT NULL DEFAULT '',
    session_id   TEXT NOT NULL DEFAULT '',
    phase        TEXT NOT NULL DEFAULT 'awareness',
    source       TEXT NOT NULL DEFAULT '',
    started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_journey_tracking_company_id ON journey_tracking(company_id);
CREATE INDEX IF NOT EXISTS idx_journey_tracking_company_created_at ON journey_tracking(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_journey_tracking_customer_id ON journey_tracking(customer_id);
CREATE INDEX IF NOT EXISTS idx_journey_tracking_lead_id ON journey_tracking(lead_id);

CREATE TABLE IF NOT EXISTS journey_metadata (
    journey_id  TEXT NOT NULL REFERENCES journey_tracking(id) ON DELETE CASCADE,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    meta_key    TEXT NOT NULL,
    meta_value  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (journey_id, meta_key)
);
CREATE INDEX IF NOT EXISTS idx_journey_metadata_company_id ON journey_metadata(company_id);

CREATE TABLE IF NOT EXISTS system_logs (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    user_id     TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL,
    entity_type TEXT NOT NULL DEFAULT '',
    entity_id   TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_system_logs_company_id ON system_logs(company_id);
CREATE INDEX IF NOT EXISTS idx_system_logs_company_created_at ON system_logs(company_id, created_at);

CREATE TABLE IF NOT EXISTS system_log_metadata (
    log_id      TEXT NOT NULL REFERENCES system_logs(id) ON DELETE CASCADE,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    meta_key    TEXT NOT NULL,
    meta_value  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (log_id, meta_key)
);
CREATE INDEX IF NOT EXISTS idx_system_log_metadata_company_id ON system_log_metadata(company_id);

-- ============================================================================
-- Unified customer profiles
-- ============================================================================

CREATE TABLE IF NOT EXISTS unified_profiles (
    id                 TEXT PRIMARY KEY,
    company_id         TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    display_name       TEXT NOT NULL DEFAULT '',
    primary_email      TEXT NOT NULL DEFAULT '',
    primary_phone      TEXT NOT NULL DEFAULT '',
    total_interactions INTEGER NOT NULL DEFAULT 0,
    platforms_used     TEXT NOT NULL DEFAULT '',
    lifetime_value     NUMERIC(14,2) NOT NULL DEFAULT 0,
    overall_sentiment  NUMERIC(5,4) NOT NULL DEFAULT 0,
    unified_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_unified_profiles_company_row UNIQUE (company_id, id)
);
CREATE INDEX IF NOT EXISTS idx_unified_profiles_company_id ON unified_profiles(company_id);
CREATE INDEX IF NOT EXISTS idx_unified_profiles_company_created_at ON unified_profiles(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_unified_profiles_primary_email ON unified_profiles(primary_email);
CREATE INDEX IF NOT EXISTS idx_unified_profiles_primary_phone ON unified_profiles(primary_phone);

CREATE TABLE IF NOT EXISTS unified_profile_members (
    id                 TEXT PRIMARY KEY,
    company_id         TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    unified_profile_id TEXT NOT NULL REFERENCES unified_profiles(id) ON DELETE CASCADE,
    customer_id        TEXT NOT NULL,
    match_method       TEXT NOT NULL DEFAULT 'manual',
    match_confidence   NUMERIC(5,4) NOT NULL DEFAULT 0.0,
    is_primary         BOOLEAN NOT NULL DEFAULT FALSE,
    linked_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_unified_profile_member UNIQUE (unified_profile_id, customer_id),
    CONSTRAINT fk_unified_profile_members_customer_company FOREIGN KEY (company_id, customer_id) REFERENCES customers(company_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_unified_profile_members_company_id ON unified_profile_members(company_id);
CREATE INDEX IF NOT EXISTS idx_unified_profile_members_profile_id ON unified_profile_members(unified_profile_id);
CREATE INDEX IF NOT EXISTS idx_unified_profile_members_customer_id ON unified_profile_members(customer_id);

CREATE TABLE IF NOT EXISTS unification_merge_suggestions (
    id             TEXT PRIMARY KEY,
    company_id     TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    customer_id_a  TEXT NOT NULL,
    customer_id_b  TEXT NOT NULL,
    match_score    NUMERIC(5,4) NOT NULL DEFAULT 0.0,
    match_reasons  TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'pending',
    resolved_by    TEXT NOT NULL DEFAULT '',
    resolved_at    TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_unification_merge_suggestions_company_id ON unification_merge_suggestions(company_id);
CREATE INDEX IF NOT EXISTS idx_unification_merge_suggestions_company_status ON unification_merge_suggestions(company_id, status);
CREATE INDEX IF NOT EXISTS idx_unification_merge_suggestions_company_created_at ON unification_merge_suggestions(company_id, created_at);

-- ============================================================================
-- Identity service (tenant scoped)
-- ============================================================================

CREATE TABLE IF NOT EXISTS consent_ledger (
    consent_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            TEXT NOT NULL CHECK (BTRIM(tenant_id) <> ''),
    platform_user_id     TEXT NOT NULL,
    platform             TEXT NOT NULL,
    consent_given        BOOLEAN NOT NULL DEFAULT TRUE,
    consent_timestamp    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    consent_version      TEXT NOT NULL DEFAULT 'v1.0',
    consent_method       TEXT NOT NULL DEFAULT 'checkbox',
    consent_token        TEXT NOT NULL,
    ip_hash              TEXT,
    granular_consent     JSONB NOT NULL DEFAULT '{}'::jsonb,
    data_retention_days  INTEGER NOT NULL DEFAULT 365,
    revoked_at           TIMESTAMPTZ,
    CONSTRAINT uq_consent_ledger_tenant_token UNIQUE (tenant_id, consent_token)
);
CREATE INDEX IF NOT EXISTS idx_consent_ledger_tenant_id ON consent_ledger(tenant_id);
CREATE INDEX IF NOT EXISTS idx_consent_ledger_platform_user_id ON consent_ledger(platform_user_id);
CREATE INDEX IF NOT EXISTS idx_consent_ledger_platform ON consent_ledger(platform);
CREATE INDEX IF NOT EXISTS idx_consent_ledger_tenant_user_platform ON consent_ledger(tenant_id, platform_user_id, platform);
CREATE INDEX IF NOT EXISTS idx_consent_ledger_consent_token ON consent_ledger(consent_token);

CREATE TABLE IF NOT EXISTS unified_customers (
    customer_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id                   UUID UNIQUE,
    tenant_id            TEXT NOT NULL CHECK (BTRIM(tenant_id) <> ''),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active            BOOLEAN NOT NULL DEFAULT TRUE,
    profile_confidence   DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    primary_identity     TEXT,
    primary_name         TEXT,
    primary_phone_hash   VARCHAR(128),
    primary_email_hash   VARCHAR(128),
    embedding_vector     JSONB,
    signal_profile       JSONB NOT NULL DEFAULT '{}'::jsonb,
    consent_id           UUID REFERENCES consent_ledger(consent_id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_unified_customers_id ON unified_customers(id);
CREATE INDEX IF NOT EXISTS idx_unified_customers_customer_id ON unified_customers(customer_id);
CREATE INDEX IF NOT EXISTS idx_unified_customers_tenant_id ON unified_customers(tenant_id);
CREATE INDEX IF NOT EXISTS idx_unified_customers_tenant_active ON unified_customers(tenant_id, is_active);
CREATE INDEX IF NOT EXISTS idx_unified_customers_phone_hash ON unified_customers(primary_phone_hash);
CREATE INDEX IF NOT EXISTS idx_unified_customers_email_hash ON unified_customers(primary_email_hash);
CREATE INDEX IF NOT EXISTS idx_unified_customers_tenant_phone_hash ON unified_customers(tenant_id, primary_phone_hash);
CREATE INDEX IF NOT EXISTS idx_unified_customers_tenant_email_hash ON unified_customers(tenant_id, primary_email_hash);

CREATE TABLE IF NOT EXISTS identity_mappings (
    mapping_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id                    UUID UNIQUE,
    tenant_id             TEXT NOT NULL CHECK (BTRIM(tenant_id) <> ''),
    customer_id           UUID NOT NULL REFERENCES unified_customers(customer_id) ON DELETE CASCADE,
    platform              TEXT NOT NULL,
    platform_user_id      TEXT NOT NULL,
    platform_username     TEXT,
    phone                 TEXT,
    email                 TEXT,
    name                  TEXT,
    fingerprint           TEXT,
    linked_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confidence            DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    confidence_score      DOUBLE PRECISION,
    is_primary_platform   BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_identity_mapping_platform_user UNIQUE (tenant_id, platform, platform_user_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_identity_mappings_id ON identity_mappings(id);
CREATE INDEX IF NOT EXISTS idx_identity_mappings_mapping_id ON identity_mappings(mapping_id);
CREATE INDEX IF NOT EXISTS idx_identity_mappings_tenant_id ON identity_mappings(tenant_id);
CREATE INDEX IF NOT EXISTS idx_identity_mappings_customer_id ON identity_mappings(customer_id);
CREATE INDEX IF NOT EXISTS idx_identity_mappings_platform ON identity_mappings(platform);
CREATE INDEX IF NOT EXISTS idx_identity_mappings_platform_user_id ON identity_mappings(platform_user_id);
CREATE INDEX IF NOT EXISTS idx_identity_mappings_tenant_platform_user ON identity_mappings(tenant_id, platform, platform_user_id);

CREATE TABLE IF NOT EXISTS device_fingerprints (
    fingerprint_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            TEXT NOT NULL CHECK (BTRIM(tenant_id) <> ''),
    customer_id          UUID NOT NULL REFERENCES unified_customers(customer_id) ON DELETE CASCADE,
    fingerprint_hash     VARCHAR(128) NOT NULL,
    signals_json         JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    match_count          INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_device_fingerprints_tenant_id ON device_fingerprints(tenant_id);
CREATE INDEX IF NOT EXISTS idx_device_fingerprints_customer_id ON device_fingerprints(customer_id);
CREATE INDEX IF NOT EXISTS idx_device_fingerprints_fingerprint_hash ON device_fingerprints(fingerprint_hash);
CREATE INDEX IF NOT EXISTS idx_device_fingerprints_tenant_hash ON device_fingerprints(tenant_id, fingerprint_hash);

CREATE TABLE IF NOT EXISTS resolution_audit_log (
    resolution_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            TEXT NOT NULL CHECK (BTRIM(tenant_id) <> ''),
    customer_id          UUID REFERENCES unified_customers(customer_id) ON DELETE SET NULL,
    input_signals        JSONB NOT NULL DEFAULT '{}'::jsonb,
    score_breakdown      JSONB NOT NULL DEFAULT '{}'::jsonb,
    match_type           TEXT NOT NULL,
    confidence           DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    decision             TEXT NOT NULL,
    decision_reason      TEXT NOT NULL,
    processing_ms        INTEGER NOT NULL DEFAULT 0,
    merge_performed      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_resolution_audit_log_tenant_id ON resolution_audit_log(tenant_id);
CREATE INDEX IF NOT EXISTS idx_resolution_audit_log_customer_id ON resolution_audit_log(customer_id);
CREATE INDEX IF NOT EXISTS idx_resolution_audit_log_tenant_created_at ON resolution_audit_log(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_resolution_audit_log_match_type ON resolution_audit_log(match_type);

CREATE TABLE IF NOT EXISTS profile_merge_history (
    merge_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            TEXT NOT NULL CHECK (BTRIM(tenant_id) <> ''),
    source_customer_id   UUID REFERENCES unified_customers(customer_id) ON DELETE SET NULL,
    target_customer_id   UUID REFERENCES unified_customers(customer_id) ON DELETE SET NULL,
    merge_reason         TEXT NOT NULL,
    merged_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    merged_by            TEXT NOT NULL DEFAULT 'auto'
);
CREATE INDEX IF NOT EXISTS idx_profile_merge_history_tenant_id ON profile_merge_history(tenant_id);
CREATE INDEX IF NOT EXISTS idx_profile_merge_history_source ON profile_merge_history(source_customer_id);
CREATE INDEX IF NOT EXISTS idx_profile_merge_history_target ON profile_merge_history(target_customer_id);
CREATE INDEX IF NOT EXISTS idx_profile_merge_history_tenant_merged_at ON profile_merge_history(tenant_id, merged_at);
COMMENT ON COLUMN profile_merge_history.source_customer_id IS
    'Identity profile UUID from unified_customers.customer_id, not Pulse CRM customers.id.';
COMMENT ON COLUMN profile_merge_history.target_customer_id IS
    'Identity profile UUID from unified_customers.customer_id, not Pulse CRM customers.id.';

CREATE TABLE IF NOT EXISTS review_queue (
    review_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             TEXT NOT NULL CHECK (BTRIM(tenant_id) <> ''),
    resolution_id         UUID NOT NULL,
    source_customer_id    UUID REFERENCES unified_customers(customer_id) ON DELETE SET NULL,
    candidate_customer_id UUID REFERENCES unified_customers(customer_id) ON DELETE SET NULL,
    status                TEXT NOT NULL DEFAULT 'pending',
    source                TEXT NOT NULL DEFAULT 'internal',
    reason                TEXT NOT NULL,
    recommended_action    TEXT NOT NULL DEFAULT 'review',
    score_breakdown       JSONB NOT NULL DEFAULT '{}'::jsonb,
    independent_signals   JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at           TIMESTAMPTZ,
    review_notes          TEXT
);
CREATE INDEX IF NOT EXISTS idx_review_queue_tenant_id ON review_queue(tenant_id);
CREATE INDEX IF NOT EXISTS idx_review_queue_resolution_id ON review_queue(resolution_id);
CREATE INDEX IF NOT EXISTS idx_review_queue_source_customer_id ON review_queue(source_customer_id);
CREATE INDEX IF NOT EXISTS idx_review_queue_candidate_customer_id ON review_queue(candidate_customer_id);
CREATE INDEX IF NOT EXISTS idx_review_queue_status ON review_queue(status);
CREATE INDEX IF NOT EXISTS idx_review_queue_tenant_status ON review_queue(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_review_queue_source ON review_queue(source);

CREATE TABLE IF NOT EXISTS identity_history (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             TEXT NOT NULL CHECK (BTRIM(tenant_id) <> ''),
    customer_id           UUID REFERENCES unified_customers(customer_id) ON DELETE CASCADE,
    event_type            TEXT NOT NULL,
    data_snapshot         JSONB NOT NULL DEFAULT '{}'::jsonb,
    timestamp             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_identity_history_tenant_id ON identity_history(tenant_id);
CREATE INDEX IF NOT EXISTS idx_identity_history_customer ON identity_history(customer_id);
CREATE INDEX IF NOT EXISTS idx_identity_history_timestamp ON identity_history(timestamp);

CREATE TABLE IF NOT EXISTS consent_records (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             TEXT NOT NULL CHECK (BTRIM(tenant_id) <> ''),
    customer_id           UUID REFERENCES unified_customers(customer_id) ON DELETE SET NULL,
    consent_given         BOOLEAN NOT NULL DEFAULT FALSE,
    timestamp             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_consent_records_tenant_id ON consent_records(tenant_id);
CREATE INDEX IF NOT EXISTS idx_consent_records_customer ON consent_records(customer_id);
CREATE INDEX IF NOT EXISTS idx_consent_records_timestamp ON consent_records(timestamp);

CREATE TABLE IF NOT EXISTS identity_events (
    event_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             TEXT NOT NULL CHECK (BTRIM(tenant_id) <> ''),
    event_type            TEXT NOT NULL,
    aggregate_customer_id UUID,
    idempotency_key       VARCHAR(191),
    payload               JSONB NOT NULL DEFAULT '{}'::jsonb,
    status                TEXT NOT NULL DEFAULT 'pending',
    error_message         TEXT,
    published_at          TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_identity_events_tenant_id ON identity_events(tenant_id);
CREATE INDEX IF NOT EXISTS idx_identity_events_event_type ON identity_events(event_type);
CREATE INDEX IF NOT EXISTS idx_identity_events_status ON identity_events(status);
CREATE INDEX IF NOT EXISTS idx_identity_events_created_at ON identity_events(created_at);
CREATE INDEX IF NOT EXISTS idx_identity_events_aggregate_customer_id ON identity_events(aggregate_customer_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_identity_events_tenant_idempotency
    ON identity_events(tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS event_outbox (
    outbox_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id              UUID NOT NULL UNIQUE REFERENCES identity_events(event_id) ON DELETE CASCADE,
    tenant_id             TEXT NOT NULL CHECK (BTRIM(tenant_id) <> ''),
    stream_name           TEXT NOT NULL DEFAULT 'identity.events',
    idempotency_key       VARCHAR(191),
    payload               JSONB NOT NULL DEFAULT '{}'::jsonb,
    status                TEXT NOT NULL DEFAULT 'queued',
    retry_count           INTEGER NOT NULL DEFAULT 0,
    next_retry_at         TIMESTAMPTZ,
    last_error            TEXT,
    published_at          TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_event_outbox_tenant_id ON event_outbox(tenant_id);
CREATE INDEX IF NOT EXISTS idx_event_outbox_status ON event_outbox(status);
CREATE INDEX IF NOT EXISTS idx_event_outbox_next_retry ON event_outbox(next_retry_at);
CREATE INDEX IF NOT EXISTS idx_event_outbox_created_at ON event_outbox(created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_event_outbox_tenant_idempotency
    ON event_outbox(tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

ALTER TABLE identity_history ADD COLUMN IF NOT EXISTS tenant_id TEXT;
UPDATE identity_history ih
SET tenant_id = COALESCE(NULLIF(ih.tenant_id, ''), (SELECT uc.tenant_id FROM unified_customers uc WHERE uc.customer_id = ih.customer_id), 'demo_tenant')
WHERE ih.tenant_id IS NULL OR BTRIM(ih.tenant_id) = '';

ALTER TABLE consent_records ADD COLUMN IF NOT EXISTS tenant_id TEXT;
UPDATE consent_records cr
SET tenant_id = COALESCE(NULLIF(cr.tenant_id, ''), (SELECT uc.tenant_id FROM unified_customers uc WHERE uc.customer_id = cr.customer_id), 'demo_tenant')
WHERE cr.tenant_id IS NULL OR BTRIM(cr.tenant_id) = '';

ALTER TABLE dead_letter_queue ADD COLUMN IF NOT EXISTS task_name TEXT NOT NULL DEFAULT '';
ALTER TABLE dead_letter_queue ADD COLUMN IF NOT EXISTS event_id TEXT NOT NULL DEFAULT '';
ALTER TABLE dead_letter_queue ADD COLUMN IF NOT EXISTS trace_id TEXT NOT NULL DEFAULT '';
ALTER TABLE dead_letter_queue ADD COLUMN IF NOT EXISTS channel TEXT NOT NULL DEFAULT '';
ALTER TABLE dead_letter_queue ADD COLUMN IF NOT EXISTS error TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_unified_customers_customer_id ON unified_customers(customer_id);
CREATE INDEX IF NOT EXISTS idx_identity_mappings_mapping_id ON identity_mappings(mapping_id);
CREATE INDEX IF NOT EXISTS idx_review_queue_status ON review_queue(status);

UPDATE resolution_audit_log ral
SET customer_id = NULL
WHERE customer_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM unified_customers uc
      WHERE uc.customer_id = ral.customer_id
  );

UPDATE profile_merge_history pmh
SET source_customer_id = NULL
WHERE source_customer_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM unified_customers uc
      WHERE uc.customer_id = pmh.source_customer_id
  );

UPDATE profile_merge_history pmh
SET target_customer_id = NULL
WHERE target_customer_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM unified_customers uc
      WHERE uc.customer_id = pmh.target_customer_id
  );

UPDATE review_queue rq
SET source_customer_id = NULL
WHERE source_customer_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM unified_customers uc
      WHERE uc.customer_id = rq.source_customer_id
  );

UPDATE review_queue rq
SET candidate_customer_id = NULL
WHERE candidate_customer_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM unified_customers uc
      WHERE uc.customer_id = rq.candidate_customer_id
  );

ALTER TABLE profile_merge_history ALTER COLUMN source_customer_id DROP NOT NULL;
ALTER TABLE profile_merge_history ALTER COLUMN target_customer_id DROP NOT NULL;
ALTER TABLE review_queue ALTER COLUMN source_customer_id DROP NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_resolution_audit_log_customer_id'
    ) THEN
        ALTER TABLE resolution_audit_log
            ADD CONSTRAINT fk_resolution_audit_log_customer_id
            FOREIGN KEY (customer_id) REFERENCES unified_customers(customer_id) ON DELETE SET NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_profile_merge_history_source_customer_id'
    ) THEN
        ALTER TABLE profile_merge_history
            ADD CONSTRAINT fk_profile_merge_history_source_customer_id
            FOREIGN KEY (source_customer_id) REFERENCES unified_customers(customer_id) ON DELETE SET NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_profile_merge_history_target_customer_id'
    ) THEN
        ALTER TABLE profile_merge_history
            ADD CONSTRAINT fk_profile_merge_history_target_customer_id
            FOREIGN KEY (target_customer_id) REFERENCES unified_customers(customer_id) ON DELETE SET NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_review_queue_source_customer_id'
    ) THEN
        ALTER TABLE review_queue
            ADD CONSTRAINT fk_review_queue_source_customer_id
            FOREIGN KEY (source_customer_id) REFERENCES unified_customers(customer_id) ON DELETE SET NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_review_queue_candidate_customer_id'
    ) THEN
        ALTER TABLE review_queue
            ADD CONSTRAINT fk_review_queue_candidate_customer_id
            FOREIGN KEY (candidate_customer_id) REFERENCES unified_customers(customer_id) ON DELETE SET NULL;
    END IF;
END $$;


-- ============================================================================
-- Compatibility migrations for older local databases
-- ============================================================================

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS agent_type TEXT NOT NULL DEFAULT 'generic';

-- Adaptive qualification + onboarding flow state (JSONB, no schema drift required for callers).
ALTER TABLE leads ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE messages ADD COLUMN IF NOT EXISTS external_message_id TEXT NOT NULL DEFAULT '';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS idempotency_key TEXT NOT NULL DEFAULT '';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS delivery_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS failed_at TIMESTAMPTZ;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
CREATE INDEX IF NOT EXISTS idx_messages_company_external_message_id ON messages(company_id, external_message_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_company_idempotency_key_nonempty
    ON messages(company_id, idempotency_key)
    WHERE BTRIM(idempotency_key) <> '';

ALTER TABLE user_oauth_providers ADD COLUMN IF NOT EXISTS company_id TEXT;
UPDATE user_oauth_providers uop
SET company_id = u.company_id
FROM users u
WHERE u.id = uop.user_id
  AND (uop.company_id IS NULL OR BTRIM(uop.company_id) = '');
CREATE INDEX IF NOT EXISTS idx_user_oauth_company_id ON user_oauth_providers(company_id);

ALTER TABLE email_verification_resend_controls ADD COLUMN IF NOT EXISTS company_id TEXT;
UPDATE email_verification_resend_controls evrc
SET company_id = u.company_id
FROM users u
WHERE u.id = evrc.user_id
  AND (evrc.company_id IS NULL OR BTRIM(evrc.company_id) = '');
CREATE INDEX IF NOT EXISTS idx_email_verification_resend_controls_company_id ON email_verification_resend_controls(company_id);

ALTER TABLE message_attachments ADD COLUMN IF NOT EXISTS company_id TEXT;
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
UPDATE message_attachments ma
SET company_id = m.company_id
FROM messages m
WHERE m.id = ma.message_id
  AND (ma.company_id IS NULL OR BTRIM(ma.company_id) = '');
UPDATE message_attachments ma
SET conversation_id = m.conversation_id
FROM messages m
WHERE m.id = ma.message_id
  AND (ma.conversation_id IS NULL OR BTRIM(ma.conversation_id) = '');
CREATE INDEX IF NOT EXISTS idx_message_attachments_company_id ON message_attachments(company_id);
CREATE INDEX IF NOT EXISTS idx_message_attachments_conversation_id ON message_attachments(conversation_id);
CREATE INDEX IF NOT EXISTS idx_message_attachments_customer_id ON message_attachments(customer_id);
CREATE INDEX IF NOT EXISTS idx_message_attachments_image_analysis_status ON message_attachments(image_analysis_status);

ALTER TABLE conversation_logs ADD COLUMN IF NOT EXISTS company_id TEXT;
UPDATE conversation_logs cl
SET company_id = c.company_id
FROM conversations c
WHERE c.id = cl.convo_id
  AND (cl.company_id IS NULL OR BTRIM(cl.company_id) = '');
CREATE INDEX IF NOT EXISTS idx_conversation_logs_company_id ON conversation_logs(company_id);

ALTER TABLE chat_histories ADD COLUMN IF NOT EXISTS company_id TEXT;
UPDATE chat_histories ch
SET company_id = c.company_id
FROM conversations c
WHERE c.id = ch.conversation_id
  AND (ch.company_id IS NULL OR BTRIM(ch.company_id) = '');
CREATE INDEX IF NOT EXISTS idx_chat_histories_company_id ON chat_histories(company_id);

ALTER TABLE ticket_notes ADD COLUMN IF NOT EXISTS company_id TEXT;
UPDATE ticket_notes tn
SET company_id = t.company_id
FROM tickets t
WHERE t.id = tn.ticket_id
  AND (tn.company_id IS NULL OR BTRIM(tn.company_id) = '');
CREATE INDEX IF NOT EXISTS idx_ticket_notes_company_id ON ticket_notes(company_id);

ALTER TABLE analytics_report_data ADD COLUMN IF NOT EXISTS company_id TEXT;
UPDATE analytics_report_data ard
SET company_id = ar.company_id
FROM analytics_reports ar
WHERE ar.id = ard.report_id
  AND (ard.company_id IS NULL OR BTRIM(ard.company_id) = '');
CREATE INDEX IF NOT EXISTS idx_analytics_report_data_company_id ON analytics_report_data(company_id);

ALTER TABLE journey_metadata ADD COLUMN IF NOT EXISTS company_id TEXT;
UPDATE journey_metadata jm
SET company_id = jt.company_id
FROM journey_tracking jt
WHERE jt.id = jm.journey_id
  AND (jm.company_id IS NULL OR BTRIM(jm.company_id) = '');
CREATE INDEX IF NOT EXISTS idx_journey_metadata_company_id ON journey_metadata(company_id);

ALTER TABLE system_log_metadata ADD COLUMN IF NOT EXISTS company_id TEXT;
UPDATE system_log_metadata slm
SET company_id = sl.company_id
FROM system_logs sl
WHERE sl.id = slm.log_id
  AND (slm.company_id IS NULL OR BTRIM(slm.company_id) = '');
CREATE INDEX IF NOT EXISTS idx_system_log_metadata_company_id ON system_log_metadata(company_id);

ALTER TABLE deleted_companies ADD COLUMN IF NOT EXISTS metadata TEXT NOT NULL DEFAULT '';

-- ============================================================================
-- Seed data
-- ============================================================================

-- Services enforce UUID tenant IDs; keep a stable UUID for local bootstrapping.
-- This matches `.env` DEFAULT_TENANT_ID and WHATSAPP_BRIDGE default.
INSERT INTO companies(id, name, is_active, created_at, updated_at)
VALUES ('d7c253c7-3c35-47c6-8f93-b10cf50a0370', 'Local Tenant', TRUE, NOW(), NOW())
ON CONFLICT (id) DO NOTHING;

INSERT INTO company_settings(id, company_id, created_at, updated_at)
VALUES (
  'company_settings_d7c253c7-3c35-47c6-8f93-b10cf50a0370',
  'd7c253c7-3c35-47c6-8f93-b10cf50a0370',
  NOW(),
  NOW()
)
ON CONFLICT (company_id) DO NOTHING;

INSERT INTO roles (id, role_name, description, perm_scope, perm_all)
VALUES
    ('role_super_admin', 'super_admin', 'Platform-wide administrator', 'platform', TRUE),
    ('role_admin', 'admin', 'Tenant administrator', 'company', FALSE),
    ('role_agent', 'company_agent', 'Company agent', 'company', FALSE)
ON CONFLICT (role_name) DO NOTHING;

-- ============================================================================
-- Row-level security
-- ============================================================================

ALTER TABLE companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_companies_tenant ON companies;
CREATE POLICY p_companies_tenant ON companies
USING (id = current_setting('app.current_company', true))
WITH CHECK (id = current_setting('app.current_company', true));

ALTER TABLE company_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE company_settings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_company_settings_tenant ON company_settings;
CREATE POLICY p_company_settings_tenant ON company_settings
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_users_tenant ON users;
CREATE POLICY p_users_tenant ON users
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));
DROP POLICY IF EXISTS p_users_public_auth_lookup ON users;
CREATE POLICY p_users_public_auth_lookup ON users
FOR SELECT
USING (
    current_setting('app.public_auth_mode', true) = 'on'
    AND email = current_setting('app.auth_email', true)
);
DROP POLICY IF EXISTS p_users_public_auth_update ON users;
CREATE POLICY p_users_public_auth_update ON users
FOR UPDATE
USING (
    current_setting('app.public_auth_mode', true) = 'on'
    AND email = current_setting('app.auth_email', true)
)
WITH CHECK (
    current_setting('app.public_auth_mode', true) = 'on'
    AND email = current_setting('app.auth_email', true)
    AND company_id = current_setting('app.current_company', true)
);

ALTER TABLE user_oauth_providers ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_oauth_providers FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_user_oauth_providers_tenant ON user_oauth_providers;
CREATE POLICY p_user_oauth_providers_tenant ON user_oauth_providers
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_sessions_tenant ON sessions;
CREATE POLICY p_sessions_tenant ON sessions
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));
DROP POLICY IF EXISTS p_sessions_public_auth_lookup ON sessions;
CREATE POLICY p_sessions_public_auth_lookup ON sessions
FOR SELECT
USING (
    current_setting('app.public_auth_mode', true) = 'on'
    AND session_token = current_setting('app.auth_session_token', true)
);

ALTER TABLE security_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE security_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_security_events_tenant ON security_events;
CREATE POLICY p_security_events_tenant ON security_events
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE password_resets ENABLE ROW LEVEL SECURITY;
ALTER TABLE password_resets FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_password_resets_tenant ON password_resets;
CREATE POLICY p_password_resets_tenant ON password_resets
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));
DROP POLICY IF EXISTS p_password_resets_public_auth_lookup ON password_resets;
CREATE POLICY p_password_resets_public_auth_lookup ON password_resets
FOR SELECT
USING (
    current_setting('app.public_auth_mode', true) = 'on'
    AND token = current_setting('app.auth_token', true)
);

ALTER TABLE email_verifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_verifications FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_email_verifications_tenant ON email_verifications;
CREATE POLICY p_email_verifications_tenant ON email_verifications
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));
DROP POLICY IF EXISTS p_email_verifications_public_auth_lookup ON email_verifications;
CREATE POLICY p_email_verifications_public_auth_lookup ON email_verifications
FOR SELECT
USING (
    current_setting('app.public_auth_mode', true) = 'on'
    AND token = current_setting('app.auth_token', true)
);

ALTER TABLE email_verification_resend_controls ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_verification_resend_controls FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_email_verification_resend_controls_tenant ON email_verification_resend_controls;
CREATE POLICY p_email_verification_resend_controls_tenant ON email_verification_resend_controls
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE account_deletion_verifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_deletion_verifications FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_account_deletion_verifications_tenant ON account_deletion_verifications;
CREATE POLICY p_account_deletion_verifications_tenant ON account_deletion_verifications
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE deleted_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE deleted_accounts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_deleted_accounts_tenant ON deleted_accounts;
CREATE POLICY p_deleted_accounts_tenant ON deleted_accounts
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE invitations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_invitations_tenant ON invitations;
CREATE POLICY p_invitations_tenant ON invitations
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_api_keys_tenant ON api_keys;
CREATE POLICY p_api_keys_tenant ON api_keys
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE lead_statuses ENABLE ROW LEVEL SECURITY;
ALTER TABLE lead_statuses FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_lead_statuses_tenant ON lead_statuses;
CREATE POLICY p_lead_statuses_tenant ON lead_statuses
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE sources FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_sources_tenant ON sources;
CREATE POLICY p_sources_tenant ON sources
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE channels ENABLE ROW LEVEL SECURITY;
ALTER TABLE channels FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_channels_tenant ON channels;
CREATE POLICY p_channels_tenant ON channels
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE ticket_statuses ENABLE ROW LEVEL SECURITY;
ALTER TABLE ticket_statuses FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_ticket_statuses_tenant ON ticket_statuses;
CREATE POLICY p_ticket_statuses_tenant ON ticket_statuses
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE channel_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE channel_settings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_channel_settings_tenant ON channel_settings;
CREATE POLICY p_channel_settings_tenant ON channel_settings
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE templates FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_templates_tenant ON templates;
CREATE POLICY p_templates_tenant ON templates
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_leads_tenant ON leads;
CREATE POLICY p_leads_tenant ON leads
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE lead_activities ENABLE ROW LEVEL SECURITY;
ALTER TABLE lead_activities FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_lead_activities_tenant ON lead_activities;
CREATE POLICY p_lead_activities_tenant ON lead_activities
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE lead_nurture_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE lead_nurture_messages FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_lead_nurture_messages_tenant ON lead_nurture_messages;
CREATE POLICY p_lead_nurture_messages_tenant ON lead_nurture_messages
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_customers_tenant ON customers;
CREATE POLICY p_customers_tenant ON customers
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE customer_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_profiles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_customer_profiles_tenant ON customer_profiles;
CREATE POLICY p_customer_profiles_tenant ON customer_profiles
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE purchases ENABLE ROW LEVEL SECURITY;
ALTER TABLE purchases FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_purchases_tenant ON purchases;
CREATE POLICY p_purchases_tenant ON purchases
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE external_purchases ENABLE ROW LEVEL SECURITY;
ALTER TABLE external_purchases FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_external_purchases_tenant ON external_purchases;
CREATE POLICY p_external_purchases_tenant ON external_purchases
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_conversations_tenant ON conversations;
CREATE POLICY p_conversations_tenant ON conversations
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_messages_tenant ON messages;
CREATE POLICY p_messages_tenant ON messages
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE message_attachments ENABLE ROW LEVEL SECURITY;
ALTER TABLE message_attachments FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_message_attachments_tenant ON message_attachments;
CREATE POLICY p_message_attachments_tenant ON message_attachments
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE conversation_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_logs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_conversation_logs_tenant ON conversation_logs;
CREATE POLICY p_conversation_logs_tenant ON conversation_logs
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE chat_histories ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_histories FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_chat_histories_tenant ON chat_histories;
CREATE POLICY p_chat_histories_tenant ON chat_histories
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE tickets FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_tickets_tenant ON tickets;
CREATE POLICY p_tickets_tenant ON tickets
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE ticket_notes ENABLE ROW LEVEL SECURITY;
ALTER TABLE ticket_notes FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_ticket_notes_tenant ON ticket_notes;
CREATE POLICY p_ticket_notes_tenant ON ticket_notes
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE knowledge_base ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_base FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_knowledge_base_tenant ON knowledge_base;
CREATE POLICY p_knowledge_base_tenant ON knowledge_base
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE company_products ENABLE ROW LEVEL SECURITY;
ALTER TABLE company_products FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_company_products_tenant ON company_products;
CREATE POLICY p_company_products_tenant ON company_products
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE company_faqs ENABLE ROW LEVEL SECURITY;
ALTER TABLE company_faqs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_company_faqs_tenant ON company_faqs;
CREATE POLICY p_company_faqs_tenant ON company_faqs
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE onboarding_docs ENABLE ROW LEVEL SECURITY;
ALTER TABLE onboarding_docs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_onboarding_docs_tenant ON onboarding_docs;
CREATE POLICY p_onboarding_docs_tenant ON onboarding_docs
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE ai_agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_agents FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_ai_agents_tenant ON ai_agents;
CREATE POLICY p_ai_agents_tenant ON ai_agents
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE ai_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_sessions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_ai_sessions_tenant ON ai_sessions;
CREATE POLICY p_ai_sessions_tenant ON ai_sessions
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE training_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE training_data FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_training_data_tenant ON training_data;
CREATE POLICY p_training_data_tenant ON training_data
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE context_memories ENABLE ROW LEVEL SECURITY;
ALTER TABLE context_memories FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_context_memories_tenant ON context_memories;
CREATE POLICY p_context_memories_tenant ON context_memories
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE embeddings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_embeddings_tenant ON embeddings;
CREATE POLICY p_embeddings_tenant ON embeddings
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE embedding_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE embedding_jobs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_embedding_jobs_tenant ON embedding_jobs;
CREATE POLICY p_embedding_jobs_tenant ON embedding_jobs
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE sentiment_analyses ENABLE ROW LEVEL SECURITY;
ALTER TABLE sentiment_analyses FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_sentiment_analyses_tenant ON sentiment_analyses;
CREATE POLICY p_sentiment_analyses_tenant ON sentiment_analyses
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE user_ai_memories ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_ai_memories FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_user_ai_memories_tenant ON user_ai_memories;
CREATE POLICY p_user_ai_memories_tenant ON user_ai_memories
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE mcp_clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE mcp_clients FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_mcp_clients_tenant ON mcp_clients;
CREATE POLICY p_mcp_clients_tenant ON mcp_clients
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE webhook_handlers ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_handlers FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_webhook_handlers_tenant ON webhook_handlers;
CREATE POLICY p_webhook_handlers_tenant ON webhook_handlers
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE webhook_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_webhook_events_tenant ON webhook_events;
CREATE POLICY p_webhook_events_tenant ON webhook_events
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE social_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE social_accounts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_social_accounts_tenant ON social_accounts;
CREATE POLICY p_social_accounts_tenant ON social_accounts
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE social_posts ENABLE ROW LEVEL SECURITY;
ALTER TABLE social_posts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_social_posts_tenant ON social_posts;
CREATE POLICY p_social_posts_tenant ON social_posts
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE analytics_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics_reports FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_analytics_reports_tenant ON analytics_reports;
CREATE POLICY p_analytics_reports_tenant ON analytics_reports
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE analytics_report_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics_report_data FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_analytics_report_data_tenant ON analytics_report_data;
CREATE POLICY p_analytics_report_data_tenant ON analytics_report_data
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE metrics FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_metrics_tenant ON metrics;
CREATE POLICY p_metrics_tenant ON metrics
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE customer_interaction_summaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_interaction_summaries FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_customer_interaction_summaries_tenant ON customer_interaction_summaries;
CREATE POLICY p_customer_interaction_summaries_tenant ON customer_interaction_summaries
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE daily_summaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_summaries FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_daily_summaries_tenant ON daily_summaries;
CREATE POLICY p_daily_summaries_tenant ON daily_summaries
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE raw_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_raw_events_tenant ON raw_events;
CREATE POLICY p_raw_events_tenant ON raw_events
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE raw_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw_messages FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_raw_messages_tenant ON raw_messages;
CREATE POLICY p_raw_messages_tenant ON raw_messages
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE raw_leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw_leads FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_raw_leads_tenant ON raw_leads;
CREATE POLICY p_raw_leads_tenant ON raw_leads
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE analytics_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_analytics_events_tenant ON analytics_events;
CREATE POLICY p_analytics_events_tenant ON analytics_events
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE lead_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE lead_metrics FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_lead_metrics_tenant ON lead_metrics;
CREATE POLICY p_lead_metrics_tenant ON lead_metrics
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE conversation_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_metrics FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_conversation_metrics_tenant ON conversation_metrics;
CREATE POLICY p_conversation_metrics_tenant ON conversation_metrics
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE sentiment_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE sentiment_logs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_sentiment_logs_tenant ON sentiment_logs;
CREATE POLICY p_sentiment_logs_tenant ON sentiment_logs
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_notifications_tenant ON notifications;
CREATE POLICY p_notifications_tenant ON notifications
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE notification_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_settings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_notification_settings_tenant ON notification_settings;
CREATE POLICY p_notification_settings_tenant ON notification_settings
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE journey_tracking ENABLE ROW LEVEL SECURITY;
ALTER TABLE journey_tracking FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_journey_tracking_tenant ON journey_tracking;
CREATE POLICY p_journey_tracking_tenant ON journey_tracking
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE journey_metadata ENABLE ROW LEVEL SECURITY;
ALTER TABLE journey_metadata FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_journey_metadata_tenant ON journey_metadata;
CREATE POLICY p_journey_metadata_tenant ON journey_metadata
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE system_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE system_logs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_system_logs_tenant ON system_logs;
CREATE POLICY p_system_logs_tenant ON system_logs
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE system_log_metadata ENABLE ROW LEVEL SECURITY;
ALTER TABLE system_log_metadata FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_system_log_metadata_tenant ON system_log_metadata;
CREATE POLICY p_system_log_metadata_tenant ON system_log_metadata
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE unified_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE unified_profiles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_unified_profiles_tenant ON unified_profiles;
CREATE POLICY p_unified_profiles_tenant ON unified_profiles
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE unified_profile_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE unified_profile_members FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_unified_profile_members_tenant ON unified_profile_members;
CREATE POLICY p_unified_profile_members_tenant ON unified_profile_members
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE unification_merge_suggestions ENABLE ROW LEVEL SECURITY;
ALTER TABLE unification_merge_suggestions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_unification_merge_suggestions_tenant ON unification_merge_suggestions;
CREATE POLICY p_unification_merge_suggestions_tenant ON unification_merge_suggestions
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE consent_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE consent_ledger FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_consent_ledger_tenant ON consent_ledger;
CREATE POLICY p_consent_ledger_tenant ON consent_ledger
USING (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)))
WITH CHECK (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)));

ALTER TABLE unified_customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE unified_customers FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_unified_customers_tenant ON unified_customers;
CREATE POLICY p_unified_customers_tenant ON unified_customers
USING (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)))
WITH CHECK (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)));

ALTER TABLE identity_mappings ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity_mappings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_identity_mappings_tenant ON identity_mappings;
CREATE POLICY p_identity_mappings_tenant ON identity_mappings
USING (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)))
WITH CHECK (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)));

ALTER TABLE device_fingerprints ENABLE ROW LEVEL SECURITY;
ALTER TABLE device_fingerprints FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_device_fingerprints_tenant ON device_fingerprints;
CREATE POLICY p_device_fingerprints_tenant ON device_fingerprints
USING (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)))
WITH CHECK (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)));

ALTER TABLE resolution_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE resolution_audit_log FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_resolution_audit_log_tenant ON resolution_audit_log;
CREATE POLICY p_resolution_audit_log_tenant ON resolution_audit_log
USING (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)))
WITH CHECK (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)));

ALTER TABLE profile_merge_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE profile_merge_history FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_profile_merge_history_tenant ON profile_merge_history;
CREATE POLICY p_profile_merge_history_tenant ON profile_merge_history
USING (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)))
WITH CHECK (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)));

ALTER TABLE review_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_queue FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_review_queue_tenant ON review_queue;
CREATE POLICY p_review_queue_tenant ON review_queue
USING (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)))
WITH CHECK (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)));

ALTER TABLE identity_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity_history FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_identity_history_tenant ON identity_history;
CREATE POLICY p_identity_history_tenant ON identity_history
USING (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)))
WITH CHECK (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)));

ALTER TABLE consent_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE consent_records FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_consent_records_tenant ON consent_records;
CREATE POLICY p_consent_records_tenant ON consent_records
USING (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)))
WITH CHECK (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)));

ALTER TABLE identity_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_identity_events_tenant ON identity_events;
CREATE POLICY p_identity_events_tenant ON identity_events
USING (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)))
WITH CHECK (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)));

ALTER TABLE event_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE event_outbox FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_event_outbox_tenant ON event_outbox;
CREATE POLICY p_event_outbox_tenant ON event_outbox
USING (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)))
WITH CHECK (tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant', true), ''), current_setting('app.current_company', true)));

ALTER TABLE customer_tags ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_tags FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_customer_tags_tenant ON customer_tags;
CREATE POLICY p_customer_tags_tenant ON customer_tags
USING (EXISTS (SELECT 1 FROM customers c WHERE c.id = customer_tags.customer_id AND c.company_id = current_setting('app.current_company', true)))
WITH CHECK (EXISTS (SELECT 1 FROM customers c WHERE c.id = customer_tags.customer_id AND c.company_id = current_setting('app.current_company', true)));

ALTER TABLE customer_channels ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_channels FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_customer_channels_tenant ON customer_channels;
CREATE POLICY p_customer_channels_tenant ON customer_channels
USING (EXISTS (SELECT 1 FROM customers c WHERE c.id = customer_channels.customer_id AND c.company_id = current_setting('app.current_company', true)))
WITH CHECK (EXISTS (SELECT 1 FROM customers c WHERE c.id = customer_channels.customer_id AND c.company_id = current_setting('app.current_company', true)));

ALTER TABLE customer_social_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_social_profiles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_customer_social_profiles_tenant ON customer_social_profiles;
CREATE POLICY p_customer_social_profiles_tenant ON customer_social_profiles
USING (EXISTS (SELECT 1 FROM customers c WHERE c.id = customer_social_profiles.customer_id AND c.company_id = current_setting('app.current_company', true)))
WITH CHECK (EXISTS (SELECT 1 FROM customers c WHERE c.id = customer_social_profiles.customer_id AND c.company_id = current_setting('app.current_company', true)));

ALTER TABLE customer_profile_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_profile_preferences FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_customer_profile_preferences_tenant ON customer_profile_preferences;
CREATE POLICY p_customer_profile_preferences_tenant ON customer_profile_preferences
USING (EXISTS (SELECT 1 FROM customers c WHERE c.id = customer_profile_preferences.customer_id AND c.company_id = current_setting('app.current_company', true)))
WITH CHECK (EXISTS (SELECT 1 FROM customers c WHERE c.id = customer_profile_preferences.customer_id AND c.company_id = current_setting('app.current_company', true)));

ALTER TABLE lead_channels ENABLE ROW LEVEL SECURITY;
ALTER TABLE lead_channels FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_lead_channels_tenant ON lead_channels;
CREATE POLICY p_lead_channels_tenant ON lead_channels
USING (EXISTS (SELECT 1 FROM leads l WHERE l.id = lead_channels.lead_id AND l.company_id = current_setting('app.current_company', true)))
WITH CHECK (EXISTS (SELECT 1 FROM leads l WHERE l.id = lead_channels.lead_id AND l.company_id = current_setting('app.current_company', true)));

ALTER TABLE conversation_tags ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_tags FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_conversation_tags_tenant ON conversation_tags;
CREATE POLICY p_conversation_tags_tenant ON conversation_tags
USING (EXISTS (SELECT 1 FROM conversations c WHERE c.id = conversation_tags.conversation_id AND c.company_id = current_setting('app.current_company', true)))
WITH CHECK (EXISTS (SELECT 1 FROM conversations c WHERE c.id = conversation_tags.conversation_id AND c.company_id = current_setting('app.current_company', true)));

ALTER TABLE knowledge_base_tags ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_base_tags FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_knowledge_base_tags_tenant ON knowledge_base_tags;
CREATE POLICY p_knowledge_base_tags_tenant ON knowledge_base_tags
USING (EXISTS (SELECT 1 FROM knowledge_base kb WHERE kb.id = knowledge_base_tags.kb_id AND kb.company_id = current_setting('app.current_company', true)))
WITH CHECK (EXISTS (SELECT 1 FROM knowledge_base kb WHERE kb.id = knowledge_base_tags.kb_id AND kb.company_id = current_setting('app.current_company', true)));

ALTER TABLE product_images ENABLE ROW LEVEL SECURITY;
ALTER TABLE product_images FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_product_images_tenant ON product_images;
CREATE POLICY p_product_images_tenant ON product_images
USING (EXISTS (SELECT 1 FROM company_products p WHERE p.id = product_images.product_id AND p.company_id = current_setting('app.current_company', true)))
WITH CHECK (EXISTS (SELECT 1 FROM company_products p WHERE p.id = product_images.product_id AND p.company_id = current_setting('app.current_company', true)));

ALTER TABLE product_features ENABLE ROW LEVEL SECURITY;
ALTER TABLE product_features FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_product_features_tenant ON product_features;
CREATE POLICY p_product_features_tenant ON product_features
USING (EXISTS (SELECT 1 FROM company_products p WHERE p.id = product_features.product_id AND p.company_id = current_setting('app.current_company', true)))
WITH CHECK (EXISTS (SELECT 1 FROM company_products p WHERE p.id = product_features.product_id AND p.company_id = current_setting('app.current_company', true)));

ALTER TABLE onboarding_doc_tags ENABLE ROW LEVEL SECURITY;
ALTER TABLE onboarding_doc_tags FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_onboarding_doc_tags_tenant ON onboarding_doc_tags;
CREATE POLICY p_onboarding_doc_tags_tenant ON onboarding_doc_tags
USING (EXISTS (SELECT 1 FROM onboarding_docs d WHERE d.id = onboarding_doc_tags.doc_id AND d.company_id = current_setting('app.current_company', true)))
WITH CHECK (EXISTS (SELECT 1 FROM onboarding_docs d WHERE d.id = onboarding_doc_tags.doc_id AND d.company_id = current_setting('app.current_company', true)));

ALTER TABLE ai_agent_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_agent_config FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_ai_agent_config_tenant ON ai_agent_config;
CREATE POLICY p_ai_agent_config_tenant ON ai_agent_config
USING (EXISTS (SELECT 1 FROM ai_agents a WHERE a.id = ai_agent_config.agent_id AND a.company_id = current_setting('app.current_company', true)))
WITH CHECK (EXISTS (SELECT 1 FROM ai_agents a WHERE a.id = ai_agent_config.agent_id AND a.company_id = current_setting('app.current_company', true)));

ALTER TABLE mcp_client_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE mcp_client_config FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_mcp_client_config_tenant ON mcp_client_config;
CREATE POLICY p_mcp_client_config_tenant ON mcp_client_config
USING (EXISTS (SELECT 1 FROM mcp_clients c WHERE c.id = mcp_client_config.client_id AND c.company_id = current_setting('app.current_company', true)))
WITH CHECK (EXISTS (SELECT 1 FROM mcp_clients c WHERE c.id = mcp_client_config.client_id AND c.company_id = current_setting('app.current_company', true)));

ALTER TABLE users DROP CONSTRAINT IF EXISTS uq_users_email;
ALTER TABLE users DROP CONSTRAINT IF EXISTS uq_users_company_email;
ALTER TABLE users ADD CONSTRAINT uq_users_company_email UNIQUE (company_id, email);

ALTER TABLE channel_settings ADD COLUMN IF NOT EXISTS phone_number TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_channel_settings_phone_number ON channel_settings(phone_number);
CREATE INDEX IF NOT EXISTS idx_channel_settings_page_id ON channel_settings(page_id);
CREATE INDEX IF NOT EXISTS idx_channel_settings_phone_number_id ON channel_settings(phone_number_id);

ALTER TABLE llm_engines ADD COLUMN IF NOT EXISTS company_id TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_engines_company_provider_model_version
    ON llm_engines(company_id, provider, model_name, version);
CREATE INDEX IF NOT EXISTS idx_llm_engines_company_active
    ON llm_engines(company_id, is_active, provider, model_name);

ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS company_id TEXT NOT NULL DEFAULT '';
ALTER TABLE mcp_servers DROP CONSTRAINT IF EXISTS uq_mcp_servers_endpoint;
CREATE UNIQUE INDEX IF NOT EXISTS uq_mcp_servers_company_endpoint
    ON mcp_servers(company_id, endpoint);
CREATE INDEX IF NOT EXISTS idx_mcp_servers_company_status
    ON mcp_servers(company_id, status, last_heartbeat DESC);

CREATE TABLE IF NOT EXISTS tenant_meta_config (
    id                     TEXT PRIMARY KEY,
    company_id             TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    channel                TEXT NOT NULL DEFAULT 'whatsapp',
    config_name            TEXT NOT NULL DEFAULT 'default',
    provider_mode          TEXT NOT NULL DEFAULT 'cloud_api',
    api_version            TEXT NOT NULL DEFAULT 'v21.0',
    app_id                 TEXT NOT NULL DEFAULT '',
    app_secret_enc         TEXT NOT NULL DEFAULT '',
    access_token_enc       TEXT NOT NULL DEFAULT '',
    access_token_last4     TEXT NOT NULL DEFAULT '',
    verify_token           TEXT NOT NULL DEFAULT '',
    webhook_secret_enc     TEXT NOT NULL DEFAULT '',
    phone_number_id        TEXT NOT NULL DEFAULT '',
    business_account_id    TEXT NOT NULL DEFAULT '',
    system_user_id         TEXT NOT NULL DEFAULT '',
    catalog_id             TEXT NOT NULL DEFAULT '',
    credit_limit_per_day   INTEGER NOT NULL DEFAULT 1000,
    requests_per_minute    INTEGER NOT NULL DEFAULT 120,
    is_default             BOOLEAN NOT NULL DEFAULT FALSE,
    is_active              BOOLEAN NOT NULL DEFAULT TRUE,
    last_validated_at      TIMESTAMPTZ,
    last_error             TEXT NOT NULL DEFAULT '',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tenant_meta_config_company_channel_name UNIQUE (company_id, channel, config_name)
);
CREATE INDEX IF NOT EXISTS idx_tenant_meta_config_company_channel
    ON tenant_meta_config(company_id, channel, is_active);

CREATE TABLE IF NOT EXISTS whatsapp_channels (
    id                     TEXT PRIMARY KEY,
    company_id             TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    meta_config_id         TEXT REFERENCES tenant_meta_config(id) ON DELETE SET NULL,
    channel_name           TEXT NOT NULL DEFAULT '',
    phone_number_id        TEXT NOT NULL,
    display_phone_number   TEXT NOT NULL DEFAULT '',
    business_account_id    TEXT NOT NULL DEFAULT '',
    verified_name          TEXT NOT NULL DEFAULT '',
    quality_rating         TEXT NOT NULL DEFAULT '',
    throughput_tier        TEXT NOT NULL DEFAULT '',
    is_default             BOOLEAN NOT NULL DEFAULT FALSE,
    is_active              BOOLEAN NOT NULL DEFAULT TRUE,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_whatsapp_channels_company_phone UNIQUE (company_id, phone_number_id)
);
CREATE INDEX IF NOT EXISTS idx_whatsapp_channels_company_active
    ON whatsapp_channels(company_id, is_active, phone_number_id);

CREATE TABLE IF NOT EXISTS meta_message_templates (
    id                     TEXT PRIMARY KEY,
    company_id             TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    meta_config_id         TEXT REFERENCES tenant_meta_config(id) ON DELETE SET NULL,
    external_template_id   TEXT NOT NULL DEFAULT '',
    template_name          TEXT NOT NULL,
    language               TEXT NOT NULL DEFAULT 'en_US',
    category               TEXT NOT NULL DEFAULT 'MARKETING',
    template_status        TEXT NOT NULL DEFAULT 'PENDING',
    rejection_reason       TEXT NOT NULL DEFAULT '',
    components_json        TEXT NOT NULL DEFAULT '{}',
    last_synced_at         TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_meta_templates_company_name_lang UNIQUE (company_id, template_name, language)
);
CREATE INDEX IF NOT EXISTS idx_meta_message_templates_company_status
    ON meta_message_templates(company_id, template_status, updated_at DESC);

CREATE TABLE IF NOT EXISTS meta_api_usage (
    id                     TEXT PRIMARY KEY,
    company_id             TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    meta_config_id         TEXT REFERENCES tenant_meta_config(id) ON DELETE SET NULL,
    channel                TEXT NOT NULL DEFAULT 'whatsapp',
    endpoint               TEXT NOT NULL DEFAULT '',
    metric_name            TEXT NOT NULL DEFAULT 'api_call',
    request_count          INTEGER NOT NULL DEFAULT 1,
    unit_count             INTEGER NOT NULL DEFAULT 1,
    status_code            INTEGER NOT NULL DEFAULT 0,
    error_code             TEXT NOT NULL DEFAULT '',
    billable_units         INTEGER NOT NULL DEFAULT 0,
    recorded_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_meta_api_usage_company_recorded_at
    ON meta_api_usage(company_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_meta_api_usage_company_endpoint
    ON meta_api_usage(company_id, channel, endpoint, recorded_at DESC);

CREATE TABLE IF NOT EXISTS billing_customers (
    id                     TEXT PRIMARY KEY,
    company_id             TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    stripe_customer_id     TEXT,
    billing_email          TEXT NOT NULL DEFAULT '',
    billing_name           TEXT NOT NULL DEFAULT '',
    country_code           TEXT NOT NULL DEFAULT '',
    tax_id                 TEXT NOT NULL DEFAULT '',
    payment_status         TEXT NOT NULL DEFAULT 'inactive',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_billing_customers_company UNIQUE (company_id)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id                     TEXT PRIMARY KEY,
    company_id             TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    billing_customer_id    TEXT REFERENCES billing_customers(id) ON DELETE SET NULL,
    stripe_subscription_id TEXT,
    plan_code              TEXT NOT NULL DEFAULT 'free',
    status                 TEXT NOT NULL DEFAULT 'trialing',
    billing_interval       TEXT NOT NULL DEFAULT 'month',
    ai_credit_limit               INTEGER NOT NULL DEFAULT 0,
    monthly_conversation_limit    INTEGER NOT NULL DEFAULT 0,
    max_users                     INTEGER NOT NULL DEFAULT 0,
    overage_cents          INTEGER NOT NULL DEFAULT 0,
    current_period_start   TIMESTAMPTZ,
    current_period_end     TIMESTAMPTZ,
    canceled_at            TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_subscriptions_company UNIQUE (company_id)
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_plan_status
    ON subscriptions(plan_code, status, current_period_end);

ALTER TABLE billing_customers DROP CONSTRAINT IF EXISTS uq_billing_customers_stripe;
ALTER TABLE billing_customers ALTER COLUMN stripe_customer_id DROP NOT NULL;
UPDATE billing_customers SET stripe_customer_id=NULL WHERE stripe_customer_id IS NOT NULL AND BTRIM(stripe_customer_id) = '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_billing_customers_stripe_nonempty
    ON billing_customers(stripe_customer_id)
    WHERE stripe_customer_id IS NOT NULL AND BTRIM(stripe_customer_id) <> '';

ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS uq_subscriptions_stripe;
ALTER TABLE subscriptions ALTER COLUMN stripe_subscription_id DROP NOT NULL;
UPDATE subscriptions SET stripe_subscription_id=NULL WHERE stripe_subscription_id IS NOT NULL AND BTRIM(stripe_subscription_id) = '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_subscriptions_stripe_nonempty
    ON subscriptions(stripe_subscription_id)
    WHERE stripe_subscription_id IS NOT NULL AND BTRIM(stripe_subscription_id) <> '';

CREATE TABLE IF NOT EXISTS usage_ledger (
    id                     TEXT PRIMARY KEY,
    company_id             TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE CHECK (BTRIM(company_id) <> ''),
    subscription_id        TEXT REFERENCES subscriptions(id) ON DELETE SET NULL,
    usage_type             TEXT NOT NULL DEFAULT 'ai_tokens',
    usage_units            NUMERIC(18,4) NOT NULL DEFAULT 0,
    usage_window           TEXT NOT NULL DEFAULT 'monthly',
    reference_id           TEXT NOT NULL DEFAULT '',
    metadata               TEXT NOT NULL DEFAULT '{}',
    usage_idempotency_key  TEXT NOT NULL DEFAULT '',
    occurred_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE usage_ledger
    ADD COLUMN IF NOT EXISTS usage_idempotency_key TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_usage_ledger_company_type_time
    ON usage_ledger(company_id, usage_type, occurred_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_usage_ledger_company_conv_idem
    ON usage_ledger(company_id, usage_idempotency_key)
    WHERE usage_type = 'conversation_message' AND BTRIM(usage_idempotency_key) <> '';

CREATE TABLE IF NOT EXISTS stripe_webhook_events (
    id                     TEXT PRIMARY KEY,
    company_id             TEXT NOT NULL DEFAULT '',
    stripe_event_id        TEXT NOT NULL,
    event_type             TEXT NOT NULL,
    payload                TEXT NOT NULL DEFAULT '{}',
    processed              BOOLEAN NOT NULL DEFAULT FALSE,
    processed_at           TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_stripe_webhook_events_event UNIQUE (stripe_event_id)
);
CREATE INDEX IF NOT EXISTS idx_stripe_webhook_events_processed
    ON stripe_webhook_events(processed, created_at DESC);

-- Admin billing override history (for audit trail + rollback)
CREATE TABLE IF NOT EXISTS admin_billing_overrides (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    previous_state  JSONB NOT NULL DEFAULT '{}',
    new_state       JSONB NOT NULL DEFAULT '{}',
    reason          TEXT NOT NULL DEFAULT '',
    changed_by      TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_admin_billing_overrides_company
    ON admin_billing_overrides(company_id, created_at DESC);

ALTER TABLE llm_engines ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm_engines FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_llm_engines_tenant ON llm_engines;
CREATE POLICY p_llm_engines_tenant ON llm_engines
USING (
    COALESCE(NULLIF(current_setting('app.current_company', true), ''), '__all__') = '__all__'
    OR company_id = ''
    OR company_id = current_setting('app.current_company', true)
)
WITH CHECK (
    COALESCE(NULLIF(current_setting('app.current_company', true), ''), '__all__') = '__all__'
    OR company_id = ''
    OR company_id = current_setting('app.current_company', true)
);

ALTER TABLE mcp_servers ENABLE ROW LEVEL SECURITY;
ALTER TABLE mcp_servers FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_mcp_servers_tenant ON mcp_servers;
CREATE POLICY p_mcp_servers_tenant ON mcp_servers
USING (
    COALESCE(NULLIF(current_setting('app.current_company', true), ''), '__all__') = '__all__'
    OR company_id = ''
    OR company_id = current_setting('app.current_company', true)
)
WITH CHECK (
    COALESCE(NULLIF(current_setting('app.current_company', true), ''), '__all__') = '__all__'
    OR company_id = ''
    OR company_id = current_setting('app.current_company', true)
);

ALTER TABLE tenant_meta_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_meta_config FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_tenant_meta_config_tenant ON tenant_meta_config;
CREATE POLICY p_tenant_meta_config_tenant ON tenant_meta_config
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE whatsapp_channels ENABLE ROW LEVEL SECURITY;
ALTER TABLE whatsapp_channels FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_whatsapp_channels_tenant ON whatsapp_channels;
CREATE POLICY p_whatsapp_channels_tenant ON whatsapp_channels
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE meta_message_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE meta_message_templates FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_meta_message_templates_tenant ON meta_message_templates;
CREATE POLICY p_meta_message_templates_tenant ON meta_message_templates
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE meta_api_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE meta_api_usage FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_meta_api_usage_tenant ON meta_api_usage;
CREATE POLICY p_meta_api_usage_tenant ON meta_api_usage
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE billing_customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_customers FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_billing_customers_tenant ON billing_customers;
CREATE POLICY p_billing_customers_tenant ON billing_customers
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscriptions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_subscriptions_tenant ON subscriptions;
CREATE POLICY p_subscriptions_tenant ON subscriptions
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE usage_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_ledger FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_usage_ledger_tenant ON usage_ledger;
CREATE POLICY p_usage_ledger_tenant ON usage_ledger
USING (company_id = current_setting('app.current_company', true))
WITH CHECK (company_id = current_setting('app.current_company', true));

ALTER TABLE stripe_webhook_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE stripe_webhook_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_stripe_webhook_events_tenant ON stripe_webhook_events;
CREATE POLICY p_stripe_webhook_events_tenant ON stripe_webhook_events
USING (
    company_id = ''
    OR company_id = current_setting('app.current_company', true)
)
WITH CHECK (
    company_id = ''
    OR company_id = current_setting('app.current_company', true)
);

-- ── RLS gap fixes ────────────────────────────────────────────────────────────
-- Tables that were missing tenant-scoped row level security.

-- dead_letter_queue: has company_id; allow tenant access plus platform admin bypass.
ALTER TABLE dead_letter_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE dead_letter_queue FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_dead_letter_queue_tenant ON dead_letter_queue;
CREATE POLICY p_dead_letter_queue_tenant ON dead_letter_queue
USING (
    company_id = current_setting('app.current_company', true)
    OR company_id = ''
    OR current_setting('app.platform_admin_mode', true) = 'on'
)
WITH CHECK (
    company_id = current_setting('app.current_company', true)
    OR company_id = ''
    OR current_setting('app.platform_admin_mode', true) = 'on'
);

-- unprocessed_events: no direct company_id; restrict to platform admin and system roles.
ALTER TABLE unprocessed_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE unprocessed_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_unprocessed_events_platform ON unprocessed_events;
CREATE POLICY p_unprocessed_events_platform ON unprocessed_events
USING (
    current_setting('app.platform_admin_mode', true) = 'on'
    OR (
        resolved_company_id <> ''
        AND resolved_company_id = current_setting('app.current_company', true)
    )
)
WITH CHECK (
    current_setting('app.platform_admin_mode', true) = 'on'
);

-- mcp_server_capabilities: no direct company_id; restrict via parent mcp_servers.company_id.
ALTER TABLE mcp_server_capabilities ENABLE ROW LEVEL SECURITY;
ALTER TABLE mcp_server_capabilities FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_mcp_server_capabilities_tenant ON mcp_server_capabilities;
CREATE POLICY p_mcp_server_capabilities_tenant ON mcp_server_capabilities
USING (
    server_id IN (
        SELECT id FROM mcp_servers
        WHERE company_id = current_setting('app.current_company', true)
    )
    OR current_setting('app.platform_admin_mode', true) = 'on'
)
WITH CHECK (
    server_id IN (
        SELECT id FROM mcp_servers
        WHERE company_id = current_setting('app.current_company', true)
    )
    OR current_setting('app.platform_admin_mode', true) = 'on'
);

-- deleted_companies: platform admin only.
ALTER TABLE deleted_companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE deleted_companies FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_deleted_companies_platform ON deleted_companies;
CREATE POLICY p_deleted_companies_platform ON deleted_companies
USING (current_setting('app.platform_admin_mode', true) = 'on')
WITH CHECK (current_setting('app.platform_admin_mode', true) = 'on');

-- roles: global table; readable by all authenticated users, writable by platform admin only.
ALTER TABLE roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE roles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_roles_read ON roles;
CREATE POLICY p_roles_read ON roles FOR SELECT
USING (TRUE);
DROP POLICY IF EXISTS p_roles_write ON roles;
CREATE POLICY p_roles_write ON roles FOR ALL
USING (current_setting('app.platform_admin_mode', true) = 'on')
WITH CHECK (current_setting('app.platform_admin_mode', true) = 'on');

-- oauth_states: no company_id; allow all during auth flows, platform admin for maintenance.
ALTER TABLE oauth_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE oauth_states FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_oauth_states_auth ON oauth_states;
CREATE POLICY p_oauth_states_auth ON oauth_states
USING (
    current_setting('app.public_auth_mode', true) = 'on'
    OR current_setting('app.platform_admin_mode', true) = 'on'
)
WITH CHECK (
    current_setting('app.public_auth_mode', true) = 'on'
    OR current_setting('app.platform_admin_mode', true) = 'on'
);

-- =============================================================================
-- Email Campaigns (bulk outbound email to filtered leads/customers).
-- =============================================================================
CREATE TABLE IF NOT EXISTS email_campaigns (
    id             TEXT PRIMARY KEY,
    company_id     TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE
                   CHECK (BTRIM(company_id) <> ''),
    name           TEXT NOT NULL,
    subject        TEXT NOT NULL,
    body           TEXT NOT NULL DEFAULT '',
    html_body      TEXT NOT NULL DEFAULT '',
    filters        JSONB NOT NULL DEFAULT '{}'::jsonb,
    status         TEXT NOT NULL DEFAULT 'draft'
                   CHECK (status IN ('draft','queued','sending','completed','failed','cancelled')),
    total_recipients INTEGER NOT NULL DEFAULT 0,
    sent_count     INTEGER NOT NULL DEFAULT 0,
    failed_count   INTEGER NOT NULL DEFAULT 0,
    created_by     TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at     TIMESTAMPTZ,
    completed_at   TIMESTAMPTZ,
    last_error     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_email_campaigns_company_id
    ON email_campaigns(company_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_email_campaigns_status
    ON email_campaigns(company_id, status);

CREATE TABLE IF NOT EXISTS email_campaign_recipients (
    id             TEXT PRIMARY KEY,
    campaign_id    TEXT NOT NULL REFERENCES email_campaigns(id) ON DELETE CASCADE,
    company_id     TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE
                   CHECK (BTRIM(company_id) <> ''),
    customer_id    TEXT NOT NULL DEFAULT '',
    lead_id        TEXT NOT NULL DEFAULT '',
    email          TEXT NOT NULL,
    name           TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','sent','failed','skipped')),
    error          TEXT NOT NULL DEFAULT '',
    sent_at        TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_email_campaign_recipients_campaign
    ON email_campaign_recipients(campaign_id, status);
CREATE INDEX IF NOT EXISTS idx_email_campaign_recipients_company
    ON email_campaign_recipients(company_id);

ALTER TABLE email_campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_campaigns FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_email_campaigns_tenant ON email_campaigns;
CREATE POLICY p_email_campaigns_tenant ON email_campaigns
USING (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
)
WITH CHECK (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
);

ALTER TABLE email_campaign_recipients ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_campaign_recipients FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_email_campaign_recipients_tenant ON email_campaign_recipients;
CREATE POLICY p_email_campaign_recipients_tenant ON email_campaign_recipients
USING (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
)
WITH CHECK (
    company_id = current_setting('app.current_company', true)
    OR current_setting('app.platform_admin_mode', true) = 'on'
);

-- Optional generic lead_tags table for tagging social_lead / hot_lead / interested.
-- customer_tags already exists (see above). This mirror table keeps lead-scoped tags.
CREATE TABLE IF NOT EXISTS lead_tags (
    lead_id     TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    tag         TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (lead_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_lead_tags_tag ON lead_tags(tag);
