# Pulse Engine v2 — Production-Readiness Audit Report

**Date:** 2026-05-30  
**Auditor:** Claude Code (claude-sonnet-4-6)  
**Branch:** `claude/dreamy-wozniak-Vtfsk`  
**Codebase root:** `/home/user/pulse_engine-v2`

---

## EXECUTIVE SUMMARY

Pulse Engine v2 is a multi-tenant SaaS customer-engagement platform that combines AI-assisted messaging (WhatsApp, Facebook, Instagram, web-chat, email), lead and order management, identity unification, and a conversational AI engine backed by Gemini/OpenAI/Anthropic. The stack is a React single-page application proxied by an API gateway that fans out to 13 Python microservices, one Node.js WhatsApp bridge, PostgreSQL 16 with pgvector, and Redis 7.

**Production Readiness Verdict: NOT READY — Conditionally Ready in 2–4 weeks with critical blockers resolved.**

**Critical blocker count by category:**

| Category | Critical | High |
|---|---|---|
| Security | 4 | 5 |
| DevOps / Infrastructure | 3 | 4 |
| Database | 2 | 3 |
| Full-Stack Engineering | 1 | 4 |
| AI Systems | 1 | 3 |
| Cloud Architecture | 0 | 5 |

---

## 1. SYSTEM ARCHITECTURE OVERVIEW

### Service Map

| Service | Port | Language | Purpose |
|---|---|---|---|
| `gateway` | 8000 | Python/FastAPI | Reverse proxy, JWT validation, rate limiting, billing pre-flight |
| `auth` | 8001 | Python/FastAPI | Login, register, OAuth, refresh tokens, email verification |
| `user` | 8002 | Python/FastAPI | Users, roles, invitations, settings |
| `customer` | 8003 | Python/FastAPI | Customers, conversations, tickets, webhook ingestion, Socket.IO |
| `lead` | 8004 | Python/FastAPI | Lead management, nurture sequences, stage service |
| `ai` | 8005 | Python/FastAPI | Sentiment, intent, embeddings, RAG, KB summary, LLM client |
| `analytics` | 8006 | Python/FastAPI | Metrics, dashboards, reports |
| `product` | 8007 | Python/FastAPI | Products, FAQs, onboarding docs, company data |
| `notification` | 8008 | Python/FastAPI | Push/in-app notifications |
| `agent-orchestrator` | 8009 | Python/FastAPI | Multi-agent conversation orchestration |
| `identity` | 8010 | Python/FastAPI | Cross-channel identity unification, merge/split, consent |
| `super-admin` | 8011 | Python/FastAPI | Platform-level admin operations, tenant management |
| `data-pipeline` | 8012 | Python/FastAPI | ETL for leads, analytics ingestion |
| `email-campaign` | 8013 | Python/FastAPI | Outbound email campaigns |
| `whatsapp-bridge` | 3001 | Node.js/Express | WhatsApp Web.js linked-device bridge |
| `frontend` | 3000 | React/nginx | SPA served via nginx |
| `postgres` | 5432 | PostgreSQL 16 + pgvector | Shared database, row-level security per tenant |
| `redis` | 6379 | Redis 7 | Cache, rate limiting, background job queue, billing cache |

### Frontend Architecture

React 18 SPA with lazy-loaded routes, React Router v6, Tailwind CSS + shadcn/ui component library, axios with in-memory access token + httpOnly refresh cookie pattern, Socket.IO client (customer service websocket), Storybook for component development.

### Backend Architecture

FastAPI microservices sharing a `shared/` library (database pool, JWT, config, billing guard, rate limiter, JSON logging, tracing). All services use asyncpg for PostgreSQL access with a connection pool per service. Schema isolation via PostgreSQL schemas (e.g., `auth_service`, `customer_service`). Row-Level Security enforces tenant isolation at the database layer. Redis Streams power the background job queue.

### Data Flow (Text Diagram)

```
Browser ──► nginx (port 3000) ──► React SPA
                                        │
                                        ▼
External ──► API Gateway (8000) ──► JWT/rate-limit/billing check
             │
             ├──► auth (8001)
             ├──► user (8002)
             ├──► customer (8003) ◄──► Socket.IO (real-time)
             │        │
             │        ├──► conversation engine (in-process)
             │        ├──► agent orchestrator (8009)
             │        └──► AI service (8005)
             ├──► lead (8004)
             ├──► ai (8005) ◄──► Gemini / OpenAI / Anthropic
             ├──► product (8007)
             ├──► identity (8010)
             └──► ... (other services)
                        │
                        ▼
             PostgreSQL (5432) + Redis (6379)
                        │
             WhatsApp Bridge (3001)
             Meta Cloud API / external channels
```

### Integration Points

- Meta Graph API (WhatsApp Cloud, Facebook, Instagram) via HMAC-signed webhooks and outbound calls
- WhatsApp Web.js Bridge (Puppeteer/Chromium linked-device mode) — separate Node container
- Stripe (billing, checkout, webhooks)
- Google OAuth, Facebook OAuth
- SMTP/Brevo (email delivery)
- Google Vertex AI / Gemini API, OpenAI API, Anthropic API
- Google Cloud Storage (Vertex AI service account)

---

## 2. FEATURE INVENTORY

| Feature | Status |
|---|---|
| Multi-tenant SaaS with company isolation | ✅ Complete |
| Email/password authentication | ✅ Complete |
| Google OAuth sign-in | ✅ Complete |
| Facebook OAuth sign-in | ✅ Complete |
| Email verification flow | ✅ Complete |
| Password reset | ✅ Complete |
| Team invitations with seat limits | ✅ Complete |
| Onboarding wizard with billing gate | ✅ Complete |
| Stripe subscription management | ✅ Complete |
| Super-admin dashboard | ✅ Complete |
| Inbox / conversation management | ✅ Complete |
| Real-time messaging via Socket.IO | ✅ Complete |
| WhatsApp Cloud API (Meta) integration | ✅ Complete |
| WhatsApp Web.js bridge | ✅ Complete |
| Facebook Messenger integration | ✅ Complete |
| Instagram messaging integration | ✅ Complete |
| Web chat widget (embeddable) | ✅ Complete |
| Email inbound/outbound channel | ✅ Complete |
| Lead management & pipeline | ✅ Complete |
| Lead scoring (AI-assisted) | ✅ Complete |
| Lead nurture sequences | ✅ Complete |
| Customer profile management | ✅ Complete |
| Order management | ✅ Complete |
| Ticket management | ✅ Complete |
| Product catalog with images | ✅ Complete |
| Public product pages (shareable links) | ✅ Complete |
| Bulk product upload | ✅ Complete |
| Knowledge base management | ✅ Complete |
| AI conversation engine (RAG + grounding) | ✅ Complete |
| Sentiment analysis | ✅ Complete |
| Intent classification | ✅ Complete |
| Prompt injection detection | ✅ Complete |
| AI response safety filter | ✅ Complete |
| AI identity-disclosure redaction | ✅ Complete |
| Multi-provider AI fallback (Gemini→OpenAI→Anthropic) | ✅ Complete |
| Vector embeddings + pgvector search | ✅ Complete |
| Memory engine (short-term + long-term) | ✅ Complete |
| Proactive/follow-up scheduler | ✅ Complete |
| Email campaign management | ✅ Complete |
| Analytics dashboard | ✅ Complete |
| Identity unification (cross-channel merge) | ✅ Complete |
| Consent management (GDPR) | ✅ Complete |
| Rate limiting (gateway + per endpoint) | ✅ Complete |
| Structured JSON logging | ✅ Complete |
| Distributed request tracing | ✅ Complete |
| Background job queue (Redis Streams) | ✅ Complete |
| Token blacklist / session revocation | ✅ Complete |
| Billing cache (Redis-backed, DB fallback) | ✅ Complete |
| Media upload and serving | ✅ Complete |
| Prometheus-compatible metrics export | ⚠️ Partial (in-memory only, no scrape endpoint for Prometheus) |
| Automated database migrations | ❌ Missing (schema applied at startup via raw SQL; no Alembic migrations) |
| Distributed tracing export (Jaeger/Zipkin/OTEL) | ❌ Missing |
| External error tracking (Sentry) | ❌ Missing |
| S3/object storage for media files | ❌ Missing (local disk only) |
| Container resource limits | ❌ Missing |
| Health check for Redis (per-service) | ❌ Missing |
| CI test execution (unit/integration) | ❌ Missing (CI only lints and validates compose) |

