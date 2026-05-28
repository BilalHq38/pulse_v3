#!/usr/bin/env bash
# deploy.sh — One-command deployment for Pulse Engine
#
# Usage:
#   ./deploy.sh            # Start (or restart) all services
#   ./deploy.sh --build    # Rebuild images then start
#   ./deploy.sh --reset    # Remove volumes, rebuild, start fresh
#   ./deploy.sh --migrate  # Run pending SQL migrations only
#   ./deploy.sh --status   # Show service health without starting

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[deploy]${NC} $*"; }
success() { echo -e "${GREEN}[deploy]${NC} $*"; }
warn()    { echo -e "${YELLOW}[deploy]${NC} $*"; }
error()   { echo -e "${RED}[deploy]${NC} $*" >&2; }
fatal()   { error "$*"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BUILD=false
RESET=false
MIGRATE_ONLY=false
STATUS_ONLY=false

for arg in "$@"; do
  case "$arg" in
    --build)   BUILD=true ;;
    --reset)   RESET=true; BUILD=true ;;
    --migrate) MIGRATE_ONLY=true ;;
    --status)  STATUS_ONLY=true ;;
    --help|-h) echo "Usage: $0 [--build|--reset|--migrate|--status]"; exit 0 ;;
    *) warn "Unknown argument: $arg" ;;
  esac
done

echo ""
echo -e "${BOLD}╔══════════════════════════════════════╗${NC}"
echo -e "${BOLD}║     Pulse Engine Deployment v1.0     ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════╝${NC}"
echo ""

# ── 1. Dependency checks ─────────────────────────────────────────────────────
info "Checking dependencies…"
command -v docker   >/dev/null 2>&1 || fatal "docker is not installed"
command -v docker compose >/dev/null 2>&1 || \
  docker compose version >/dev/null 2>&1 || \
  fatal "docker compose (v2) is not installed"

DOCKER_COMPOSE="docker compose"

# ── 2. Environment validation ─────────────────────────────────────────────────
info "Validating environment…"

ENV_FILE=".env"
[[ ! -f "$ENV_FILE" ]] && fatal ".env file not found. Copy .env.example and fill in secrets."

# Required non-empty vars
REQUIRED_VARS=(
  JWT_SECRET
  INTERNAL_SERVICE_SECRET
  DATABASE_URL
  DEFAULT_TENANT_ID
  DEFAULT_TENANT_API_KEY
  DEFAULT_TENANT_SALT
  TENANT_SALTS
  IDENTITY_ADMIN_EMAIL
  IDENTITY_ADMIN_PASSWORD
  META_WEBHOOK_SECRET
  WEB_CHAT_WEBHOOK_SECRET
  EXTERNAL_WEBHOOK_SECRET
)

