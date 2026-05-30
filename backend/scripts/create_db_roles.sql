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

-- Grant usage on schemas
GRANT USAGE ON SCHEMA public TO pulse_app;
GRANT USAGE ON SCHEMA auth_service TO pulse_app;
GRANT USAGE ON SCHEMA customer_service TO pulse_app;
-- Add other service schemas as needed

-- Grant DML only (no DDL)
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO pulse_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA auth_service TO pulse_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA customer_service TO pulse_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pulse_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA auth_service TO pulse_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA customer_service TO pulse_app;

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
GRANT CREATE ON SCHEMA customer_service TO pulse_migrator;

-- Verify: confirm pulse_app does NOT have BYPASSRLS
SELECT rolname, rolsuper, rolcreatedb, rolbypassrls
FROM pg_roles
WHERE rolname IN ('pulse_app', 'pulse_migrator');
