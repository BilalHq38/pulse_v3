# Pulse Engine Full QA Report

Date: 2026-05-31
Environment: local Docker, Windows host, Docker Compose project `pulse-v3`

## Executive Result

Status: PASS for the requested engine fix, 10-message QA run, local Docker health, production Compose validation, WhatsApp QR scope isolation, and post-conversation scheduler unit coverage.

Residual external validation: real RDS bootstrap, ElastiCache TLS connectivity, Stripe/OAuth/Brevo live calls, and Meta Business Manager webhook delivery still require real AWS/provider credentials and should be run in the deployed environment.

## Engine Issue

Issue reproduced from logs: `legacy_support_agent_message_path_executed reason=engine_opt_out` caused the support stage to bypass the new conversation engine and left the workflow waiting without a generated AI message.

Fixes applied:

- `backend/routers/conversations.py`: customer message workflow now uses `suppress_response_generation=True` so the orchestrator records workflow telemetry without stealing the response path from the conversation engine.
- `backend/routers/settings.py`: `ai_use_conversation_engine` is accepted and persisted through settings APIs.
- `backend/services/db_helpers.py`: company settings defaults now create new companies with `ai_use_conversation_engine=TRUE`.
- `backend/scripts/customer_service_qa_runner.py`: QA-created companies explicitly enable the conversation engine.

Verification:

- Rebuilt `customer` image and recreated the service.
- 10-message QA on rebuilt image: PASS 10, FAIL 0, WARN 0.
- Orchestrator log check: `legacy_support_agent_message_path_executed` count 0.
- Orchestrator log check: `conversation_engine_override` observed for all tested workflow turns.

Artifacts:

- `qa-artifacts/customer_service_qa_10_rebuilt_runner.log`
- `qa-artifacts/customer_service_qa_10_rebuilt_log.json`
- `qa-artifacts/orchestrator_10_rebuilt_engine_check.log`

## Docker Services

Result: PASS.

Final local stack status: all application containers reported healthy, including gateway, frontend, customer, auth, user, lead, AI, agent-orchestrator, analytics, data-pipeline, email-campaign, identity, notification, product, super-admin, Postgres, Redis, and WhatsApp bridge.

Public local ports:

- Frontend: `http://localhost:3000`
- Gateway/API: `http://localhost:8000`
- Customer/socket service: `http://localhost:8003`
- WhatsApp bridge: `http://localhost:3001`
- Postgres: `localhost:5435`

## Database

Result: PASS for local fresh-file verification.

- Consolidated file created: `database_complete.sql`
- Fresh local verification previously completed with zero SQL execution errors.
- Fresh verification artifact: `qa-artifacts/database_complete_verify.log`
- Current running local DB count after app/test activity: 142 public tables, 578 public indexes.

RDS validation is not run locally. The production guide includes the exact `psql -v ON_ERROR_STOP=1 "$DATABASE_URL" -f database_complete.sql` command to run against RDS.

## QA Runner

User-requested scope: 10 messages, then move on.

Result: PASS.

- Account/company setup: PASS
- Onboarding completion: PASS
- Billing plan selection: PASS
- Products seeded: 5
- Knowledge base entries seeded: 3
- FAQs seeded: 5
- Total messages: 10
- Passed: 10
- Failed: 0
- Warned: 0

The full 500-message run was not completed after the user narrowed the immediate test requirement to 10 messages.

## AI Pipeline Verification

Scoped 10-message web chat path:

- Inbound receipt: PASS
- Identity/conversation routing: PASS
- Capture intent classification: PASS
- Qualification scoring deferral: PASS
- Support response path via conversation engine override: PASS
- Legacy support bypass removed: PASS
- Response write/read by QA runner: PASS
- Analytics enqueue: PASS

Not fully exercised by the 10-message greeting/small-talk test:

- Complex RAG retrieval
- Vertex/Gemini LLM inference
- pgvector semantic retrieval
- Provider outbound delivery beyond web chat

## WhatsApp / Meta Hybrid

QR bridge multi-user isolation: PASS.

- 5 concurrent QA scopes were created.
- Initial check showed 5 unique scopes with no bleed.
- After bridge restart, all 5 QA scopes restored as `qr_required` with `qr_present=true`.
- Sanitized artifact: `qa-artifacts/whatsapp_bridge_qr_5_scope_authenticated_recheck.json`

Additional hardening:

