# Pulse Engine — project guide

This document describes how the repository is organized, how services talk to each other locally, and where to change behavior.

## High-level architecture

- **API gateway** — single public HTTP entry for the SPA and external clients; routes to backend microservices.
- **Microservices** — separate processes with their own DB schema (see `docker-compose.yml` `DB_SCHEMA` per service), typical domains: identity, auth, users, customers, leads, AI, analytics, notifications, products, data pipeline, agent orchestrator, super admin.
- **Postgres** — `pgvector/pgvector:pg16`; initialized with SQL under `backend/sql_schema.sql` and `backend/shared/db/bootstrap_microservices.sql`.
- **Redis** — caching, rate limiting, and background job queue URLs as defined in root `.env`.
- **Frontend** — React app in `frontend/`, served in Docker on port 3000 by default.

Compose project name is **`pulse-v3`** so multiple clones do not collide on default Docker project names.

## Repository layout

| Path | Role |
|------|------|
| `docker-compose.yml` | Service definitions, env injection, healthchecks |
| `setup.ps1` | Bootstrap: `.env`, optional venv repair, `pip install`, optional local `npm run build`, `docker compose up` |
| `start.ps1` | Fast path: stop (or full rebuild), Postgres/Redis first, then full stack; HTTP readiness checks |
| `backend/` | Python services, shared libraries, channel layer, routers, SQL |
| `frontend/` | React UI, chat widget, settings |
| `backend/whatsapp_bridge/` | Optional WhatsApp Web bridge (`node bridge.js`); reads **root** `.env` |
| `scripts/` | Auxiliary scripts (e.g. local uvicorn) |

## Configuration

### Root `.env`

All containers load `env_file: .env` from the repo root. Required keys are enforced in `docker-compose.yml` (for example `JWT_SECRET`, `DATABASE_URL`, `INTERNAL_SERVICE_SECRET`, tenant and webhook secrets).

`setup.ps1` generates secure random values for missing secrets and writes consistent `*_SERVICE_URL` values for the Docker network (`http://<service>:<port>`).

### Frontend local API URL

For `npm start` in `frontend`, use `frontend/.env.development` so the browser can reach the host-mapped gateway.

## Common tasks

### First machine setup

```powershell
.\setup.ps1
```

### Daily start

```powershell
.\start.ps1
```

### Clean slate (wipes Compose volumes including Postgres data)

```powershell
.\start.ps1 -FullRebuild
```

### Compose only (no PowerShell)

Ensure `.env` exists, then:

```bash
docker compose -p pulse-v3 -f docker-compose.yml --env-file .env up -d --build
```

## Development notes

- **AI and memory** — under `backend/services/ai_service/` and `backend/memory_engine/`.
- **Channels** — `backend/channel_layer/` normalizes inbound/outbound messages (web chat, WhatsApp, email, social adapters).
- **CI** — `.github/workflows/ci.yml` creates a minimal `.env` for validation steps.

## Security

Never commit real `.env` files or live API keys. Rotate any secret that was ever committed to history. Use your cloud provider’s secret store in production instead of flat files on disk.