---

## 3. FULL STACK ENGINEERING REVIEW

### 3.1 **[CRITICAL]** Monolith (`server.py`) Coexists with Microservices — Dual-Entrypoint Confusion

**Root cause:** `backend/server.py` registers all routers as a FastAPI monolith and mounts the identity service at `/`, then `api_gateway/app.py` is the microservices gateway. Both exist and are configured in `docker-compose.yml` indirectly (only `gateway` service uses `api_gateway/Dockerfile`). However the `server.py` path remains invokable and contains `app.state.allow_direct_jwt_auth = True` plus `app.add_middleware(CORSMiddleware, allow_methods=["*"], allow_headers=["*"])` — a wildcard CORS configuration.  
**Affected files:** `backend/server.py`, `backend/api_gateway/app.py`  
**Risk:** Operators who mistakenly deploy `server.py` instead of the gateway get an unguarded CORS wildcard and no billing pre-flight. The code also references `backend/core/config.py:25: CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")` which defaults to `*`.  
**Fix:** Remove or clearly deprecate `server.py`; ensure all production paths go through `api_gateway/app.py`. Remove the `CORS_ORIGINS = "*"` default in `core/config.py`.

### 3.2 **[HIGH]** CORS Wildcard Default in `core/config.py`

**Root cause:** `backend/core/config.py:25` sets `CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")`.  
**Affected files:** `backend/core/config.py`, `backend/server.py` (lines 226–232)  
**Risk:** Any origin can make credentialed requests to the legacy monolith path; enables CSRF via browser cross-origin requests.  
**Fix:** Remove the `"*"` default; require explicit `CORS_ORIGINS` in production, fail startup if unset.

### 3.3 **[HIGH]** Hardcoded Hardcoded `qa-finalize.preview.emergentagent.com` in CORS Allowlist

**Root cause:** `backend/shared/config.py:17` and `backend/core/config.py:29–31` include `https://qa-finalize.preview.emergentagent.com` in `DEFAULT_ORIGINS` and `gateway_allowed_origins()`.  
**Affected files:** `backend/shared/config.py`, `backend/core/config.py`  
**Risk:** A third-party preview domain (staging QA environment) is permanently whitelisted in production builds, allowing that origin to make credentialed API calls.  
**Fix:** Remove the QA origin from default lists; inject via `GATEWAY_ALLOWED_ORIGINS` env var only for the relevant environment.

### 3.4 **[HIGH]** `SELECT *` Used Pervasively Across All Routers

**Root cause:** 215+ occurrences of `SELECT *` across all routers (`backend/routers/`).  
**Affected files:** `backend/routers/conversations.py`, `backend/routers/leads.py`, `backend/routers/auth.py`, all others.  
**Risk:** Fetches all columns including large JSONB blobs, binary attachments references, and future sensitive fields. Increases network I/O, memory, and makes schema changes risky.  
**Fix:** Select only required columns. For high-frequency queries (conversations list, messages fetch), create explicit column sets.

### 3.5 **[HIGH]** Unbounded Conversation and Message Queries (`LIMIT 500`, `LIMIT 200`)

**Root cause:** `backend/routers/conversations.py:556` applies `LIMIT 500` to conversation list and `LIMIT 200` to conversation logs without cursor pagination.  
**Affected files:** `backend/routers/conversations.py` (lines 364, 556, 2332)  
**Risk:** A tenant with thousands of conversations fetches 500 full rows per request — O(n) memory, potential slow query. No cursor to allow clients to page forward.  
**Fix:** Implement keyset pagination (`WHERE updated_at < :cursor ORDER BY updated_at DESC LIMIT 50`).

### 3.6 **[HIGH]** Frontend Stores User Object in `localStorage`

**Root cause:** `frontend/src/contexts/AuthContext.js` and `frontend/src/lib/api.js` persist the full user object (`pe_user`) in `localStorage`.  
**Affected files:** `frontend/src/contexts/AuthContext.js`, `frontend/src/lib/api.js`  
**Risk:** Any XSS vulnerability leaks the entire user object (including roles, company_id, billing_status). The access token itself is stored only in memory (good), but the user object is not.  
**Fix:** Store only non-sensitive display fields (name, avatar) in localStorage. Fetch authoritative user state from the server on each page load.

### 3.7 **[MEDIUM]** `nginx.conf` Has No Security Headers, Size Limits, or Rate Limiting

**Root cause:** `frontend/nginx.conf` only configures routing — no `add_header` for security headers, no `client_max_body_size`, no `limit_req`.  
**Affected files:** `frontend/nginx.conf`  
**Risk:** Frontend served without `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`. The backend gateway does set these headers, but the nginx server itself does not.  
**Fix:** Add security headers, `gzip on`, `client_max_body_size 5m`, connection timeouts.

### 3.8 **[MEDIUM]** No Global React Error Boundary

**Root cause:** `frontend/src/App.js` uses only `<Suspense>` but no `ErrorBoundary` component.  
**Affected files:** `frontend/src/App.js`  
**Risk:** Unhandled JavaScript errors in any lazy-loaded page crash the entire React tree with a blank screen.  
**Fix:** Wrap `<Routes>` in an `ErrorBoundary` component that renders a user-friendly error page.

### 3.9 **[MEDIUM]** `*.json` in `.gitignore` Would Block `package.json` and `package-lock.json`

**Root cause:** `.gitignore:11` contains `*.json` which would match `package.json`, `package-lock.json`, `tsconfig.json`, etc. The existing files are already tracked so git does not ignore them retroactively, but any newly created JSON files at the root level would silently not be committed.  
**Affected files:** `.gitignore:11`  
**Risk:** New configuration JSON files (e.g., tooling configs) may be accidentally excluded.  
**Fix:** Replace `*.json` with specific patterns like `credentials.json`, `service-account.json`, `*-secret.json`.

### 3.10 **[LOW]** No Global Loading State or Skeleton Screens

**Root cause:** Pages use `<Suspense fallback={<Spinner />}>` but individual data-fetching within pages has no consistent skeleton/loading state pattern.  
**Risk:** Poor perceived performance on slow connections.  
**Fix:** Add skeleton loading states to data-heavy pages (Inbox, Leads, Customers).

---

## 4. AI SYSTEMS REVIEW

### 4.1 **[CRITICAL]** `ALLOW_UNSIGNED_WEB_CHAT_WIDGET=true` is the Default in Production Config

