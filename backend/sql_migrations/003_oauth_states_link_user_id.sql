-- OAuth "link to existing account" flow: bind state row to authenticated user id.
ALTER TABLE oauth_states ADD COLUMN IF NOT EXISTS link_user_id TEXT;
