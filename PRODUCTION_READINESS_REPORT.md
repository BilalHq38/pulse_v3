# Pulse Engine — Production Readiness Report

**Assessment Date:** 2026-05-30  
**Assessor:** Principal Engineer Review  
**Previous Score:** 47/100 (pre-hardening)  
**Current Score:** 84/100

---

## Executive Summary

The platform has undergone a two-phase hardening cycle. Phase 1 addressed 20 critical infrastructure, security, and AI reliability blockers. Phase 2 extracted all runtime DDL into Alembic migrations, split background workers into dedicated ECS services, and produced the complete AWS deployment guide. The platform is now **conditionally production-ready**: it can be deployed to staging immediately and to production after completing the 5 remaining validation gates.

---

## Score Breakdown

| Category | Before | After | Notes |
|----------|--------|-------|-------|
| Security | 4/20 | 17/20 | CORS fixed, injection patterns split, HTTPS enforced, secrets in Secrets Manager |
| Database | 5/15 | 12/15 | Alembic migrations created, roles secured; -3 pending live RDS validation |
| AI Reliability | 6/15 | 13/15 | Grounding fixed, memory injection, false-positive injection fixed |
| Infrastructure | 8/20 | 18/20 | Compose hardened, ECS service split, scheduler singleton |
| Observability | 5/15 | 12/15 | CloudWatch log groups, health checks, alarms defined |
| CI/CD | 4/10 | 9/10 | pytest job added; -1 for no e2e tests |
| Documentation | 2/5 | 3/5 | Guides created; -2 for no runbook |
| **Total** | **34/100** | **84/100** | |

---

## Resolved Blockers (Phase 1 + Phase 2)

### Security
- [x] `ALLOW_UNSIGNED_WEB_CHAT_WIDGET` default changed to `false`
- [x] `CORS_ORIGINS="*"` wildcard removed; explicit allowlist required
- [x] QA preview domain removed from production CORS
- [x] PostgreSQL SSL default changed to `require`
- [x] Docker log rotation added (json-file, 50MB/3 files)
- [x] Resource limits added to all Docker services
- [x] Redis `--maxmemory 512mb` set
- [x] nginx security headers (CSP, HSTS, X-Frame-Options, etc.)
- [x] `localStorage` sensitive field removal (company_id, role, billing_status)
- [x] Prompt injection pattern set split: strict for user input, narrow for chunks

### Database
- [x] Alembic baseline migration created
- [x] `agent_orchestrator` schema (5 tables, 8 indexes) extracted to migration `b1c2d3e4f5a6`
- [x] `analytics_service` schema (7 tables, 14 indexes + RLS) extracted to migration `c2d3e4f5a6b7`
- [x] `pending_signups` table extracted to migration `d3e4f5a6b7c8`
- [x] `context_memory_dedup` table extracted to migration `e4f5a6b7c8d9`
- [x] Embedding vector HNSW index (IVFFlat → HNSW upgrade) in migration `f5a6b7c8d9e0`
- [x] `backend/scripts/create_db_roles.sql` — `pulse_app` (NOSUPERUSER, NOBYPASSRLS)

### AI Reliability
- [x] Product grounding validator false positives fixed (shown-products injection)
- [x] Product image routing: direct ID lookup before semantic search
- [x] Context router: "order", "checkout", referential phrases added
- [x] `fastembed` wrapped in `asyncio.to_thread()` — unblocks event loop
- [x] Rolling summary hallucination prevention

### Infrastructure
- [x] All Dockerfiles: `--workers 2 --timeout-graceful-shutdown 30`
- [x] Health checks migrated from Python subprocess to `wget`
- [x] Follow-up scheduler standalone ECS entrypoint created
- [x] Background worker standalone entrypoint created
- [x] ECS task definitions for scheduler, worker, data-pipeline-worker
- [x] S3 media storage adapter implemented

### CI/CD
- [x] `pytest` CI job added with postgres:16 + redis:7 service containers

---

## Remaining Blockers

### P0 — Must Fix Before Production Go-Live

**B1: Run live Alembic upgrade in staging and validate**
```bash
cd backend && alembic upgrade head
alembic current   # must match f5a6b7c8d9e0
alembic heads     # must show single head (no divergence)
```
- Risk: Migrations use `SET search_path` which may not work as expected in asyncpg context
- Mitigation: Test in staging against real Aurora PostgreSQL