**Root cause:** `backend/routers/webhooks.py:1501` and `backend/channel_layer/adapters/chat_widget.py:203` read `ALLOW_UNSIGNED_WEB_CHAT_WIDGET` with default `true`. The `.env.example:337` also sets it to `true`.  
**Affected files:** `backend/routers/webhooks.py`, `backend/channel_layer/adapters/chat_widget.py`, `.env.example`  
**Risk:** Any anonymous caller can POST messages to the web-chat webhook endpoint without signature validation, allowing message injection, conversation pollution, AI response extraction, and usage quota exhaustion. This is a production-breaking security and AI abuse vector.  
**Fix:** Set `ALLOW_UNSIGNED_WEB_CHAT_WIDGET=false` as the default. Require `WEB_CHAT_WIDGET_KEY` to be set in production and enforce HMAC signature checks.

### 4.2 **[HIGH]** IVFFlat Vector Index with `lists=100` is Suboptimal for Multi-Tenant Use

**Root cause:** `backend/sql_schema.sql:1392` creates `idx_embeddings_vector_cosine` using `ivfflat` with `lists=100` on the global `embeddings` table without per-tenant partitioning.  
**Affected files:** `backend/sql_schema.sql` (line 1392)  
**Risk:** IVFFlat requires training data to be representative; `lists=100` is only optimal for ~1M vectors. Multi-tenant data means each tenant's embedding distribution is mixed with others, reducing recall accuracy. Under high multi-tenant load, RLS on the shared table adds per-query overhead.  
**Fix:** Consider HNSW index (`USING hnsw`) which does not require training and has better recall characteristics. For scaling, partition the `embeddings` table by `company_id` using PostgreSQL table partitioning.

### 4.3 **[HIGH]** Prompt Injection Guard is Incomplete — Missing Common Jailbreak Patterns

**Root cause:** `backend/services/conversation_engine/prompt_builder.py:40–46` only checks 5 regex patterns (`<system>`, `</instruction>`, `[inst]`, `### system`, `ignore previous instructions`).  
**Affected files:** `backend/services/conversation_engine/prompt_builder.py`  
**Risk:** Common jailbreak strings like `DAN`, `JAILBREAK`, `Act as...`, `Pretend you are...`, `</s><s>`, `<|im_start|>system`, `Human:`, `Assistant:` are not caught. Retrieved RAG chunks from user-controlled KB articles can also contain injection patterns that pass the `is_chunk_safe()` check.  
**Fix:** Expand the injection pattern list. Also add input length enforcement in addition to truncation.

### 4.4 **[HIGH]** Local ML Model (`fastembed`) Loaded Synchronously in Async Context

**Root cause:** `backend/services/ai_service/local_ml.py:199–200` imports and instantiates `TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")` synchronously. The model warmup in multiple `Dockerfile` files runs `list(m.embed(['warmup']))` at build time to cache the ONNX model.  
**Affected files:** `backend/services/ai_service/local_ml.py`, various `Dockerfile` files  
**Risk:** If the fastembed model is called from an async route handler without `asyncio.get_event_loop().run_in_executor()`, it blocks the event loop for 100–500ms per embedding. Under concurrent traffic this will serialize all requests through a single thread.  
**Fix:** Wrap fastembed calls in `asyncio.to_thread()` or use an `Executor`.

### 4.5 **[HIGH]** AI Token Budget is Fixed at 12,000 Tokens — No Dynamic Scaling

**Root cause:** `AI_INPUT_TOKEN_BUDGET=12000` is the only budget control. The orchestrator uses a fixed-tier token budget system (`backend/services/conversation_engine/budget.py`) without adapting to conversation length or available context.  
**Affected files:** `backend/shared/config.py` (line 325), `.env.example`  
**Risk:** Long conversations approach the context window limit, causing older turns to be aggressively pruned and reducing conversation quality. Large product catalogs may exhaust the budget before customer history is included.  
**Fix:** Implement dynamic budget allocation based on conversation length and active retrieval sources.

### 4.6 **[MEDIUM]** Sentiment Analysis Uses a Small Hardcoded Profanity Wordlist

**Root cause:** `backend/services/conversation_engine/validator.py:29` defines `_PROFANITY = {"damn", "shit", "fuck", "asshole", "bitch"}` — 5 words.  
**Affected files:** `backend/services/conversation_engine/validator.py`  
**Risk:** Inadequate for production content moderation. Does not handle obfuscated spellings, multilingual content, or contextual profanity.  
**Fix:** Use a proper content moderation library or extend the wordlist significantly. Consider delegating tone validation to the LLM with a structured prompt.

### 4.7 **[MEDIUM]** No Audit Trail for AI-Generated Response Edits

**Root cause:** When the validator retries with a modified directive or falls back to `_STATIC_FALLBACK`, no audit record distinguishes AI-generated from fallback responses.  
**Affected files:** `backend/services/conversation_engine/orchestrator.py`  
**Risk:** Difficult to audit AI behavior post-incident; no visibility into how often fallbacks are triggered per tenant.  
**Fix:** Persist a `response_type` field (`ai_generated`, `fallback`, `injection_blocked`) in the `messages` table.

---

## 5. DATABASE ARCHITECTURE REVIEW

### 5.1 **[CRITICAL]** No Migration Tool — Schema Applied at Startup via Raw SQL

**Root cause:** `backend/sql_schema.sql` is mounted as `10-monolith-schema.sql` in the PostgreSQL Docker container's `docker-entrypoint-initdb.d/`. Subsequent schema changes use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` appended to the same file. There is no Alembic migration history. `alembic==1.16.5` is in `requirements.txt` but no `alembic.ini` or `migrations/` directory exists.  
**Affected files:** `backend/sql_schema.sql`, `docker-compose.yml:86–87`  
**Risk:** In production (RDS), `docker-entrypoint-initdb.d/` is not used. Operators must manually apply schema changes. No rollback capability. `ADD COLUMN IF NOT EXISTS` statements at the bottom of a 4,167-line file are executed on every startup against a live database (via `bootstrap_sql_schema`), which can cause lock contention on large tables.  
**Fix:** Initialize Alembic with the current schema as baseline migration; gate new `ALTER TABLE` changes behind versioned migration files. Remove the startup `bootstrap_sql_schema` call from production paths.

### 5.2 **[CRITICAL]** PostgreSQL Role Security — App Role Likely Has `BYPASSRLS` or Superuser

**Root cause:** `backend/shared/database.py:264–279` checks whether the connected DB role has `rolsuper` or `rolbypassrls` and logs a warning in non-production, but **raises a `RuntimeError`** only in production with `ALLOW_INSECURE_DB_ROLE=false`. In development (the `.env.example` default `ENVIRONMENT=development`), the app silently runs with a potentially over-privileged role. The schema bootstrap creates all tables as the app user — that user must have `CREATE TABLE` privileges, typically meaning it is a superuser or has DDL rights.  
**Affected files:** `backend/shared/database.py` (lines 264–280), `.env.example:255`  
**Risk:** If the PostgreSQL user has `BYPASSRLS`, all row-level security policies are silently bypassed, enabling cross-tenant data access.  
**Fix:** Create a least-privilege app role without `BYPASSRLS` or superuser. Create a separate migration role for DDL. Document the role setup in deployment guides.

### 5.3 **[HIGH]** `embeddings` Table Uses `ivfflat` Index Requiring Periodic Reindex

**Root cause:** IVFFlat indexes must be rebuilt when the data distribution changes significantly (standard recommendation: rebuild after 10% data change).  
**Affected files:** `backend/sql_schema.sql:1392`  
**Risk:** Degrading embedding recall over time as more products and KB articles are added without reindexing. No scheduled `REINDEX` job exists.  
**Fix:** Switch to HNSW (`USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)`) which updates incrementally, or schedule periodic `REINDEX CONCURRENTLY`.

