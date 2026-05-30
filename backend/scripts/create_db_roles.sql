-- Run this script as a PostgreSQL superuser ONCE before first production deployment.
-- Creates a least-privilege application role without BYPASSRLS or superuser.

-- Create the application role (no superuser, no BYPASSRLS, no CREATEDB)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pulse_app') THEN
        CREATE ROLE pulse_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
END
$$;

-- Set password (change this in production — use Secrets Manager)
-- ALTER ROLE pulse_app WITH PASSWORD '<your-secure-password>';

-- Grant connect on database
GRANT CONNECT ON DATABASE pulse_engine TO pulse_app;

-- Ensure service schemas exist before grants. Alembic owns table creation.
CREATE SCHEMA IF NOT EXISTS auth_service;
CREATE SCHEMA IF NOT EXISTS user_service;
CREATE SCHEMA IF NOT EXISTS customer_service;
CREATE SCHEMA IF NOT EXISTS lead_service;
CREATE SCHEMA IF NOT EXISTS email_campaign_service;
CREATE SCHEMA IF NOT EXISTS ai_service;
CREATE SCHEMA IF NOT EXISTS agent_orchestrator;
CREATE SCHEMA IF NOT EXISTS product_service;
CREATE SCHEMA IF NOT EXISTS analytics_service;
CREATE SCHEMA IF NOT EXISTS data_pipeline_service;
CREATE SCHEMA IF NOT EXISTS notification_service;
CREATE SCHEMA IF NOT EXISTS super_admin_service;
CREATE SCHEMA IF NOT EXISTS identity_service;

-- Grant usage on schemas
GRANT USAGE ON SCHEMA public TO pulse_app;
GRANT USAGE ON SCHEMA auth_service TO pulse_app;
GRANT USAGE ON SCHEMA user_service TO pulse_app;
GRANT USAGE ON SCHEMA customer_service TO pulse_app;
GRANT USAGE ON SCHEMA lead_service TO pulse_app;
GRANT USAGE ON SCHEMA email_campaign_service TO pulse_app;
GRANT USAGE ON SCHEMA ai_service TO pulse_app;
GRANT USAGE ON SCHEMA agent_orchestrator TO pulse_app;
GRANT USAGE ON SCHEMA product_service TO pulse_app;
GRANT USAGE ON SCHEMA analytics_service TO pulse_app;
GRANT USAGE ON SCHEMA data_pipeline_service TO pulse_app;
GRANT USAGE ON SCHEMA notification_service TO pulse_app;
GRANT USAGE ON SCHEMA super_admin_service TO pulse_app;
GRANT USAGE ON SCHEMA identity_service TO pulse_app;

-- Grant DML only (no DDL)
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO pulse_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA auth_service TO pulse_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA user_service TO pulse_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA customer_service TO pulse_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA lead_service TO pulse_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA email_campaign_service TO pulse_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ai_service TO pulse_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA agent_orchestrator TO pulse_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA product_service TO pulse_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA analytics_service TO pulse_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA data_pipeline_service TO pulse_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA notification_service TO pulse_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA super_admin_service TO pulse_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA identity_service TO pulse_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pulse_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA auth_service TO pulse_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA user_service TO pulse_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA customer_service TO pulse_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA lead_service TO pulse_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA email_campaign_service TO pulse_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ai_service TO pulse_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA agent_orchestrator TO pulse_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA product_service TO pulse_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA analytics_service TO pulse_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA data_pipeline_service TO pulse_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA notification_service TO pulse_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA super_admin_service TO pulse_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA identity_service TO pulse_app;

-- Create a separate migration role with DDL privileges
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pulse_migrator') THEN
        CREATE ROLE pulse_migrator WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
END
$$;

GRANT pulse_app TO pulse_migrator;
GRANT CREATE ON SCHEMA public TO pulse_migrator;
GRANT CREATE ON SCHEMA auth_service TO pulse_migrator;
GRANT CREATE ON SCHEMA user_service TO pulse_migrator;
GRANT CREATE ON SCHEMA customer_service TO pulse_migrator;
GRANT CREATE ON SCHEMA lead_service TO pulse_migrator;
GRANT CREATE ON SCHEMA email_campaign_service TO pulse_migrator;
GRANT CREATE ON SCHEMA ai_service TO pulse_migrator;
GRANT CREATE ON SCHEMA agent_orchestrator TO pulse_migrator;
GRANT CREATE ON SCHEMA product_service TO pulse_migrator;
GRANT CREATE ON SCHEMA analytics_service TO pulse_migrator;
GRANT CREATE ON SCHEMA data_pipeline_service TO pulse_migrator;
GRANT CREATE ON SCHEMA notification_service TO pulse_migrator;
GRANT CREATE ON SCHEMA super_admin_service TO pulse_migrator;
GRANT CREATE ON SCHEMA identity_service TO pulse_migrator;

-- Verify: confirm pulse_app does NOT have BYPASSRLS
SELECT rolname, rolsuper, rolcreatedb, rolbypassrls
FROM pg_roles
WHERE rolname IN ('pulse_app', 'pulse_migrator');