**B2: Verify RDS roles have no SUPERUSER / BYPASSRLS**
```sql
SELECT rolname, rolsuper, rolbypassrls
FROM pg_roles WHERE rolname IN ('pulse_app', 'pulse_migrator');
-- pulse_app: rolsuper=false, rolbypassrls=false REQUIRED
```

**B3: Cross-tenant RLS live test**
- Create two test tenants in staging
- Issue requests authenticated as tenant A; verify tenant B data is never returned
- Check analytics_service tables specifically (new schema with RLS enabled)

### P1 — Must Fix Before First Real Customer

**B4: Stripe test-mode end-to-end flows**
- Signup → checkout → webhook → account creation
- Subscription cancellation → downgrade flow
- Grace period expiry → lockout

**B5: Followup scheduler singleton enforcement**
- Confirm ECS service `desiredCount=1` and auto-scaling min/max both = 1
- Verify that `FOR UPDATE SKIP LOCKED` prevents double-scheduling in parallel load test

### P2 — Recommended Before Scale

**B6: AI red-team tests**
- Test injection patterns with adversarial inputs
- Verify `_CHUNK_INJECTION_PATTERNS` vs `_RISKY_INJECTION_PATTERNS` split is correct
- Test with: `<system>`, `[inst]`, `ignore all previous instructions`

**B7: Load test**
- Target: 100 concurrent conversations, 10 messages/second
- Verify DB connection pool does not exhaust (`asyncpg` pool max = 20 per service)
- Verify Redis does not OOM under load (512MB limit)
- Expected bottleneck: LLM API rate limits (Gemini: 2,000 RPM on Flash)

**B8: Backup restore drill**
- Restore from RDS automated snapshot to test cluster
- Verify data integrity with `pg_dump --schema-only` diff

---

## Architecture Warnings

### Scheduler Singleton
The follow-up scheduler MUST run as a single ECS task. The `FOR UPDATE SKIP LOCKED` logic prevents two tasks from claiming the same row, but two tasks will each independently schedule follow-ups, resulting in 2× the messages sent per customer. ECS service `desiredCount` is set to 1 in `infra/ecs/followup-scheduler-service.json`; **never auto-scale this service**.

### Memory Growth
The `context_memories` table grows unbounded. At 10 messages/conversation × 1000 conversations/day × 90 days = 900,000 rows. Add a cleanup job or TTL policy before 30 days of production traffic.

### Embedding Deduplication
Migration `f5a6b7c8d9e0` deletes duplicate embeddings before creating the unique index. This is safe but will silently remove rows. Run `SELECT COUNT(*) FROM embeddings` before and after migration to quantify the cleanup.

### Analytics Schema Isolation
`analytics_service` tables live in a separate schema with `search_path`. Application code in `data_pipeline/bootstrap.py` still sets `search_path` at runtime. After Alembic migration completes, you must ensure the `pulse_app` role has `USAGE` on the `analytics_service` schema:
```sql
GRANT USAGE ON SCHEMA analytics_service TO pulse_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA analytics_service TO pulse_app;
```

---

## Capacity Estimates

| Load Level | RPS | DB Connections | Redis Memory | LLM Tokens/min |
|-----------|-----|----------------|--------------|----------------|
| Launch (50 tenants) | 5 | 20 | 50MB | 50,000 |
| Growth (500 tenants) | 50 | 80 | 200MB | 500,000 |
| Scale (5000 tenants) | 500 | 300 | 400MB | 5,000,000 |

At Growth tier: upgrade RDS to db.r7g.xlarge and Redis to cache.r7g.xlarge.  
At Scale tier: add Aurora read replica, Redis cluster mode, multiple ECS tasks per service.

---

## Go / No-Go Recommendation

| Gate | Status | Owner |
|------|--------|-------|
| Alembic upgrade head runs clean in staging | ⏳ Pending | DevOps |
| RDS roles verified NOSUPERUSER NOBYPASSRLS | ⏳ Pending | DBA |
| Cross-tenant RLS isolation confirmed | ⏳ Pending | QA |
| Stripe test-mode flows pass | ⏳ Pending | Engineering |
| Scheduler singleton confirmed | ⏳ Pending | DevOps |

**Recommendation: NO-GO for production until all 5 P0/P1 gates are green.**  
**Recommendation: GO for staging deployment immediately — all code is ready.**

Once staging passes all 5 gates:  
**CONDITIONAL GO for production** — platform is technically sound; business risk is low.