### 5.4 **[HIGH]** Schema Migrations Are Not Idempotent for Destructive Changes

**Root cause:** `backend/sql_schema.sql:738` contains `ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check` followed by `ADD CONSTRAINT orders_status_check`. This runs on every startup call to `bootstrap_sql_schema`.  
**Affected files:** `backend/sql_schema.sql` (lines 738–740)  
**Risk:** Dropping and recreating constraints on startup requires a table lock. On a live production database with active write traffic, this causes deadlocks or delayed request processing.  
**Fix:** Move constraint modifications to versioned Alembic migrations gated by a migration state check.

### 5.5 **[HIGH]** Missing Indexes on High-Cardinality Filter Columns

**Root cause:** Several `WHERE` clauses in common queries lack supporting indexes:
- `conversations` filtered by `last_message_at` has an index but sorting by `updated_at DESC` uses a separate index — combined sorts may not use the index efficiently.
- `messages` table: no index on `(conversation_id, created_at)` visible in the schema review for message history queries.
- `sentiment_logs`: no index on `(company_id, created_at)` for analytics aggregations.  
**Affected files:** `backend/sql_schema.sql`  
**Risk:** Full table scans on high-volume tables under multi-tenant load.  
**Fix:** Add composite indexes targeting the actual query patterns used in the routers.

### 5.6 **[MEDIUM]** PostgreSQL SSL Disabled by Default (`POSTGRES_SSLMODE:-disable`)

**Root cause:** `docker-compose.yml:4–5` default to `POSTGRES_SSLMODE: ${POSTGRES_SSLMODE:-disable}`.  
**Affected files:** `docker-compose.yml`  
**Risk:** Data in transit between application services and PostgreSQL is unencrypted. On a shared host or compromised Docker network, credentials and customer data are exposed.  
**Fix:** Set `POSTGRES_SSLMODE=require` for all non-local environments; document required server-side cert setup.

### 5.7 **[MEDIUM]** Redis Has No `maxmemory` Limit Set

**Root cause:** `docker-compose.yml:98` sets `--maxmemory-policy noeviction` but does not set `--maxmemory`. Without a maxmemory limit, Redis will consume all available container memory until the host OOM killer intervenes.  
**Affected files:** `docker-compose.yml`  
**Risk:** Redis OOM crash causes cascading failure across all 13 services that depend on it for rate limiting, caching, and background queues.  
**Fix:** Set `--maxmemory 512mb` (or appropriate value) alongside `noeviction` (or change to `allkeys-lru` for cache-only Redis instances on separate databases).

---

## 6. DEVOPS REVIEW

### 6.1 **[CRITICAL]** GCP Service Account Key File Bind-Mounted from Host (`./backend/secrets/`)

**Root cause:** `docker-compose.yml` mounts `./backend/secrets/gcp-vertex-sa.json` into 6 services. The `backend/secrets/` directory is gitignored but is a plain file on the host filesystem.  
**Affected files:** `docker-compose.yml` (lines 243, 291, 323, 355, 520, 552)  
**Risk:** The service account JSON key file is stored as a cleartext file on the host. Any process with filesystem access or any Docker volume escape can read the credentials. In a CI/CD pipeline, secrets can be accidentally copied into build artifacts.  
**Fix:** Use AWS Secrets Manager or GCP Secret Manager (Workload Identity Federation) in production. In Docker Compose for local dev, use Docker secrets or environment variable injection. Remove the file bind-mount pattern entirely from the production compose configuration.

### 6.2 **[CRITICAL]** No Container Resource Limits Defined

**Root cause:** `docker-compose.yml` has no `deploy.resources.limits` for any service. The WhatsApp bridge uses `shm_size: '256m'` for Chromium but no memory/CPU limit.  
**Affected files:** `docker-compose.yml`  
**Risk:** A memory leak or traffic spike in any single service (e.g., WhatsApp bridge running Chromium, or the fastembed model in AI service) can exhaust host memory and take down the entire stack via OOM.  
**Fix:** Add `deploy.resources.limits.memory` and `deploy.resources.limits.cpus` for each service. Typical allocations: gateway 512MB, AI service 1GB, WhatsApp bridge 1GB, other services 256–512MB.

### 6.3 **[CRITICAL]** CI Pipeline Does Not Run Tests

**Root cause:** `.github/workflows/ci.yml` contains four jobs: lint, syntax validation, Docker Compose validation, and a basic secret scan. None of the 371 test functions are executed. The test infrastructure (pytest, asyncio) is in `requirements.txt` but no CI job runs `pytest`.  
**Affected files:** `.github/workflows/ci.yml`  
**Risk:** Code changes that break business logic, auth flows, or AI pipeline behavior are merged without automated regression checks.  
**Fix:** Add a `test` CI job using a `postgres:16` and `redis:7` service container; run `pytest backend/tests/ -x --timeout=60`.

### 6.4 **[HIGH]** Services Start Without `--workers` Parameter — Single-Process Uvicorn

**Root cause:** All service Dockerfiles use bare `uvicorn ... --host 0.0.0.0 --port XXXX` without `--workers`. FastAPI with asyncpg is I/O-bound but a single process cannot utilize multiple CPU cores.  
**Affected files:** All `Dockerfile` files under `backend/services/*/`  
**Risk:** Under concurrent load, a single-process service becomes CPU-bound on JSON serialization and Python GIL-heavy operations (e.g., bcrypt, fastembed). The gateway proxy timeout is 60s, which is easily exhausted.  
**Fix:** Use `--workers 2` (or use `gunicorn -k uvicorn.workers.UvicornWorker`) in production Dockerfiles. Alternatively, use ECS task replicas with a single worker each.

### 6.5 **[HIGH]** Media Files Stored on Local Container Filesystem

**Root cause:** `backend/services/media_storage.py` stores uploads to `UPLOAD_STORAGE_DIR` (default `/app/uploads`) on the container's local filesystem. The `uploads-data` Docker volume provides persistence within a single-host Docker deployment.  
**Affected files:** `backend/services/media_storage.py`, `docker-compose.yml`  
**Risk:** In a multi-replica deployment (ECS, Kubernetes), each replica has its own volume, causing files uploaded to replica A to be unavailable on replica B. No AWS S3 integration exists.  
**Fix:** Implement S3-backed storage with presigned URLs. `boto3==1.42.86` is already in `requirements.txt` — create an S3 adapter behind the existing `store_media_bytes` / `serve_stored_media` interface.

### 6.6 **[HIGH]** No Docker Log Size Limits

**Root cause:** `docker-compose.yml` has no `logging` configuration on any service. Services emit structured JSON logs to stdout continuously.  
**Affected files:** `docker-compose.yml`  
**Risk:** Unbounded log growth can fill the host disk. The default Docker JSON file log driver accumulates logs without rotation.  
**Fix:** Add `logging: driver: "json-file" options: max-size: "50m" max-file: "3"` to the compose defaults.

### 6.7 **[MEDIUM]** Health Checks Use `python -c "import urllib.request"` — Fragile and Slow

**Root cause:** All Python service health checks spawn a full Python interpreter subprocess every 15 seconds (`python -c "import urllib.request; urllib.request.urlopen(...)"`) instead of using `curl` or `wget`.  
**Affected files:** `docker-compose.yml` (lines 180–183, 203–206, etc.)  
**Risk:** Each health check spawns a Python process (50–100ms startup) consuming CPU. Under container orchestration with many replicas, this causes noticeable overhead. Python may fail to import if the container is in a degraded state, making the health check report a false negative.  
**Fix:** Use `wget -qO /dev/null http://127.0.0.1:PORT/health || exit 1` (consistent with the `whatsapp-bridge` service health check already using this approach).

