# Pulse Engine Production Readiness Report

Date: 2026-05-30

## Executive Summary

Pulse Engine is a multi-tenant CRM, commerce, messaging, analytics, identity-unification, and AI-assistance platform. The repository has been hardened substantially in this cycle, but it is **not yet approved for production launch**.

The main code-level security blockers addressed in this cycle were unsigned web-chat access, permissive production CORS behavior, host-published internal services, file-mounted GCP credentials, unbounded Redis memory, missing Docker log rotation and resource limits, local-only media storage, missing frontend error containment, sensitive browser persistence, prompt-injection handling, and absent CI test execution.

The remaining launch blockers are operational and migration-related:

1. Promote all remaining service startup DDL into ordered Alembic revisions and disable runtime DDL in production.
2. Provision AWS resources and validate the production task definitions, IAM policies, Secrets Manager injection, RDS TLS, ElastiCache TLS, S3 access, CloudFront behavior, ALB routing, autoscaling, alarms, backup restore, and disaster-recovery procedure.
3. Execute live PostgreSQL privilege verification against the production application role and migration role.
4. Separate background workers and schedulers into dedicated ECS services before horizontal scaling.

## Architecture

### Platform Topology

```text
Browser -> CloudFront -> ALB
                      -> frontend Nginx -> React SPA
                      -> API gateway -> internal FastAPI services
                      -> /socket.io -> customer realtime target

Internal services -> RDS PostgreSQL + pgvector
                  -> ElastiCache Redis
                  -> S3 media bucket -> CloudFront media origin
                  -> Stripe, Meta Graph API, SMTP/Brevo, OAuth providers, AI providers
```

The local Docker topology mirrors the service boundary: only `frontend` and `gateway` publish host ports. Realtime browser traffic is proxied through frontend Nginx to `customer:8003`.

### Frontend Architecture And Flows

The React SPA uses lazy-loaded pages, `AuthContext`, Axios API clients, Socket.IO updates, Zustand identity state, and reusable UI components. Primary flows are:

| Flow | Pages | Backend paths |
| --- | --- | --- |
| Signup, login, OAuth, verification | signup, signin, callback, onboarding | `/api/auth`, `/api/account`, `/api/billing` |
| CRM and inbox | dashboard, customers, leads, inbox, tickets | `/api/customers`, `/api/leads`, `/api/conversations`, `/api/tickets` |
| Commerce | products, product detail, orders | `/api/products`, `/api/public`, `/api/orders` |
| AI and knowledge | inbox AI, knowledge base, AI settings | `/api/ai`, `/api/knowledge-base`, `/api/orchestrator` |
| Campaigns and analytics | campaigns, analytics | `/api/campaigns`, `/api/analytics` |
| Identity unification | unification, settings | `/api/identity`, `/api/unification`, `/api/consent` |
| Administration | super-admin dashboard | `/api/admin`, `/api/platform/super-admin` |

Security changes: access tokens stay in memory, refresh state stays in HttpOnly cookies, user/profile/browser drafts moved from `localStorage` to session-scoped storage, and a global React error boundary prevents blank-screen failures.

### Backend Service Inventory

