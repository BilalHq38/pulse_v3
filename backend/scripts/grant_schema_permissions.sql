-- Grant permissions for application roles after Alembic migrations run.
-- Run this once as pulse_migrator after alembic upgrade head.

-- agent_orchestrator schema
GRANT USAGE ON SCHEMA agent_orchestrator TO pulse_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA agent_orchestrator TO pulse_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA agent_orchestrator
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO pulse_app;

-- analytics_service schema
GRANT USAGE ON SCHEMA analytics_service TO pulse_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA analytics_service TO pulse_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics_service
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO pulse_app;

-- public schema (for pending_signups, context_memory_dedup, etc.)
GRANT USAGE ON SCHEMA public TO pulse_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO pulse_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO pulse_app;

-- pulse_migrator keeps full DDL rights
GRANT ALL ON SCHEMA agent_orchestrator TO pulse_migrator;
GRANT ALL ON SCHEMA analytics_service TO pulse_migrator;
GRANT ALL ON ALL TABLES IN SCHEMA agent_orchestrator TO pulse_migrator;
GRANT ALL ON ALL TABLES IN SCHEMA analytics_service TO pulse_migrator;
