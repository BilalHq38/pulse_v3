# Pulse Engine

Multi-tenant customer engagement stack: API gateway, microservices (auth, leads, AI, analytics, and others), Postgres with pgvector, Redis, and a React frontend. Local development is **Docker-first** on Windows, macOS, or Linux with Docker Compose.

## Requirements

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine + Compose v2)
- On Windows, [PowerShell 5.1+](https://learn.microsoft.com/powershell/) for the helper scripts
- Optional: Python 3.10+ and Node.js 18+ if you use `setup.ps1` to repair local virtualenvs or run a local frontend build

## Quick start

From the repository root:

1. **First-time or full bootstrap** (creates or updates `.env`, validates Compose, builds images, starts everything):

   ```powershell
   .\setup.ps1
   ```

2. **Day-to-day restart** (assumes `.env` exists; stops containers, starts stack, waits for gateway + UI):

   ```powershell
   .\start.ps1
   ```

3. **Rebuild application images without wiping volumes**:

   ```powershell
   .\start.ps1 -Rebuild
   ```

4. **Destructive full Docker rebuild** (removes containers and Compose **volumes**, prunes dangling images, pulls base images, rebuilds, then starts). Use when you want a clean database volume or suspect image corruption:

   ```powershell
   .\start.ps1 -FullRebuild
   ```

   Add `-FullRebuildNoCache` to force a no-cache image build, or `-SkipBaseImagePull` to skip pulling `pgvector`, `redis`, `node`, and `python` base images.

### Environment file

- **Canonical file:** `.env` at the **repository root**. Docker Compose and both PowerShell scripts use this file only.
- `setup.ps1` fills in missing secrets and service URLs for local containers.
- **Frontend dev server:** `frontend/.env.development` sets `REACT_APP_BACKEND_URL` when you run `npm start` in `frontend` (keep it pointed at your gateway, usually `http://localhost:8000`).

Do not maintain a separate `backend/.env` for Compose; it is easy to get out of sync with the stack.

### Database schema

- **Canonical schema file:** [`backend/sql_schema.sql`](backend/sql_schema.sql).
- `backend/sql_schema.sql` is the single source of truth for PostgreSQL bootstrap and compatibility setup.
- The old repository-root `schema.sql` snapshot has been retired to avoid drift; review and run `backend/sql_schema.sql` first.

### URLs (default local)

| Surface | URL |
|--------|-----|
| API gateway | http://127.0.0.1:8000 |
| Health | http://127.0.0.1:8000/api/healthz |
| Frontend (Compose) | http://127.0.0.1:3000 |
| Widget demo | http://127.0.0.1:3000/widget-demo |

Internal service ports are wired inside the Compose network; only the gateway, frontend, Postgres host port, and selected services are published as defined in `docker-compose.yml`.

### Logs and stop

```powershell
docker compose -p pulse-v3 -f docker-compose.yml --env-file .env logs -f --tail=100 gateway
docker compose -p pulse-v3 -f docker-compose.yml --env-file .env down
```

## Documentation

- [docs/PROJECT.md](docs/PROJECT.md) — architecture, layout, and integration notes

## Integrations

Placeholders in `.env` cover Meta (WhatsApp / social), Stripe, and LLM providers. Replace them before using those features in production.

**Billing without Stripe:** Set `STRIPE_ENABLED=false` and keep `DEMO_MODE` / `STRIPE_OPTIONAL` off. Self-serve signup provisions a workspace with an offline trial (`TRIAL_PERIOD_DAYS`, default 90) and no card; after the trial period, API access returns 402 until you add your own billing flow or turn Stripe back on. Re-enable Stripe with `STRIPE_ENABLED=true` (or `auto`) plus `STRIPE_SECRET_KEY` and `STRIPE_PRICE_*`.

## License

See repository license terms if provided by the project owners.