| Service | Purpose | Dependencies | Inputs and outputs | Database and AI | External integrations |
| --- | --- | --- | --- | --- | --- |
| `gateway` | Public HTTP routing, CORS, rate limiting, trusted internal headers | Redis, all internal APIs | Browser/webhook requests -> proxied responses | No business writes | ALB |
| `auth` | Login, refresh, OAuth, verification, invitations, revocation | PostgreSQL, Redis | Credentials/cookies -> JWT and session state | Auth tables, token blacklist | Google/Facebook OAuth, email |
| `user` | Settings, billing-facing user APIs, security management | PostgreSQL, Redis | Authenticated CRUD -> user/company state | Users, companies, subscriptions | Stripe through shared helpers |
| `customer` | Customers, inbox, conversations, tickets, orders, webhooks, Socket.IO | PostgreSQL, Redis, bridge, identity, orchestrator | Gateway calls and channel events -> CRM state and realtime events | CRM tables; invokes AI workflow | Meta, WhatsApp bridge, IMAP |
| `lead` | Leads, scoring, nurture drafts, conversion | PostgreSQL, AI/orchestrator | Lead CRUD -> scored and nurtured leads | Lead tables; AI scoring and nurture | Messaging channels |
| `product` | Catalog, knowledge data, media, public pages | PostgreSQL, S3 | Product CRUD/uploads -> catalog and public product views | Product and KB tables | S3, CloudFront |
| `ai` | LLM routing, RAG, embeddings, sentiment, safety, memory | PostgreSQL, Redis, provider APIs | Prompt/context -> grounded response and metadata | pgvector embeddings, memories | Gemini, OpenAI, Anthropic |
| `agent-orchestrator` | Workflow routing across capture, qualification, support, analytics | PostgreSQL, Redis, AI | Message/lead workflow -> state transitions | Workflow and memory tables | AI providers indirectly |
| `analytics` | Dashboard analytics and summaries | PostgreSQL | Query requests -> aggregates | Analytics tables | None required |
| `data-pipeline` | Raw event ingestion, ETL, rollups, scheduler | PostgreSQL, Redis | Raw events -> normalized metrics | Raw and metric tables | Channel events |
| `email-campaign` | Recipient resolution, AI copy generation, async sending | PostgreSQL, Redis, AI | Campaign config -> delivery records | Campaign tables; AI copy | SMTP/Brevo |
| `notification` | Notification CRUD and preferences | PostgreSQL | Events -> notifications | Notification tables | None required |
| `identity` | Identity matching, consent, fingerprints, merge review | PostgreSQL, Redis, optional vectors | Channel identities -> unified profiles | RLS-protected identity tables | Internal relay |
| `super-admin` | Platform controls, tenant status, audit views | PostgreSQL, Redis | Admin actions -> tenant/user controls | Cross-tenant admin policy path | None required |
| `whatsapp-bridge` | Linked-device WhatsApp runtime and webhook forwarder | Chromium, Redis-adjacent service topology, gateway | WhatsApp events -> signed gateway webhooks | No direct DB | WhatsApp Web |

### Database Architecture

PostgreSQL is the system of record. The schema includes companies, users, auth sessions, refresh tokens, billing, CRM entities, orders, products, knowledge base, messages, attachments, campaigns, analytics, AI memories, vector embeddings, identity mappings, consent, audit tables, and outbox/dead-letter tables.

Tenant isolation is enforced with PostgreSQL row-level security and `FORCE ROW LEVEL SECURITY`. Request middleware binds `app.current_company`, `app.current_tenant`, and platform-admin mode per acquired connection. Application startup rejects superuser or `BYPASSRLS` roles in production. The production role must still be verified on the deployed RDS instance.

Vector search uses pgvector cosine distance, tenant predicates, source predicates, unique source-chunk indexes, and an IVFFlat cosine index. Run `ANALYZE embeddings` after significant ingestion. Review IVFFlat recall and tune list/probe values once production cardinality is known; HNSW is a candidate if read latency and recall justify higher memory usage.

### AI Architecture

AI flow:

```text
Message -> lightweight routing -> context router -> RAG retrieval
        -> prompt builder -> provider gateway -> response validator
        -> safety sanitization -> persistence -> outbound delivery
```

The AI layer includes LLM orchestration, provider fallback, request budgets, timeouts, Redis-backed cooldowns, pgvector retrieval, keyword fallback, short- and long-term memory, summarization, sentiment, lead scoring, grounding validation, and response redaction.

Hardening added in this cycle:

- Retrieved context, history, style prompts, and user input are bounded and sanitized.
- Prompt-injection patterns now cover role-tag spoofing, delimiter escape attempts, hidden-prompt extraction, and override instructions.
- RAG vector queries remain tenant-scoped.
- Provider embedding clients use async APIs with explicit timeouts and cooldowns.
- RAG embedding seeding has a one-second budget and keyword fallback.

Residual AI risk: local MiniLM classifiers are synchronous and can consume event-loop time during cold model loading. Move model warmup and inference behind a dedicated worker or `asyncio.to_thread` boundary before high-concurrency launch.

### Identity Unification

The identity service maps channel identifiers, device fingerprints, and consent records to unified customer profiles. It supports merge review, split history, event outbox delivery, dead-letter handling, and tenant-scoped cache keys. Identity DB sessions set tenant context before queries and reject privileged DB roles in production.

### Billing