- `backend/whatsapp_bridge/bridge.js` now logs transient Puppeteer `TargetCloseError`/protocol-close failures without terminating the bridge process.
- Hot-patched bridge container restarted and remained healthy.

Latency note:

- Scope creation returned quickly in the initial check, but actual QR readiness can take longer than the 2-second target because WhatsApp Web session initialization is sequential and browser-bound. Treat the under-2-second QR-generation target as not met in this local environment.

Meta API multi-user connectivity: NOT RUN.

- Requires real Meta Business Manager tokens, app webhooks, and reachable public HTTPS endpoint.
- Code/config paths were inspected, but live Meta webhook delivery was not possible locally.

Hybrid coexistence: PARTIAL.

- QR bridge isolation works.
- Meta side still requires real credentials to validate coexistence under load.

## Post-Conversation Flows

Result: PASS for scheduler logic and focused unit tests.

Changes applied:

- `backend/services/followup_scheduler/scheduler.py` now treats `delivered`, `completed`, `delivery_completed`, and `order_completed` as post-delivery feedback triggers.
- Delivery-completion aliases share a canonical idempotency key to avoid duplicate follow-ups for the same order.
- `confirmed` remains a separate `order_confirmed` follow-up path.
- `backend/services/followup_scheduler/sentiment.py` now returns `neutral` for empty feedback text before invoking local ML.
- `production docker-compose.yml` now includes a `followup-scheduler` worker service.
- Added `backend/services/followup_scheduler/Dockerfile`.

Verification:

- `.\.venv\Scripts\python.exe -m pytest backend/tests/test_wave4_followup_scheduler.py -q`
- Result: 28 passed.

## Production Deployment Files

Result: PASS.

Created/updated:

- `production docker-compose.yml`
- `.env.production.example`
- `infra/nginx/pulse-engine.prod.conf`
- `backend/services/followup_scheduler/Dockerfile`
- `docs/ec2-rds-elasticache-deployment.md`

Validation:

- `docker compose --env-file .env.production.example -f "production docker-compose.yml" config --quiet`: PASS
- Rendered service list includes `followup-scheduler`: PASS

Production architecture:

- Nginx is the only published container port.
- App services are internal-only via Docker network.
- RDS is used through `DATABASE_URL` with SSL required.
- ElastiCache Redis/Valkey is used through required Redis URLs.
- S3 is the production media backend.
- Google Vertex service account is mounted as a Docker secret.

## Syntax And Targeted Tests

PASS:

- Python compile for touched backend files.
- Node syntax check for `backend/whatsapp_bridge/bridge.js`.
- Production Compose config validation.
- Follow-up scheduler focused pytest suite: 28 passed.

Not run:

- Full backend pytest suite.
- Full 500-message QA run.
- Real AWS/provider integration tests.

## Integration Checks

Configuration-level status:

- Stripe: configured in env templates and production Compose; live webhook not tested.
- Google OAuth: localhost and production callback paths configured; live OAuth flow not tested.
- Facebook OAuth: localhost and production callback paths configured; live OAuth flow not tested.
- Brevo/SMTP: env wiring present; live email send not tested.
- Vertex AI: service-account mount and env wiring present; live model call not exercised by the 10 greeting messages.

## Deployment Guide

Full guide: `docs/ec2-rds-elasticache-deployment.md`

It covers:

- AWS VPC/security-group layout
- EC2 Docker setup
- RDS PostgreSQL bootstrap
- ElastiCache Redis/Valkey setup
- S3 media storage
- ALB HTTPS/DNS
- `.env.production` values
- Compose build/start/health checks
- OAuth, Stripe, Meta webhook URLs
- Smoke testing, updates, backup, rollback

Official docs referenced in the guide:

- Docker Engine on Ubuntu: https://docs.docker.com/engine/install/ubuntu/
- RDS PostgreSQL extensions/pgvector: https://docs.aws.amazon.com/AmazonRDS/latest/PostgreSQLReleaseNotes/postgresql-extensions.html
- ElastiCache in-transit encryption: https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/in-transit-encryption.html
- ALB HTTPS certificates: https://docs.aws.amazon.com/elasticloadbalancing/latest/application/https-listener-certificates.html

## Overall Recommendation

The project is ready for an EC2-based production deployment rehearsal using the new production Compose file, RDS, ElastiCache, S3, and ALB. Before declaring production complete, run the RDS bootstrap, provider credential checks, Meta webhook tests, and a full 500-message QA run inside the deployed environment.
