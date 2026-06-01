-- 023: Runtime schema and provider hardening.
-- Keeps partially migrated databases aligned with the current Docker runtime.

CREATE SCHEMA IF NOT EXISTS agent_orchestrator;

CREATE TABLE IF NOT EXISTS agent_orchestrator.global_memory (
    id TEXT PRIMARY KEY,
    memory_key TEXT NOT NULL UNIQUE,
    company_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL DEFAULT '',
    customer_id TEXT NOT NULL DEFAULT '',
    lead_id TEXT NOT NULL DEFAULT '',
    identity_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    conversation_history JSONB NOT NULL DEFAULT '[]'::jsonb,
    knowledge_context TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    shared_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_orchestrator.workflows (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    trace_id TEXT NOT NULL DEFAULT '',
    workflow_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    entity_type TEXT NOT NULL DEFAULT '',
    entity_id TEXT NOT NULL DEFAULT '',
    conversation_id TEXT NOT NULL DEFAULT '',
    customer_id TEXT NOT NULL DEFAULT '',
    lead_id TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    current_agent TEXT NOT NULL DEFAULT '',
    routing_mode TEXT NOT NULL DEFAULT 'rule_based',
    intent TEXT NOT NULL DEFAULT '',
    lead_status TEXT NOT NULL DEFAULT '',
    requested_by TEXT NOT NULL DEFAULT '',
    input_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    shared_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    final_output JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS agent_orchestrator.workflow_executions (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    trace_id TEXT NOT NULL DEFAULT '',
    agent_name TEXT NOT NULL,
    status TEXT NOT NULL,
    routing_decision JSONB NOT NULL DEFAULT '{}'::jsonb,
    input_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT NOT NULL DEFAULT '',
    duration_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS agent_orchestrator.workflow_transitions (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    trace_id TEXT NOT NULL DEFAULT '',
    from_agent TEXT NOT NULL DEFAULT '',
    to_agent TEXT NOT NULL DEFAULT '',
    decision_reason TEXT NOT NULL DEFAULT '',
    decision_mode TEXT NOT NULL DEFAULT 'rule_based',
    state_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_orchestrator.agent_memory (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    trace_id TEXT NOT NULL DEFAULT '',
    agent_name TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    memory_value JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_agent_memory_workflow_agent_key UNIQUE (workflow_id, agent_name, memory_key)
);

CREATE INDEX IF NOT EXISTS idx_global_memory_company
    ON agent_orchestrator.global_memory(company_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflows_company_status
    ON agent_orchestrator.workflows(company_id, status, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_workflows_message_idempotency
    ON agent_orchestrator.workflows(company_id, entity_type, entity_id)
    WHERE workflow_kind='message' AND entity_type='conversation_message';
CREATE UNIQUE INDEX IF NOT EXISTS uq_workflows_entity_idempotency
    ON agent_orchestrator.workflows(company_id, workflow_kind, entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_workflows_trace
    ON agent_orchestrator.workflows(trace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_executions_workflow
    ON agent_orchestrator.workflow_executions(workflow_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_executions_company
    ON agent_orchestrator.workflow_executions(company_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_transitions_workflow
    ON agent_orchestrator.workflow_transitions(workflow_id, created_at DESC);

ALTER TABLE company_faqs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
UPDATE company_faqs
   SET updated_at = COALESCE(updated_at, created_at, NOW())
 WHERE updated_at IS NULL;
ALTER TABLE company_faqs ALTER COLUMN updated_at SET DEFAULT NOW();
ALTER TABLE company_faqs ALTER COLUMN updated_at SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_company_faqs_company_updated_at
    ON company_faqs(company_id, updated_at DESC);

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS sentiment_score NUMERIC(5,4);
UPDATE conversations SET sentiment_score = 0 WHERE sentiment_score IS NULL;
ALTER TABLE conversations ALTER COLUMN sentiment_score SET DEFAULT 0;
ALTER TABLE conversations ALTER COLUMN sentiment_score SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_embeddings_company_source_lookup
    ON embeddings(company_id, source_type, source_id);

CREATE TABLE IF NOT EXISTS context_memory_dedup (
    company_id TEXT NOT NULL,
    convo_id TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    memory_id TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (company_id, convo_id, message_id, memory_type)
);
CREATE INDEX IF NOT EXISTS idx_context_memory_dedup_company
    ON context_memory_dedup(company_id, created_at DESC);