Stripe integration covers checkout, subscriptions, billing customers, usage ledger, webhook event deduplication, plan selection, and billing gates. Shared middleware checks account status, session revocation, and billing access before business routes. Validate live Stripe webhook signing, replay behavior, plan IDs, and dunning behavior in a Stripe test-mode deployment before production activation.

### Analytics And Campaigns

Analytics ingests raw events, messages, and leads into metric tables and daily rollups. Campaigns resolve recipients from CRM state, generate sanitized copy through the AI layer, send through tenant email configuration, and track recipient state with bounded concurrency.

### Security Architecture

Implemented controls:

- Explicit production CORS origins; wildcard and hardcoded QA origins removed.
- Web-chat unsigned access disabled by default and always rejected in production.
- Signed/replay-protected external webhooks and bridge authentication.
- Gateway-only internal HTTP boundary in Compose.
- JWT revocation, refresh-token rotation, session invalidation, and account-status revocation.
- Production DB TLS configuration guard.
- Production DB role superuser and `BYPASSRLS` rejection.
- S3 media backend with server-side encryption support.
- Redis memory cap and eviction policy.
- Docker log rotation and container resource ceilings.
- Memory-only access tokens and session-scoped browser persistence.

## Bug Resolution Report

| Blocker | Status | Evidence |
| --- | --- | --- |
| Secure web chat and disable unsigned default | Fixed | `ALLOW_UNSIGNED_WEB_CHAT_WIDGET=false`; production rejects unsigned widget calls |
| Explicit production CORS | Fixed | production requires configured origins; QA origin removed |
| Gateway-only internal access | Fixed locally | Compose host ports removed for PostgreSQL, bridge, customer, super-admin; Socket.IO proxied by Nginx |
| File-mounted cloud credentials | Fixed locally | GCP JSON bind mounts removed |
| CI tests | Fixed | GitHub Actions runs backend, frontend, bridge, syntax, Compose validation |
| Alembic | Partially fixed | baseline added; remaining startup DDL must become revisions |
| Tenant role privilege checks | Implemented, live verification pending | DB startup rejects superuser/`BYPASSRLS` in production |
| Prompt injection and RAG sanitization | Fixed | prompt builder sanitizes input, chunks, history, style |
| Async external embeddings | Fixed | provider async clients, timeout, cooldown, keyword fallback |
| Worker scaling | Partial | Redis queue exists; dedicated ECS worker services still required |
| Container resource limits | Fixed locally | Compose default CPU/memory limits |
| Cloud object storage | Fixed compatibility path | S3 backend and encrypted object writes added |
| Token invalidation | Existing controls verified by scan | blacklist, refresh revocation, account-status revocation |
| Encrypted DB communication | Fixed application path | production rejects non-TLS DB config; shared asyncpg DSNs receive `sslmode`; identity connections receive an SSL context |
| Redis memory growth | Fixed locally | `maxmemory` and `allkeys-lru` |
| Docker log rotation | Fixed locally | `json-file` rotation defaults |
| Sensitive frontend persistence | Fixed | auth/profile/chat drafts moved out of persistent local storage |
| Global frontend error boundary | Fixed | `GlobalErrorBoundary` |
| Vector search | Reviewed | tenant predicates, unique index, IVFFlat cosine index present |
| Redis-backed rate-limit outage handling | Fixed | Redis failures now swap to an in-process limiter instead of bypassing enforcement |
| Public-buy order state | Fixed | anonymous product-page purchases create `pending` orders rather than auto-confirmed orders |
| Duplicate order confirmation | Fixed | repeated confirmation remains idempotent even after the new-order time window |
| Conversation turn sequencing | Fixed | scalar memory lookup supports both full DB pools and lightweight adapters |
| Follow-up sentiment fallback | Fixed | keyword fallback activates when MiniLM is unavailable |

## Second Scan

The second repository scan confirmed:

- Only frontend and gateway publish Docker host ports.
- Unsigned web-chat access remains disabled by default.
- Hardcoded QA origins and file-mounted GCP credential references are absent.
- Sensitive frontend persistence was removed; remaining `localStorage` keys hold product UI categories and an AI exhaustion timestamp only.
- Runtime DDL still exists outside Alembic in pipeline, orchestrator, signup compatibility, PostgreSQL bootstrap, and AI-memory paths. This is the primary code-level production blocker.