### 6.8 **[MEDIUM]** No Graceful Shutdown Handling

**Root cause:** Services are started with plain `uvicorn ... CMD` with no `--timeout-graceful-shutdown`. SIGTERM handling relies on uvicorn's default which may abandon in-flight requests.  
**Affected files:** All service Dockerfiles, `backend/shared/app_factory.py`  
**Fix:** Add `--timeout-graceful-shutdown 30` to uvicorn commands and ensure `@app.on_event("shutdown")` handlers in `app_factory.py` properly close DB pools and Redis connections.

---

## 7. CLOUD ARCHITECTURE REVIEW (AWS)

### 7.1 **[HIGH]** Architecture Not Ready for Multi-Instance Deployment Without S3 Migration

**Current state:** Local volume for media storage prevents horizontal scaling.  
**Required change:** Migrate to S3 before deploying to ECS with `desired_count > 1`.  
**AWS service:** Amazon S3 + CloudFront CDN for media delivery; IAM role for service access.

### 7.2 **[HIGH]** No External Secrets Management

**Current state:** All secrets are in `.env` files or host-mounted JSON files.  
**Required change:** Move all secrets to AWS Secrets Manager or Parameter Store.  
**AWS service:** AWS Secrets Manager (`secretsmanager`), IAM roles for ECS task definitions, `aws-secretsmanager-caching-client-python` for Python SDK.

### 7.3 **[HIGH]** Single PostgreSQL Instance — No Read Replica or Failover

**Current state:** Single `pgvector/pgvector:pg16` container.  
**Required change:** Deploy RDS PostgreSQL 16 with Multi-AZ for automatic failover and at least one read replica for analytics queries.  
**AWS service:** Amazon RDS for PostgreSQL (Multi-AZ), `pgvector` extension enabled on RDS.  
**Note:** pgvector is available on RDS PostgreSQL 15+. Verify `vector` extension support with the chosen RDS instance class.

### 7.4 **[HIGH]** No CDN for Frontend Static Assets

**Current state:** nginx serves static React bundle directly on port 3000.  
**Required change:** Build frontend to S3, serve via CloudFront with SPA routing support (`/index.html` fallback).  
**AWS service:** S3 (static hosting) + CloudFront (CDN), ACM certificate for HTTPS.

### 7.5 **[HIGH]** WhatsApp Bridge (Chromium/Puppeteer) is Not Suitable for ECS Fargate

**Current state:** `whatsapp-bridge` service uses Puppeteer with Chromium, requiring `shm_size: 256m` and persistent session state in a volume (`whatsapp-bridge-auth`).  
**Required change:** Chromium requires either ECS EC2 launch type (not Fargate) or a dedicated browser automation service. Session persistence requires EFS or a single-task pinned deployment.  
**AWS service:** ECS EC2 launch type with dedicated instance for the bridge, EFS for session persistence, or migrate to Meta Cloud API (avoid the bridge entirely).

### 7.6 **[MEDIUM]** No WAF or DDoS Protection

**Current state:** Rate limiting is implemented at the gateway (Redis-backed), but no AWS WAF, Shield, or geographic filtering is present.  
**Required change:** Deploy AWS WAF on the ALB with managed rule groups (AWSManagedRulesCommonRuleSet, AWSManagedRulesBotControlRuleSet).  
**AWS service:** AWS WAF v2, AWS Shield Standard (included with ALB).

### 7.7 **[MEDIUM]** No VPC Network Isolation

**Current state:** All services communicate on a flat Docker network (`pulse-network`).  
**Required change:** Deploy into a VPC with private subnets for backend services, public subnets only for ALB. Database in isolated subnet with security group allowing only ECS task security groups.  
**AWS service:** VPC, private/public subnets across 2 AZs, security groups, VPC endpoints for S3/Secrets Manager.

### 7.8 **[MEDIUM]** Estimated Monthly AWS Cost (Minimal Production)

| Resource | Type | Est. Monthly Cost |
|---|---|---|
| ECS Fargate (13 services, 0.5 vCPU / 1GB each) | 2 replicas each | ~$450 |
| RDS PostgreSQL 16 Multi-AZ (db.t4g.medium) | 100GB gp3 | ~$180 |
| ElastiCache Redis (cache.t4g.medium, Multi-AZ) | | ~$80 |
| ALB | | ~$20 |
| CloudFront (10GB/mo) | | ~$10 |
| S3 (100GB) | | ~$5 |
| Secrets Manager | 20 secrets | ~$5 |
| NAT Gateway | | ~$35 |
| **Total estimated** | | **~$785/mo** |

---

## 8. SECURITY REVIEW

### 8.1 **[CRITICAL]** Unsigned Web Chat Webhook Allows Unauthenticated Message Injection

**Vulnerability:** `ALLOW_UNSIGNED_WEB_CHAT_WIDGET=true` by default bypasses HMAC signature verification on the `/api/webhooks/web-chat` endpoint.  
**Exploit scenario:** An attacker POSTs fabricated messages to any company's web-chat endpoint, causing the AI engine to generate and send responses, consume AI token quota, and potentially expose conversation history.  
**Business impact:** AI cost abuse, conversation data poisoning, customer experience damage.  
**Remediation:** Set `ALLOW_UNSIGNED_WEB_CHAT_WIDGET=false` as default and require `WEB_CHAT_WIDGET_KEY` in production. Enforce HMAC-SHA256 on all web-chat webhooks.

### 8.2 **[CRITICAL]** GCP Service Account Key Stored as Plaintext File on Host

**Vulnerability:** `./backend/secrets/gcp-vertex-sa.json` is bind-mounted from the host into 6 Docker containers.  
**Exploit scenario:** Any process with host filesystem access, misconfigured Docker socket, or container escape can read the service account key and make arbitrary Google Cloud API calls (Vertex AI, billing).  
**Business impact:** Unauthorized AI API access, potential GCP account compromise, uncontrolled billing.  
**Remediation:** Use Workload Identity Federation or AWS Secrets Manager to inject GCP credentials at runtime. Never store service account JSON files on the host.

### 8.3 **[CRITICAL]** `.gitignore:11` Pattern `*.json` — Risk of Accidentally Committing JSON Credentials

**Vulnerability:** While `*.json` is gitignored (preventing future JSON commits), the pattern is overly broad and any whitelisting exceptions (e.g., via `!package.json`) may be incomplete. More critically, the gitignore comment `# Environment and credentials` groups `*.json` with credential files, suggesting the intent was to prevent credential leaks.  
**Exploit scenario:** If someone removes the `*.json` line to commit a required JSON config, credential files could accidentally be committed.  
**Remediation:** Use specific patterns (`*-key.json`, `*-credentials.json`, `service-account*.json`) rather than `*.json`. Add `gitleaks` pre-commit hook.

### 8.4 **[CRITICAL]** Weak Secret Default Values Detected in `.env.example`

