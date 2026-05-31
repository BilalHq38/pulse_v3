from __future__ import annotations

import asyncio

from shared.config import is_production

_BOOTSTRAP_LOCK = asyncio.Lock()
_BOOTSTRAP_READY = False
_SCHEMA = "agent_orchestrator"
_TABLES = (
    "global_memory",
    "workflows",
    "workflow_executions",
    "workflow_transitions",
    "agent_memory",
)

_DDL = (
    f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}",
    f"""
    CREATE TABLE IF NOT EXISTS {_SCHEMA}.global_memory (
        id TEXT PRIMARY KEY,
        memory_key TEXT NOT NULL UNIQUE,
        company_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL DEFAULT '',
        customer_id TEXT NOT NULL DEFAULT '',
        lead_id TEXT NOT NULL DEFAULT '',
        identity_context JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        conversation_history JSONB NOT NULL DEFAULT '[]'::jsonb,
        knowledge_context TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '',
        shared_context JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {_SCHEMA}.workflows (
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
        input_payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        shared_context JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        final_output JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        error TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMPTZ
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {_SCHEMA}.workflow_executions (
        id TEXT PRIMARY KEY,
        workflow_id TEXT NOT NULL,
        company_id TEXT NOT NULL,
        trace_id TEXT NOT NULL DEFAULT '',
        agent_name TEXT NOT NULL,
        status TEXT NOT NULL,
        routing_decision JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        input_payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        output_payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        error TEXT NOT NULL DEFAULT '',
        duration_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
        attempt INTEGER NOT NULL DEFAULT 0,
        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMPTZ
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {_SCHEMA}.workflow_transitions (
        id TEXT PRIMARY KEY,
        workflow_id TEXT NOT NULL,
        company_id TEXT NOT NULL,
        trace_id TEXT NOT NULL DEFAULT '',
        from_agent TEXT NOT NULL DEFAULT '',
        to_agent TEXT NOT NULL DEFAULT '',
        decision_reason TEXT NOT NULL DEFAULT '',
        decision_mode TEXT NOT NULL DEFAULT 'rule_based',
        state_snapshot JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {_SCHEMA}.agent_memory (
        id TEXT PRIMARY KEY,
        workflow_id TEXT NOT NULL,
        company_id TEXT NOT NULL,
        trace_id TEXT NOT NULL DEFAULT '',
        agent_name TEXT NOT NULL,
        memory_key TEXT NOT NULL,
        memory_value JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_agent_memory_workflow_agent_key UNIQUE (workflow_id, agent_name, memory_key)
    )
    """,
    f"CREATE INDEX IF NOT EXISTS idx_global_memory_company ON {_SCHEMA}.global_memory(company_id, updated_at DESC)",
    f"CREATE INDEX IF NOT EXISTS idx_workflows_company_status ON {_SCHEMA}.workflows(company_id, status, updated_at DESC)",
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_workflows_message_idempotency "
    f"ON {_SCHEMA}.workflows(company_id, entity_type, entity_id) WHERE workflow_kind='message' AND entity_type='conversation_message'",
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_workflows_entity_idempotency "
    f"ON {_SCHEMA}.workflows(company_id, workflow_kind, entity_type, entity_id)",
    f"CREATE INDEX IF NOT EXISTS idx_workflows_trace ON {_SCHEMA}.workflows(trace_id, updated_at DESC)",
    f"CREATE INDEX IF NOT EXISTS idx_workflow_executions_workflow ON {_SCHEMA}.workflow_executions(workflow_id, started_at DESC)",
    f"CREATE INDEX IF NOT EXISTS idx_workflow_executions_company ON {_SCHEMA}.workflow_executions(company_id, started_at DESC)",
    f"CREATE INDEX IF NOT EXISTS idx_workflow_transitions_workflow ON {_SCHEMA}.workflow_transitions(workflow_id, created_at DESC)",  # noqa: E501
)


async def _verify_agent_orchestrator_schema(db) -> None:
    missing: list[str] = []
    for table_name in _TABLES:
        relation = f"{_SCHEMA}.{table_name}"
        exists = bool(await db.fetchval("SELECT to_regclass($1) IS NOT NULL", relation))
        if not exists:
            missing.append(f"relation:{relation}")
    if missing:
        raise RuntimeError(
            "Agent orchestrator schema is missing required migrated objects: "
            f"{', '.join(missing)}. Run Alembic migrations before starting production services."
        )


async def bootstrap_agent_orchestrator(db) -> None:
    global _BOOTSTRAP_READY
    if _BOOTSTRAP_READY:
        return
    async with _BOOTSTRAP_LOCK:
        if _BOOTSTRAP_READY:
            return
        if is_production():
            await _verify_agent_orchestrator_schema(db)
            _BOOTSTRAP_READY = True
            return
        for statement in _DDL:
            await db.execute(statement)
        _BOOTSTRAP_READY = True