## Deployment Readiness Audit

| Area | State | Notes |
| --- | --- | --- |
| Production security | Conditional | Code defaults hardened; AWS controls not deployed |
| Infrastructure security | Blocked | IAM, SGs, WAF, alarms, secret injection need deployment validation |
| Data security | Conditional | RLS and TLS guards exist; live RDS role verification required |
| Tenant isolation | Conditional | Schema uses forced RLS; run cross-tenant integration suite on RDS |
| AI safety | Conditional | improved sanitization; red-team prompt corpus still required |
| Billing enforcement | Conditional | middleware exists; Stripe test-mode end-to-end test required |
| Authentication and authorization | Conditional | revocation paths exist; run deployed session tests |
| Database integrity | Blocked | remaining runtime DDL extraction |
| Monitoring readiness | Blocked | CloudWatch dashboards, alarms, tracing export not provisioned |
| Backup readiness | Blocked | RDS/S3 backup policies and restore drill not executed |
| Disaster recovery | Blocked | restore runbook not exercised |
| Horizontal scaling | Blocked | schedulers and workers must become singleton/dedicated services |
| High availability | Blocked | Multi-AZ and multi-task AWS deployment not provisioned |

## Remaining Risks And Limitations

Critical blockers:

1. Runtime DDL remains in service startup paths for pipeline, orchestrator, PostgreSQL bootstrap, pending signup compatibility, and AI memory dedupe. Extract it into Alembic revisions.
2. AWS infrastructure has not been provisioned or tested.
3. RDS application-role privilege queries have not been run against the target environment.
4. Background schedulers need singleton ownership and workers need separate ECS scaling policies.

Warnings:

- S3 uploads use a server-side compatibility path. Move large uploads to presigned browser-to-S3 uploads to reduce API memory and latency.
- Local MiniLM inference needs worker isolation for predictable API latency.
- Frontend build reports one unused variable warning in `InboxPage.js`.
- Existing FastAPI `on_event` hooks should migrate to lifespan handlers.

## Verification Evidence

Executed locally on 2026-05-30:

| Check | Result |
| --- | --- |
| `docker compose -p pulse-v3 --env-file .env config --quiet` | Passed |
| Python compilation for changed backend modules | Passed |
| Hardening, media, RAG, embedding, workflow safety, and route-contract tests | `81 passed` |
| Cancellation, related-product retrieval, and production-hardening regression tests | `10 passed` |
| Complete backend suite | `358 passed`, `37 skipped`, `4 warnings` |
| Frontend tests | `14 suites`, `64 tests` passed |
| Frontend production build | Passed with one existing unused-variable warning |
| WhatsApp bridge tests | `28 tests` passed |
| `git diff --check` | Passed; line-ending warnings only |

The complete backend suite is green. The `37` skips include the retired legacy AI response-generator module: Wave 8 removed that function and dedicated deprecation coverage asserts that it stays absent.

Local Alembic CLI verification could not run because Windows denied access to the local virtualenv interpreter after the test suite completed. The migrator image installs Alembic from `requirements.txt`; run `alembic heads` and `alembic upgrade head` in the built migrator container during staging validation.

Capacity estimate before load testing:

| Layer | Conservative starting point | Scaling limit to validate |
| --- | --- | --- |
| Gateway/API ECS task | 2 vCPU, 4 GiB, 2 tasks | 100-250 lightweight requests/sec per task |
| AI/orchestrator task | 2-4 vCPU, 4-8 GiB | provider quotas and local model CPU |
| Worker tasks | 2 tasks per queue family | email/channel provider limits |
| RDS PostgreSQL | `db.t4g.large` or `db.r6g.large`, Multi-AZ | pool count, write IOPS, vector recall/latency |
| Redis | `cache.t4g.small` or larger, Multi-AZ | stream backlog and cache churn |

These are planning estimates, not measured guarantees. Run load tests with production-like data before launch.

## Final Recommendation

**NO-GO for production launch.**

Proceed to an AWS staging deployment using the companion guide. Production approval requires completion of the four critical blockers, a staging soak test, backup restore drill, cross-tenant isolation test, Stripe test-mode verification, AI red-team suite, and measured load test.
