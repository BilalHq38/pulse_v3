CREATE TABLE IF NOT EXISTS visitor_sessions (
    id               TEXT PRIMARY KEY,
    company_id       TEXT NOT NULL DEFAULT '',
    user_id          TEXT NOT NULL DEFAULT '',
    first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    page_url         TEXT NOT NULL DEFAULT '',
    referrer         TEXT NOT NULL DEFAULT '',
    landing_path     TEXT NOT NULL DEFAULT '',
    user_agent       TEXT NOT NULL DEFAULT '',
    ip_hash          TEXT NOT NULL DEFAULT '',
    metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_visitor_sessions_company_seen
    ON visitor_sessions(company_id, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_visitor_sessions_user_id
    ON visitor_sessions(user_id);

CREATE TABLE IF NOT EXISTS visitor_events (
    id               TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL REFERENCES visitor_sessions(id) ON DELETE CASCADE,
    company_id       TEXT NOT NULL DEFAULT '',
    user_id          TEXT NOT NULL DEFAULT '',
    event_type       TEXT NOT NULL DEFAULT 'page_view',
    page_url         TEXT NOT NULL DEFAULT '',
    path             TEXT NOT NULL DEFAULT '',
    referrer         TEXT NOT NULL DEFAULT '',
    element          TEXT NOT NULL DEFAULT '',
    metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_visitor_events_session_time
    ON visitor_events(session_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_visitor_events_company_time
    ON visitor_events(company_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_visitor_events_type_time
    ON visitor_events(event_type, occurred_at DESC);