missing_vars=()
for var in "${REQUIRED_VARS[@]}"; do
  val=$(grep -E "^${var}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
  if [[ -z "$val" ]]; then
    missing_vars+=("$var")
  fi
done

if [[ ${#missing_vars[@]} -gt 0 ]]; then
  error "Missing required environment variables:"
  for v in "${missing_vars[@]}"; do error "  • $v"; done
  fatal "Add the missing variables to .env and re-run."
fi

# Warn on obviously weak secrets
WEAK_PATTERNS=("change-me" "change-me-too" "fallback-secret" "demo-key" "example")
for var in JWT_SECRET INTERNAL_SERVICE_SECRET; do
  val=$(grep -E "^${var}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2-)
  for pat in "${WEAK_PATTERNS[@]}"; do
    if echo "$val" | grep -qi "$pat"; then
      warn "$var looks like a placeholder — replace with a strong secret before production use."
    fi
  done
done

success "Environment validated (${#REQUIRED_VARS[@]} required vars present)"

# ── 3. Status-only mode ───────────────────────────────────────────────────────
if $STATUS_ONLY; then
  info "Service health:"
  $DOCKER_COMPOSE ps
  echo ""
  info "Gateway health:"
  curl -sf http://localhost:8000/api/healthz 2>/dev/null && echo "" || warn "Gateway not reachable on port 8000"
  exit 0
fi

# ── 4. Optional reset ─────────────────────────────────────────────────────────
if $RESET; then
  warn "RESET mode: removing all volumes (data will be lost)…"
  read -r -p "  Type 'yes' to confirm: " confirm
  [[ "$confirm" == "yes" ]] || fatal "Reset aborted."
  $DOCKER_COMPOSE down --volumes --remove-orphans
  success "Volumes removed."
fi

# ── 5. Start infrastructure (postgres + redis) ───────────────────────────────
info "Starting infrastructure services…"
$DOCKER_COMPOSE up -d postgres redis

info "Waiting for PostgreSQL to be ready…"
MAX_WAIT=60; WAITED=0
until $DOCKER_COMPOSE exec -T postgres pg_isready -q 2>/dev/null; do
  sleep 2; WAITED=$((WAITED + 2))
  [[ $WAITED -ge $MAX_WAIT ]] && fatal "PostgreSQL did not become ready in ${MAX_WAIT}s"
done
success "PostgreSQL is ready."

info "Waiting for Redis to be ready…"
WAITED=0
until $DOCKER_COMPOSE exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; do
  sleep 1; WAITED=$((WAITED + 1))
  [[ $WAITED -ge 20 ]] && fatal "Redis did not become ready in 20s"
done
success "Redis is ready."

# ── 6. Apply all SQL migrations ───────────────────────────────────────────────
info "Applying SQL migrations…"

# Derive DB connection from .env
DB_URL=$(grep -E "^DATABASE_URL=" "$ENV_FILE" | cut -d= -f2- | tr -d '"' | tr -d "'")
# Convert service hostname to localhost for migrations (gateway accesses on mapped port)
DB_URL_LOCAL=$(echo "$DB_URL" | sed 's|@postgres:|@localhost:|g' | sed 's|/pulse_engine|/pulse_engine|g')
# Try to find mapped postgres port
PG_PORT=$(grep "POSTGRES_HOST_PORT" "$ENV_FILE" 2>/dev/null | cut -d= -f2- || echo "5433")
DB_URL_LOCAL=$(echo "$DB_URL" | sed "s|@postgres:5432|@localhost:${PG_PORT}|g")

MIGRATION_DIR="backend/sql_migrations"
if [[ -d "$MIGRATION_DIR" ]]; then
  MIGRATION_FILES=$(find "$MIGRATION_DIR" -name "*.sql" ! -name "_*" | sort)
  for sql_file in $MIGRATION_FILES; do
    filename=$(basename "$sql_file")
    info "  ↳ Applying $filename…"
    # Copy to container and apply via the postgres container directly
    $DOCKER_COMPOSE exec -T postgres psql -U "${POSTGRES_USER:-pulse_engine_app}" -d "${POSTGRES_DB:-pulse_engine}" \
      < "$sql_file" >/dev/null 2>&1 \
      && success "    ✓ $filename" \
      || warn "    ⚠ $filename had warnings (likely already applied)"
  done
else
  warn "No migrations directory found at $MIGRATION_DIR"
fi

if $MIGRATE_ONLY; then
  success "Migration-only run complete."
  exit 0
fi

# ── 7. Build (if requested) ───────────────────────────────────────────────────
if $BUILD; then
  info "Building images (this may take a few minutes)…"
  $DOCKER_COMPOSE build
  success "Build complete."
fi

# ── 8. Start all services ─────────────────────────────────────────────────────
info "Starting all services…"
$DOCKER_COMPOSE up -d
success "All services started."

# ── 9. Health verification ────────────────────────────────────────────────────
info "Waiting for gateway to become healthy…"
MAX_WAIT=120; WAITED=0
until curl -sf "http://localhost:${GATEWAY_HOST_PORT:-8000}/api/healthz" >/dev/null 2>&1; do
  sleep 3; WAITED=$((WAITED + 3))
  if [[ $WAITED -ge $MAX_WAIT ]]; then
    warn "Gateway health check timed out after ${MAX_WAIT}s — services may still be starting."
    warn "Check logs: docker compose logs gateway"
    break
  fi
done

if curl -sf "http://localhost:${GATEWAY_HOST_PORT:-8000}/api/healthz" >/dev/null 2>&1; then
  success "Gateway is healthy."
fi

# ── 10. Final report ──────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔═══════════════════════════════════════╗${NC}"
echo -e "${BOLD}║        Deployment Complete             ║${NC}"
echo -e "${BOLD}╚═══════════════════════════════════════╝${NC}"
echo ""
FRONTEND_PORT="${FRONTEND_HOST_PORT:-3000}"
GATEWAY_PORT="${GATEWAY_HOST_PORT:-8000}"
echo -e "  Frontend  → ${CYAN}http://localhost:${FRONTEND_PORT}${NC}"
echo -e "  API       → ${CYAN}http://localhost:${GATEWAY_PORT}/api${NC}"
echo -e "  Health    → ${CYAN}http://localhost:${GATEWAY_PORT}/api/healthz${NC}"
echo ""
echo -e "  To view logs:    ${YELLOW}docker compose logs -f [service]${NC}"
echo -e "  To stop:         ${YELLOW}docker compose down${NC}"
echo -e "  To rebuild:      ${YELLOW}./deploy.sh --build${NC}"
echo ""
