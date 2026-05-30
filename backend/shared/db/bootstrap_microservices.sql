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

COMMENT ON SCHEMA auth_service IS 'Auth, sessions, OAuth, and verification flows.';
COMMENT ON SCHEMA user_service IS 'Users, roles, and permissions.';
COMMENT ON SCHEMA customer_service IS 'Customers, conversations, tickets, and support workflows.';
COMMENT ON SCHEMA lead_service IS 'Lead capture, qualification, and sales automation.';
COMMENT ON SCHEMA email_campaign_service IS 'Outbound email campaign orchestration and recipients.';
COMMENT ON SCHEMA ai_service IS 'AI engines, embeddings, MCP, social AI, and model runtime data.';
COMMENT ON SCHEMA agent_orchestrator IS 'Multi-agent orchestration, workflow state, and agent memory.';
COMMENT ON SCHEMA product_service IS 'Products, FAQs, onboarding documents, and catalog metadata.';
COMMENT ON SCHEMA analytics_service IS 'Reports, summaries, and derived metrics.';
COMMENT ON SCHEMA data_pipeline_service IS 'Raw event ingestion, ETL state, and derived pipeline metrics.';
COMMENT ON SCHEMA notification_service IS 'Notifications, delivery metadata, and outbound messaging.';
COMMENT ON SCHEMA super_admin_service IS 'Platform-wide tenant operations, oversight, and aggregate controls.';
COMMENT ON SCHEMA identity_service IS 'Identity unification, unified profiles, merge/split operations, cross-channel mapping.';

-- Transitional guidance:
-- 1. Keep legacy shared tables in public while you migrate domain tables into the new schemas.
-- 2. Run each service with search_path = "<service_schema>", public.
-- 3. Replace legacy cross-domain reads with REST calls service-by-service.