**Vulnerability:** `.env.example` contains `change-me-*` default values that users may deploy verbatim: `JWT_SECRET=change-me-to-a-random-32-plus-character-secret`, `META_WEBHOOK_SECRET=change-me-meta-webhook-secret`, `WHATSAPP_BRIDGE_SECRET=change-me-whatsapp-bridge-secret`, `SECRET_KEY=change-me-shared-secret-key`.  
**Exploit scenario:** If deployed with defaults, an attacker can forge JWT tokens, spoof Meta webhooks, and impersonate the WhatsApp bridge.  
**Business impact:** Complete authentication bypass, account takeover.  
**Remediation:** The `shared/config.py:require_secret()` function does check for weak secrets in production (`is_production()` check), but only for JWT and INTERNAL_SERVICE_SECRET. Extend weak-secret checks to all security-critical secrets. Add a startup pre-flight that enumerates all secrets against `_WEAK_SECRET_MARKERS`.

### 8.5 **[HIGH]** CORS Wildcard Default (`CORS_ORIGINS="*"`) in `core/config.py`

**Vulnerability:** `backend/core/config.py:25` defaults `CORS_ORIGINS` to `"*"`.  
**Exploit scenario:** A phishing page can make credentialed cross-origin requests from a victim's browser to the API, stealing data or performing actions.  
**Remediation:** Remove the `"*"` default. Require explicit origin configuration.

### 8.6 **[HIGH]** JWT Token Version Check is Not Enforced at Gateway Level

**Vulnerability:** The gateway (`api_gateway/app.py`) decodes and validates JWT claims including `cid`, `role`, `ev`, `ob`, `pl` but does **not** check `tv` (token_version) against the database. The `token_version` check happens only in `services/db_helpers.py:resolve_refresh_token_rotation` during the refresh flow.  
**Affected files:** `backend/api_gateway/app.py`, `backend/shared/auth/jwt.py`  
**Exploit scenario:** After a password change or account suspension that bumps `token_version`, an old access token remains valid until its `exp`. The gateway blacklist check (`is_token_blacklisted` via Redis) covers explicit revocations, but token version bumps without explicit JTI blacklisting leave a window of 15–60 minutes.  
**Remediation:** On password change or account status change, add the current JTI to the blacklist in Redis in addition to bumping `token_version`.

### 8.7 **[HIGH]** Internal Service Headers Can Be Injected by External Clients

**Vulnerability:** `backend/shared/auth/dependencies.py` trusts `X-Company-Id`, `X-User-Id`, `X-User-Role` headers if `X-Internal-Service-Secret` is valid. The gateway (`api_gateway/app.py:172–178`) strips these headers from external requests via `STRIPPED_EXTERNAL_HEADERS`. However, if any downstream service is directly exposed (e.g., `customer:8003` is exposed on `ports: - "8003:8003"` in `docker-compose.yml:237–238`), an external client can bypass the gateway and inject arbitrary company_id.  
**Affected files:** `docker-compose.yml` (line 237), `backend/shared/auth/dependencies.py`  
**Remediation:** Remove direct port exposure from all services except `gateway`, `frontend`, and optionally `super-admin`. In production, place all services in private subnets accessible only via the ALB/gateway.

### 8.8 **[HIGH]** WhatsApp Bridge Secret Transmitted as HTTP Header Without TLS

**Vulnerability:** The WhatsApp bridge calls `PYTHON_BACKEND` (default `http://gateway:8000`) with `WHATSAPP_BRIDGE_SECRET` in an HTTP header over an unencrypted Docker network connection.  
**Affected files:** `backend/whatsapp_bridge/bridge.js`, `docker-compose.yml`  
**Risk:** On a compromised host, a network sniffer can capture the bridge secret, enabling replay attacks.  
**Remediation:** Enable TLS for inter-service communication or use mTLS. In the short term, ensure Docker network is isolated and not shared with untrusted containers.

### 8.9 **[MEDIUM]** No Audit Log for Sensitive Admin Operations

**Vulnerability:** The super-admin service can block tenants, approve accounts, change billing status. These operations are logged to `system_logs` table but there is no separate, immutable audit trail for privileged operations.  
**Remediation:** Write sensitive admin actions to an append-only audit log table with no `UPDATE`/`DELETE` permissions for the app user.

### 8.10 **[MEDIUM]** `ALLOW_INSECURE_DB_ROLE=false` Default Does Not Catch Development Deployments

**Vulnerability:** `ALLOW_INSECURE_DB_ROLE` check only raises in `is_production()` mode. Development deployments with superuser DB roles are silently accepted.  
**Remediation:** Always log a warning at startup about the DB role privilege level regardless of environment.

---

## 9. BUG INVENTORY

| # | Severity | Component | Bug Description | File:Line | Fix |
|---|---|---|---|---|---|
| 1 | Critical | Web Chat | `ALLOW_UNSIGNED_WEB_CHAT_WIDGET` defaults to `true`, bypassing signature check | `backend/routers/webhooks.py:1501`, `.env.example:337` | Change default to `false` |
| 2 | Critical | CORS | `core/config.py` defaults `CORS_ORIGINS` to `"*"` | `backend/core/config.py:25` | Remove wildcard default |
| 3 | High | Customer Service | Customer service exposes port 8003 directly, bypassing gateway auth | `docker-compose.yml:237–238` | Remove direct port exposure |
| 4 | High | Super Admin Service | Super admin service port 8011 exposed directly | `docker-compose.yml:501–502` | Restrict to private network only in production |
| 5 | High | Auth | JWT token version not checked at gateway level after password change | `backend/api_gateway/app.py` (missing tv check) | Add JTI blacklist on password change |
| 6 | High | Database | `bootstrap_sql_schema` runs `DROP CONSTRAINT / ADD CONSTRAINT` on every startup | `backend/sql_schema.sql:738–740` | Gate behind migration state check |
| 7 | High | AI Service | `fastembed` model called synchronously in async context | `backend/services/ai_service/local_ml.py:199–200` | Wrap in `asyncio.to_thread()` |
| 8 | High | Security | QA preview origin hardcoded in production CORS allowlist | `backend/shared/config.py:17`, `backend/core/config.py:29–31` | Remove from default lists |
| 9 | Medium | Database | Redis `--maxmemory` not set, `noeviction` policy with unlimited memory | `docker-compose.yml:98` | Set `--maxmemory 512mb` |
| 10 | Medium | Frontend | `pe_user` object stored in localStorage, exposing user role and company_id to XSS | `frontend/src/contexts/AuthContext.js:57` | Store only display fields |
| 11 | Medium | DevOps | Services start with single Uvicorn worker, blocking multi-core utilization | All service `Dockerfile` files | Add `--workers 2` |
| 12 | Medium | Database | PostgreSQL SSL disabled by default in Docker Compose | `docker-compose.yml:4` | Default to `sslmode=require` |
| 13 | Medium | AI | Prompt injection guard missing common jailbreak patterns | `backend/services/conversation_engine/prompt_builder.py:40–46` | Expand pattern list |
| 14 | Medium | Infrastructure | Docker logs have no size limits; uncontrolled disk growth | `docker-compose.yml` | Add `logging.driver` config |
| 15 | Medium | Frontend | No React error boundary; unhandled errors crash entire SPA | `frontend/src/App.js` | Add `<ErrorBoundary>` wrapper |
| 16 | Low | Security | `.gitignore:11` uses broad `*.json` pattern | `.gitignore:11` | Use specific credential file patterns |
| 17 | Low | DevOps | Health checks spawn full Python interpreter (slow, wasteful) | `docker-compose.yml` (multiple lines) | Use `wget` instead |
| 18 | Low | Database | `ivfflat` vector index requires periodic manual reindex | `backend/sql_schema.sql:1392` | Switch to HNSW |
| 19 | Low | AI | AI profanity filter contains only 5 words | `backend/services/conversation_engine/validator.py:29` | Extend wordlist significantly |
| 20 | Low | Analytics | In-memory metrics counters (`shared/metrics.py`) reset on restart, no Prometheus export | `backend/shared/metrics.py` | Integrate prometheus-client |

