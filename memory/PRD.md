# Pulse Engine Repair PRD

## Original problem statement
Analyze the complete GitHub repository, including frontend, backend, APIs, AI components, and database structure. Identify and fix all errors, bugs, and inconsistencies across the entire codebase, then verify that all fixes are correctly applied and functioning.

Investigate the sign-up flow issue where clicking sign-up only refreshes the page without creating an account. Trace the problem across frontend, backend, and database layers, and resolve it completely.

Review the database schema against the backend logic. Add any missing tables, fields, or relationships required by the backend, and remove any redundant or unused database elements.

Extract all SQL-related code scattered across the project and consolidate it into a single, clean, well-structured PostgreSQL-compatible file. Ensure this file is complete, optimized, and directly deployable on any PostgreSQL server without modification.

Ensure the final system is consistent, error-free, and fully functional across all components.

## Architecture decisions
- Run the repo as a single FastAPI backend entrypoint at `/app/backend/server.py` for this environment, while reusing the repository’s existing legacy routers and PostgreSQL-backed logic.
- Use local PostgreSQL 15 in the container with `/app/.env`-driven configuration, keeping backend data in PostgreSQL instead of MongoDB.
- Keep `backend/sql_schema.sql` as the runtime schema source and create `backend/postgresql_consolidated.sql` as the deployable consolidated PostgreSQL file.
- Use offline-trial signup when Stripe is disabled, matching the existing product behavior.

## Implemented
- Fixed missing backend entrypoint by creating `backend/server.py`, wiring legacy routers, CORS, health endpoint, schema bootstrap, and auth/billing/bootstrap tasks.
- Installed and configured local PostgreSQL, created root `.env`, and switched the app runtime to PostgreSQL.
- Fixed frontend API base URL resolution by adding `frontend/.env.development` and replacing broken `:8000` fallbacks.
- Fixed public auth gating so direct `/api/auth/*` routes work in the monolith runtime.
- Fixed signup/browser failure caused by billing customer/subscription unique constraints on empty Stripe IDs by using nullable values plus partial unique indexes.
- Fixed email verification flow by making backend verification idempotent and repairing frontend token handling across React development behavior.
- Reduced auth-transition request collisions by removing non-essential immediate avatar refetches after login/OAuth.
- Created consolidated deployable SQL file at `backend/postgresql_consolidated.sql`.
- Verified browser flow: signup → verify email → onboarding → billing → dashboard.

## Prioritized backlog

### P0
- Create a least-privilege PostgreSQL application role instead of using the `postgres` superuser in local/runtime config.
- Add automated E2E coverage for signup, verification, onboarding, billing selection, and dashboard load.

### P1
- Review remaining frontend components for missing `data-testid` coverage on interactive and critical UI elements.
- Audit remaining repository areas not exercised in this run (identity/unification, AI admin surfaces, webhooks, social adapters) with route-level smoke tests.

### P2
- Replace development-only inline styling/auth side effects with smaller reusable components and stricter route-level state handling.
- Consider moving SQL bootstrap/versioning to a formal migration workflow while keeping the consolidated deployable file as the canonical export.

## Next tasks
- Run full regression testing across key authenticated routes.
- Add a first-teammate invitation E2E test after billing selection.
- Create a production-ready database role and environment template.
