# Runtime Schema and Provider Verification Pass

Date: 2026-06-01

This pass covers runtime blockers found after the `docs/Fixes.md` implementation tracker was completed.

## Completed

- Mounted `./secrets/gcp-vertex-sa.json` into the Docker services that run Vertex/Gemini calls.
- Kept Vertex/Gemini flags configurable through `.env` instead of hardcoding Docker values.
- Added `LOCAL_ML_ENABLED=false` for Docker so optional MiniLM/ONNX paths do not initialize during normal runtime.
- Replaced duplicate legacy `services.ai_service.local_ml` with a compatibility alias to `services.conversation_engine.local_ml`.
- Replaced legacy `services.ai_service.llm_client` and `services.ai_service.embedding_service` shims with module aliases so old imports and monkeypatches hit the moved implementation.
- Qualified agent-orchestrator state tables with `agent_orchestrator.*`.
- Added migration `023_runtime_schema_provider_hardening.sql` and Alembic revision `f6a7b8c9d0e1`.
- Applied the live DB migration inside Docker.

## Schema Repaired

- `agent_orchestrator.workflows`
- `agent_orchestrator.workflow_executions`
- `agent_orchestrator.workflow_transitions`
- `agent_orchestrator.global_memory`
- `agent_orchestrator.agent_memory`
- `company_faqs.updated_at`
- `conversations.sentiment_score DEFAULT 0 NOT NULL`
- `context_memory_dedup`
- `idx_embeddings_company_source_lookup`

## Verification

- `py_compile` passed for modified Python files.
- `docker compose config` passed.
- Targeted runtime tests passed: `55 passed`.
- Embedding/RAG compatibility tests passed: `21 passed`.
- Docker rebuild completed for affected Python services.
- Restarted affected services; `docker compose ps` reports healthy.
- Gateway health: `200`.
- Frontend health: `200`.
- Live DB schema probe: all repaired objects present.

## Still Not Production Complete

- Full backend test suite is not green: `429 passed, 37 skipped, 10 failed`.
- Remaining failures are outside this runtime schema/provider pass and are follow-up scheduler expectations, conversation-engine turn persistence/proactive-turn expectations, and a hardening test that conflicts with the newly required local Docker Vertex credential mount.
- Runtime logs still warn that the local development DB role is over-privileged. Production should use least-privilege service roles.