---

## 10. PRODUCTION READINESS ASSESSMENT

### Category Scores (0–10)

| Category | Score | Rationale |
|---|---|---|
| Full-Stack Engineering | 6/10 | Solid routing, auth gates, and state management; penalized for SELECT * prevalence, missing error boundary, unbounded queries |
| AI Systems | 6/10 | Sophisticated pipeline with grounding, injection guard, provider fallback; penalized for unsigned webhook default, incomplete injection patterns |
| Database Architecture | 5/10 | Strong RLS model, good indexing; penalized for no migration tool, schema on startup, SSL off by default |
| DevOps / Infrastructure | 4/10 | Structured logging and health checks present; penalized for no tests in CI, no resource limits, no S3, no log rotation |
| Cloud Architecture | 3/10 | Nothing is yet cloud-native; Chromium bridge incompatible with Fargate, media on local disk, no VPC design |
| Security | 5/10 | Strong: bcrypt passwords, JTI blacklist, RLS, HMAC webhooks, JWT with weak-secret detection; penalized for 4 critical vulnerabilities |

### Deployment Blockers (MUST FIX Before Production)

1. **BUG-1:** Set `ALLOW_UNSIGNED_WEB_CHAT_WIDGET=false` by default and enforce HMAC on web-chat webhook.
2. **BUG-2:** Remove CORS wildcard default from `core/config.py`.
3. **BUG-3:** Remove direct port exposure for `customer:8003` and `super-admin:8011` from production compose.
4. **GCP SA key:** Replace file bind-mount with Secrets Manager or Workload Identity Federation.
5. **CI tests:** Add `pytest` job to CI to catch regressions before merge.
6. **Schema migrations:** Replace startup `bootstrap_sql_schema` with Alembic for production deployments against RDS.
7. **DB role:** Ensure PostgreSQL app role does not have `BYPASSRLS` or superuser privileges before first production deployment.

### Conditional Items (Fix Within 30 Days)

1. Implement S3 media storage to enable horizontal scaling.
2. Add container resource limits to all services.
3. Set `POSTGRES_SSLMODE=require` in non-local environments.
4. Set Redis `--maxmemory` with appropriate limit.
5. Expand prompt injection detection patterns.
6. Add Docker log rotation configuration.
7. Remove `qa-finalize.preview.emergentagent.com` from production CORS allowlist.
8. Migrate health checks from Python subprocess to `wget`.
9. Add React `ErrorBoundary` around app routes.
10. Move `pe_user` data out of `localStorage`.

---

## 11. AWS DEPLOYMENT GUIDE

### Prerequisites

- AWS CLI configured with deployment IAM role
- Docker Desktop with buildx
- ECR repositories created for each service
- Domain name with Route 53 hosted zone
- ACM certificate issued for the domain
- AWS Secrets Manager access

### Step 1: Infrastructure Setup (Terraform or Console)

```
VPC: 10.0.0.0/16
  Public subnets:  10.0.1.0/24, 10.0.2.0/24  (ALB)
  Private subnets: 10.0.3.0/24, 10.0.4.0/24  (ECS tasks)
  Isolated subnet: 10.0.5.0/24, 10.0.6.0/24  (RDS, ElastiCache)

Internet Gateway → Public subnets
NAT Gateway (one per AZ) → Private subnets

Security Groups:
  sg-alb:     inbound 443 from 0.0.0.0/0
  sg-gateway: inbound 8000 from sg-alb
  sg-services: inbound 8001-8013 from sg-gateway, sg-services
  sg-rds:     inbound 5432 from sg-services
  sg-redis:   inbound 6379 from sg-services
```

### Step 2: Database (RDS PostgreSQL 16)

```bash
# Create RDS instance
aws rds create-db-instance \
  --db-instance-identifier pulse-engine-prod \
  --db-instance-class db.t4g.medium \
  --engine postgres \
  --engine-version 16.3 \
  --master-username pulse_app \
  --master-user-password <from-secrets-manager> \
  --db-name pulse_engine \
  --vpc-security-group-ids sg-rds \
  --db-subnet-group-name pulse-isolated \
  --multi-az \
  --storage-type gp3 \
  --allocated-storage 100 \
  --storage-encrypted

# After creation, enable pgvector extension
psql -h <rds-endpoint> -U pulse_app -d pulse_engine \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"

# Run Alembic migrations (after implementing them)
alembic upgrade head
```

### Step 3: ElastiCache Redis

```bash
aws elasticache create-replication-group \
  --replication-group-id pulse-engine-redis \
  --replication-group-description "Pulse Engine Redis" \
  --cache-node-type cache.t4g.medium \
  --engine redis \
  --engine-version 7.0 \
  --num-cache-clusters 2 \
  --automatic-failover-enabled \
  --security-group-ids sg-redis \
  --subnet-group-name pulse-isolated
```

### Step 4: Secrets Manager Population

```bash
# Create secrets for each required variable
for secret in JWT_SECRET INTERNAL_SERVICE_SECRET POSTGRES_PASSWORD \
              GEMINI_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY \
              STRIPE_SECRET_KEY STRIPE_WEBHOOK_SECRET \
              META_WEBHOOK_SECRET WEB_CHAT_WEBHOOK_SECRET \
              WHATSAPP_BRIDGE_SECRET GOOGLE_CLIENT_SECRET FACEBOOK_APP_SECRET \
              DEFAULT_TENANT_API_KEY DEFAULT_TENANT_SALT TENANT_SALTS \
              IDENTITY_ADMIN_PASSWORD; do
  aws secretsmanager create-secret \
    --name "pulse-engine/prod/${secret}" \
    --secret-string "<value>"
done

# For GCP credentials (replace file mount):
aws secretsmanager create-secret \
  --name "pulse-engine/prod/GCP_SA_JSON" \
  --secret-string "$(cat backend/secrets/gcp-vertex-sa.json)"
```

### Step 5: ECR and ECS Setup

```bash
# Create ECR repositories
for svc in gateway auth user customer lead ai analytics product \
           notification agent-orchestrator identity super-admin \
           data-pipeline email-campaign frontend whatsapp-bridge; do
  aws ecr create-repository --repository-name pulse-engine/${svc}
done

# Build and push all images
export ECR_REGISTRY=<account-id>.dkr.ecr.<region>.amazonaws.com
aws ecr get-login-password | docker login --username AWS --password-stdin $ECR_REGISTRY

docker compose build
for svc in ...; do
  docker tag pulse-engine-${svc}:latest $ECR_REGISTRY/pulse-engine/${svc}:latest
  docker push $ECR_REGISTRY/pulse-engine/${svc}:latest
done

# Create ECS cluster
aws ecs create-cluster --cluster-name pulse-engine-prod
```

### Step 6: Service Deployment Order

Deploy in this order to respect dependency chains:

1. `postgres` (RDS) — already running
2. `redis` (ElastiCache) — already running
3. `identity` service
4. `auth` service
5. `user` service
6. `customer` service
7. `lead` service
8. `ai` service
9. `agent-orchestrator` service
10. `product` service
11. `analytics` service
12. `data-pipeline` service
13. `notification` service
14. `email-campaign` service
15. `super-admin` service
16. `gateway` service (last backend service)
17. `whatsapp-bridge` (EC2 launch type, separate task)
18. `frontend` (S3 + CloudFront, not ECS)

### Step 7: Environment Variable Mapping to Secrets Manager

In ECS task definitions, use `secrets` (not `environment`) for sensitive values:

```json
{
  "secrets": [
    {"name": "JWT_SECRET", "valueFrom": "arn:aws:secretsmanager:region:account:secret:pulse-engine/prod/JWT_SECRET"},
    {"name": "DATABASE_URL", "valueFrom": "arn:aws:secretsmanager:region:account:secret:pulse-engine/prod/DATABASE_URL"},
    ...
  ],
  "environment": [
    {"name": "ENVIRONMENT", "value": "production"},
    {"name": "POSTGRES_SSLMODE", "value": "require"},
    {"name": "ALLOW_UNSIGNED_WEB_CHAT_WIDGET", "value": "false"},
    {"name": "LOG_LEVEL", "value": "INFO"}
  ]
}
```

### Step 8: SSL/TLS and DNS

```bash
# Create ALB
aws elbv2 create-load-balancer \
  --name pulse-engine-alb \
  --subnets <public-subnet-1> <public-subnet-2> \
  --security-groups sg-alb

# Create HTTPS listener with ACM certificate
aws elbv2 create-listener \
  --load-balancer-arn <alb-arn> \
  --protocol HTTPS --port 443 \
  --certificates CertificateArn=<acm-cert-arn> \
  --default-actions Type=forward,TargetGroupArn=<gateway-tg-arn>

# Redirect HTTP → HTTPS
aws elbv2 create-listener \
  --load-balancer-arn <alb-arn> \
  --protocol HTTP --port 80 \
  --default-actions Type=redirect,RedirectConfig='{Protocol=HTTPS,StatusCode=HTTP_301}'

# DNS
aws route53 change-resource-record-sets \
  --hosted-zone-id <zone-id> \
  --change-batch '{"Changes":[{"Action":"UPSERT","ResourceRecordSet":{"Name":"api.yourdomain.com","Type":"A","AliasTarget":{"DNSName":"<alb-dns>","EvaluateTargetHealth":true,"HostedZoneId":"<alb-zone-id>"}}}]}'
```

### Step 9: Health Check Configuration

Each ECS task definition should configure health checks:

```json
{
  "healthCheck": {
    "command": ["CMD-SHELL", "wget -qO /dev/null http://localhost:PORT/health || exit 1"],
    "interval": 30,
    "timeout": 5,
    "retries": 3,
    "startPeriod": 60
  }
}
```

ALB target group health check: `GET /api/healthz`, 200 OK, threshold 2/2.

### Step 10: Rollback Procedure

```bash
# ECS rolling deployment (default) automatically rolls back on health check failure.
# For manual rollback:
aws ecs update-service \
  --cluster pulse-engine-prod \
  --service pulse-engine-gateway \
  --task-definition pulse-engine-gateway:<previous-revision>

# For database rollback (requires Alembic):
alembic downgrade -1

# For emergency: restore from RDS automated backup
aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier pulse-engine-prod \
  --target-db-instance-identifier pulse-engine-prod-restored \
  --restore-time <timestamp>
```

---

## 12. PRIORITIZED ROADMAP

### Week 1–2: Critical Fixes (Deployment Blockers)

| Priority | Task | Effort | Owner |
|---|---|---|---|
| P0 | Set `ALLOW_UNSIGNED_WEB_CHAT_WIDGET=false` default; enforce HMAC on web-chat | 2h | Backend |
| P0 | Remove `CORS_ORIGINS="*"` default from `core/config.py` | 1h | Backend |
| P0 | Remove direct port exposure for `customer:8003` and `super-admin:8011` from compose | 1h | DevOps |
| P0 | Replace GCP SA key file bind-mount with Secrets Manager or env injection | 4h | DevOps |
| P0 | Initialize Alembic with current schema as baseline; add migration workflow | 8h | Backend/DBA |
| P0 | Add `pytest` CI job with postgres+redis service containers | 4h | DevOps |
| P1 | Audit and fix PostgreSQL app role — create least-privilege role without BYPASSRLS | 4h | DBA |
| P1 | Expand prompt injection patterns in `prompt_builder.py` | 2h | AI |
| P1 | Remove `qa-finalize.preview.emergentagent.com` from default CORS lists | 1h | Backend |

### Month 1: High Priority Fixes

| Priority | Task | Effort |
|---|---|---|
| P1 | Implement S3 media storage adapter behind existing interface | 2 days |
| P1 | Add container resource limits (`memory`, `cpus`) to all docker-compose services | 2h |
| P1 | Set `POSTGRES_SSLMODE=require` in non-local envs; document RDS cert setup | 2h |
| P1 | Set Redis `--maxmemory 512mb` in docker-compose | 1h |
| P1 | Add Docker log rotation (`json-file`, max-size: 50m, max-file: 3) | 1h |
| P1 | Migrate health checks from Python subprocess to `wget` | 1h |
| P1 | Add `--workers 2` to production uvicorn commands | 2h |
| P1 | Wrap `fastembed` calls in `asyncio.to_thread()` | 2h |
| P2 | Add React `ErrorBoundary` component around app routes | 2h |
| P2 | Move `pe_user` sensitive fields out of `localStorage` | 4h |
| P2 | Add JTI blacklist on password change/role change | 4h |
| P2 | Add `--timeout-graceful-shutdown 30` to uvicorn commands | 1h |

### Month 2–3: Medium Priority Improvements

| Priority | Task | Effort |
|---|---|---|
| P2 | Implement Prometheus `/metrics` endpoint with `prometheus-client` | 1 day |
| P2 | Integrate Sentry SDK for error tracking across all services | 1 day |
| P2 | Replace `ivfflat` vector index with HNSW | 2h |
| P2 | Implement keyset pagination for conversations and leads lists | 2 days |
| P2 | Replace `SELECT *` with explicit column lists in high-frequency queries | 3 days |
| P2 | Implement S3-backed frontend deployment (CloudFront) | 1 day |
| P2 | Add OpenTelemetry trace export (Jaeger or AWS X-Ray) | 2 days |
| P3 | Extend AI profanity/safety filter | 1 day |
| P3 | Add audit log table for privileged admin operations | 1 day |
| P3 | Add `nginx.conf` security headers and connection limits | 2h |
| P3 | Add skeleton loading states to Inbox and Leads pages | 1 day |

### Quarter 2+: Long-Term Architectural Improvements

| Task | Description |
|---|---|
| Alembic full migration history | Convert all `ALTER TABLE` in `sql_schema.sql` to versioned Alembic migrations |
| Per-tenant embedding tables | Partition `embeddings` table by `company_id` for isolation and performance |
| WhatsApp Cloud API migration | Eliminate Puppeteer/Chromium bridge; migrate to Meta Cloud API for all WhatsApp |
| Separate read-replica routing | Route analytics queries to RDS read replica to reduce primary DB load |
| mTLS inter-service communication | Add mutual TLS between services instead of shared INTERNAL_SERVICE_SECRET |
| Horizontal scaling validation | Load test with k6 or Locust before enabling multi-replica ECS deployment |
| WAF deployment | AWS WAF with managed rule groups on ALB |
| Full VPC isolation | Deploy to private subnets with NAT gateway, VPC endpoints for AWS services |
| Distributed rate limiting review | Consider AWS API Gateway or Kong for centralized rate limiting at scale |
| Background worker isolation | Move follow-up scheduler from in-process (`server.py`) to dedicated ECS task |
