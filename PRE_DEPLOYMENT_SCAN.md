# Pulse Engine - Pre-Deployment Complete Scan Report
**Date**: May 30, 2026  
**Status**: ⚠️ **NOT READY FOR PRODUCTION** - Critical gaps remaining

---

## 📋 Executive Summary

The Pulse Engine project has substantial security hardening and testing in place, but requires completion of **4 critical blockers** and **12 high-priority enhancements** before production deployment. This report details all missing components across frontend, backend, AI, and database layers.

### Critical Blockers (MUST FIX)
1. **Runtime DDL Extraction** - Service startup DDL must be converted to Alembic migrations
2. **Monitoring & Observability** - No CloudWatch/external monitoring configured
3. **Error Tracking** - No error tracking service (Sentry/Rollbar) integrated
4. **Production Logging Export** - Logs not exported to external service

---

## 🔴 CRITICAL BLOCKERS

### 1. Runtime DDL Extraction Required
**Severity**: CRITICAL  
**Impact**: Database deployment, scaling, and high-availability concerns

Services still executing DDL at runtime (must be converted to Alembic):

#### Agent Orchestrator Service
- **File**: `backend/agent_orchestrator/bootstrap.py`
- **DDL Count**: 8 tables + 6 indexes
- **Tables**: `global_memory`, `workflows`, `workflow_executions`, `workflow_transitions`, `agent_memory`
- **Issue**: Creates schema `agent_orchestrator` and tables on startup

#### Data Pipeline Service
- **File**: `backend/data_pipeline/bootstrap.py`
- **DDL Count**: 4 tables + 6+ indexes
- **Tables**: `raw_events`, `raw_messages`, `raw_leads`, `analytics_events`
- **Issue**: Critical metrics tables created at runtime

#### Public Signup Service
- **File**: `backend/services/public_signup_service.py` (lines 102-131)
- **DDL Count**: 1 table + 2 indexes
- **Table**: `public.pending_signups`
- **Issue**: Creates pending signup tracking table at runtime

#### Current Migration Coverage
- **Total migrations**: 7
- **Status**: Missing foundation migrations for core services
- **Risk**: Cannot safely redeploy or scale without downtime

**Action Required**:
```bash
# Create new Alembic revisions for:
1. Agent orchestrator schema and tables
2. Data pipeline schema and tables
3. Public signup table
4. Update agent_orchestrator/bootstrap.py, data_pipeline/bootstrap.py, 
   services/public_signup_service.py to remove runtime DDL
5. Verify with: alembic upgrade head
```

---

### 2. Monitoring & Observability NOT CONFIGURED
**Severity**: CRITICAL  
**Impact**: Cannot detect outages, performance degradation, or issues in production

#### What's Missing

**CloudWatch Integration**
- ❌ CloudWatch Logs export not configured
- ❌ CloudWatch Metrics not published
- ❌ Alarms not set up (CPU, memory, DB, error rates)
- ❌ Dashboards not created
- ❌ Log groups not configured

**Distributed Tracing**
- ⚠️ **Partial**: Trace context exists in code (`shared/tracing.py`)
- ❌ Not exported to X-Ray or Jaeger
- ❌ Trace sampling not configured
- ❌ Span propagation to external services missing

**Application Metrics**
- ⚠️ **Partial**: In-memory metrics exist (`shared/metrics.py`)
- ❌ Not exported to CloudWatch, Prometheus, or DataDog
- ❌ Custom business metrics missing
- ❌ Provider health metrics only in-memory

#### Structured Logging
- ✅ **Implemented**: JSON logging formatter in place (`shared/json_logging.py`)
- ❌ **Missing**: Export to CloudWatch Logs, ELK, or Splunk

**Action Required**:
```python
# Add to backend/shared/app_factory.py or separate monitoring module:

1. CloudWatch Logs export
2. CloudWatch Metrics for:
   - Request count/latency (HTTP status codes)
   - Database pool stats
   - Redis connection health
   - AI provider response times
   - Error rates (5xx, 4xx)
   - Authentication/authorization failures
   - Rate limit violations

3. Custom metrics:
   - Active conversations
   - Messages processed
   - AI API calls
   - Lead scoring operations
   - Email campaigns sent

4. Alarms for:
   - Error rate > 5%
   - p99 latency > 5s
   - Database connection pool exhaustion
   - Redis memory usage > 80%
   - RDS CPU > 80%
   - Lambda/ECS task failures
```

---

### 3. Error Tracking Service NOT INTEGRATED
**Severity**: CRITICAL  
**Impact**: Cannot track production issues, debug customer problems, identify regressions

#### What's Missing
- ❌ **No Sentry integration** (recommended for Python/React)
- ❌ **No Rollbar integration** (alternative)
- ❌ **No DataDog integration** (alternative)
- ❌ Exception capture in FastAPI middleware
- ❌ Frontend error boundary reporting
- ❌ Error context enrichment (user_id, company_id, trace_id)

#### Current Error Handling
- ✅ Global FastAPI exception handler exists (`shared/app_factory.py:142-150`)
- ✅ Frontend error boundary exists (`components/GlobalErrorBoundary.jsx`)
- ❌ Neither reports to external service

**Action Required**:
```python
# Add Sentry integration example:

# 1. backend/shared/sentry_config.py (new)
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from sentry_sdk.integrations.redis import RedisIntegration

def init_sentry(service_name: str):
    sentry_sdk.init(
        dsn=os.environ.get("SENTRY_DSN"),
        environment=os.environ.get("ENVIRONMENT", "development"),
        traces_sample_rate=0.1,  # Adjust for production
        profiles_sample_rate=0.1,
        integrations=[
            FastApiIntegration(),
            SqlalchemyIntegration(),
            RedisIntegration(),
        ],
    )
    sentry_sdk.set_tag("service", service_name)

# 2. Add to backend/server.py startup
init_sentry("pulse-engine")

# 3. Enhance exception handler to capture context
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    trace = current_trace_context()
    with sentry_sdk.push_scope() as scope:
        scope.set_extra("company_id", request.app.current_company)
        scope.set_extra("user_id", request.app.current_user)
        scope.set_extra("request_id", trace.request_id if trace else None)
        scope.set_context("request", {
            "method": request.method,
            "url": str(request.url),
            "path": request.url.path,
        })
        sentry_sdk.capture_exception(exc)
    ...

# 4. frontend/src/utils/errorReporting.js (new)
import * as Sentry from "@sentry/react";

export function initSentry() {
  Sentry.init({
    dsn: process.env.REACT_APP_SENTRY_DSN,
    environment: process.env.NODE_ENV,
    tracesSampleRate: 0.1,
  });
}

// In GlobalErrorBoundary
Sentry.captureException(error);
```

---

### 4. Production Logging Export NOT CONFIGURED
**Severity**: CRITICAL  
**Impact**: Cannot retain logs after container restart, no log searchability

#### What's Missing
- ❌ CloudWatch Logs group configuration
- ❌ Log retention policy (e.g., 30 days)
- ❌ Log streaming from containers
- ❌ Structured log aggregation
- ❌ Log-based metrics
- ❌ Log search and alerting

#### Current Logging
- ✅ Structured JSON logging in place (`shared/json_logging.py`)
- ✅ Logs output to stdout
- ❌ Not persisted beyond container lifecycle

**Action Required**:
```yaml
# docker-compose.yml updates needed:
services:
  gateway:
    logging:
      driver: awslogs
      options:
        awslogs-group: /ecs/pulse-engine-gateway
        awslogs-region: ${AWS_REGION}
        awslogs-stream-prefix: ecs

# OR for local development, add:
  gateway:
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

# AWS CloudFormation/Terraform: Create CloudWatch Log Group
resource "aws_cloudwatch_log_group" "pulse_engine" {
  name              = "/ecs/pulse-engine"
  retention_in_days = 30
}

# Queries to set up:
# - Error rate: fields @message | filter @message like /ERROR/
# - Performance: fields @duration | stats avg(@duration), max(@duration)
# - Auth failures: fields @message | filter @message like /401|403/
```

---

## 🟠 HIGH-PRIORITY ENHANCEMENTS

### 5. Health Check Endpoints - INCOMPLETE
**Severity**: HIGH  
**Impact**: Kubernetes/ECS cannot properly determine service health

#### Current State
- ✅ Basic health check exists: `GET /health`
- ✅ Readiness check exists: `GET /ready`
- ⚠️ Incomplete checks

#### Missing
- ❌ Database connection pool health
- ❌ Redis connection health
- ❌ External provider health (AI, Stripe, etc.)
- ❌ Queue depth monitoring
- ❌ Circuit breaker state

**Implementation**:
```python
# backend/shared/app_factory.py - enhance readiness check

@app.get("/ready")
async def readiness() -> dict:
    checks: dict[str, str] = {}
    
    # Database
    try:
        pool = await db._get_pool()
        size = pool.get_size()
        free = pool.get_idle_size()
        checks["database"] = "ok" if free > 0 else "exhausted"
    except Exception as e:
        checks["database"] = f"error: {str(e)[:50]}"
    
    # Redis
    try:
        cache = await close_cache_clients.__self__.redis
        await cache.ping()
        checks["cache"] = "ok"
    except Exception as e:
        checks["cache"] = f"error: {str(e)[:50]}"
    
    # AI provider health (from provider_health.py)
    health_snapshot = provider_health_snapshot()
    degraded = [h for h in health_snapshot if h['status'] != 'healthy']
    checks["ai_providers"] = f"{len(degraded)} degraded" if degraded else "ok"
    
    # Queue
    try:
        snapshot = collect_background_queue_snapshot()
        checks["background_queue"] = "ok" if snapshot['pending'] < 1000 else "warning"
    except Exception:
        checks["background_queue"] = "unknown"
    
    is_ready = all(v == "ok" for v in checks.values())
    status_code = 200 if is_ready else 503
    return {"service": service_name, "ready": is_ready, "checks": checks}, status_code
```

---

### 6. Rate Limiting - INCOMPLETE
**Severity**: HIGH  
**Impact**: Service vulnerable to abuse, no API quota enforcement

#### Current State
- ✅ In-memory rate limiter exists
- ✅ Redis-backed rate limiter with fallback
- ❌ No per-tenant rate limits
- ❌ No per-user rate limits
- ❌ No per-endpoint rate limits
- ❌ No rate limit response headers

**Implementation**:
```python
# backend/shared/rate_limiting.py (new)

# Add per-tenant limits
TENANT_LIMITS = {
    "default": {"requests_per_minute": 1000, "requests_per_hour": 50000},
    "premium": {"requests_per_minute": 5000, "requests_per_hour": 500000},
}

# Add per-user limits
USER_LIMITS = {
    "admin": {"requests_per_minute": 10000},
    "standard": {"requests_per_minute": 1000},
    "limited": {"requests_per_minute": 100},
}

# Add response headers
response.headers["RateLimit-Limit"] = str(limit)
response.headers["RateLimit-Remaining"] = str(remaining)
response.headers["RateLimit-Reset"] = str(reset_timestamp)
```

---

### 7. Database Connection Pooling Optimization
**Severity**: HIGH  
**Impact**: Connection exhaustion under load, slow queries

#### Current State
```env
DB_POOL_MIN_SIZE=5
DB_POOL_MAX_SIZE=20
DB_COMMAND_TIMEOUT_SECONDS=60
```

#### Missing
- ❌ Pool metrics not exposed
- ❌ Timeout handling not comprehensive
- ❌ Statement pool not configured
- ❌ Connection validation queries missing

**Action Required**:
```python
# Add pool monitoring to /metrics endpoint
# Add Prometheus-style metrics:
db_pool_size{service="gateway"} 20
db_pool_idle{service="gateway"} 3
db_pool_connections_acquired_total{service="gateway"} 15432
db_pool_connection_errors_total{service="gateway"} 2
```

---

### 8. Frontend Environment Variables Validation
**Severity**: HIGH  
**Impact**: Missing API endpoints in production build

#### Current State
- ✅ Environment variables used (9 instances)
- ❌ No validation of required variables
- ❌ No defaults for production
- ❌ No build-time warnings

**Required Variables**:
```bash
REACT_APP_BACKEND_URL        # Critical
REACT_APP_DEMO_MODE          # Feature flag
REACT_APP_SENTRY_DSN         # Error tracking
REACT_APP_GA_ID              # Analytics (optional)
REACT_APP_STRIPE_PUBLIC_KEY  # Billing (optional)
```

**Action**:
```javascript
// frontend/src/utils/envValidation.js (new)
const REQUIRED_ENV_VARS = ['REACT_APP_BACKEND_URL'];
const OPTIONAL_ENV_VARS = ['REACT_APP_SENTRY_DSN', 'REACT_APP_GA_ID'];

export function validateEnv() {
  const missing = REQUIRED_ENV_VARS.filter(v => !process.env[v]);
  if (missing.length > 0) {
    throw new Error(`Missing required env vars: ${missing.join(', ')}`);
  }
}

// Call in index.js
validateEnv();
```

---

### 9. Frontend Build Optimization
**Severity**: MEDIUM  
**Impact**: Slow initial load, large bundle size

#### Current State
- ✅ React production build works
- ⚠️ One unused variable warning
- ❌ Bundle size analysis missing
- ❌ Code splitting not optimized
- ❌ Source maps in production

**Action Required**:
```javascript
// frontend/src/pages/InboxPage.js - fix unused variable warning

// .env settings for production
GENERATE_SOURCEMAP=false  # Don't include source maps in production bundle

// package.json - add build analysis
"scripts": {
  "build:analyze": "craco build && source-map-explorer 'build/static/js/*.js'"
}

// Tailwind CSS optimization
// tailwind.config.js should have:
content: [
  "./src/**/*.{js,jsx}",
],
```

---

### 10. AI Provider Failover & Degradation
**Severity**: HIGH  
**Impact**: Single provider outage = service down, poor customer experience

#### Current State
- ✅ Provider health tracking exists
- ✅ Fallback mechanism exists
- ⚠️ Degraded mode not fully implemented
- ❌ Circuit breaker not integrated
- ❌ No graceful degradation UI messaging

**Gaps**:
```python
# backend/services/ai_service/routes.py - missing:
1. Circuit breaker state management
2. Automatic fallback to keyword matching when all providers fail
3. Caching of AI responses
4. Return cached responses on provider timeout

# Missing degradation responses:
{
    "success": true,
    "data": {...},
    "degraded": true,  # Flag when AI is degraded
    "degraded_reason": "Using fallback - primary provider unavailable"
}
```

---

### 11. Backup & Disaster Recovery - NOT TESTED
**Severity**: CRITICAL  
**Impact**: Cannot recover from data loss

#### Missing Items
- ❌ RDS automated backups not configured
- ❌ Backup retention policy undefined
- ❌ Backup restoration not tested
- ❌ Cross-region replication not configured
- ❌ S3 versioning and lifecycle policies missing
- ❌ Disaster recovery runbook not documented

**Action Required**:
```bash
# AWS RDS Backup Configuration
# Set via AWS Console or Terraform:
backup_retention_period = 30  # days
backup_window = "03:00-04:00" # UTC
multi_az = true                # For HA
copy_tags_to_snapshot = true

# S3 Versioning & Lifecycle
# Enable versioning on media bucket
# Set lifecycle policy:
# - Transition to GLACIER after 90 days
# - Delete after 1 year (adjust per retention policy)

# Create backup test runbook:
# 1. Snapshot current RDS instance
# 2. Restore to test instance
# 3. Run integration tests
# 4. Verify data integrity
# 5. Document recovery time (RTO)
```

---

### 12. Infrastructure as Code & Deployment Automation
**Severity**: HIGH  
**Impact**: Manual deployments error-prone, rollback difficult

#### Missing
- ❌ AWS CloudFormation templates
- ❌ Terraform scripts
- ❌ Kubernetes manifests
- ❌ ECS task definitions
- ❌ Auto-scaling policies undefined
- ❌ Blue-green deployment not configured

**Minimal Requirements**:
```bash
# Create directory structure:
infrastructure/
├── terraform/
│   ├── main.tf          # VPC, RDS, ElastiCache, S3, ALB
│   ├── ecs.tf           # ECS cluster, task definitions, services
│   ├── rds.tf           # Database configuration
│   ├── networking.tf    # VPC, subnets, security groups
│   └── variables.tf     # Input variables
├── docker/
│   ├── gateway.Dockerfile
│   ├── auth.Dockerfile
│   ├── customer.Dockerfile
│   └── ... (one per service)
└── k8s/ (if using Kubernetes)
    ├── namespace.yaml
    ├── deployments/
    └── services/
```

---

## 🟡 MEDIUM PRIORITY ITEMS

### 13. Secret Management in Production
**Current State**:
- ✅ Environment variables for secrets
- ❌ AWS Secrets Manager not integrated
- ❌ Rotation policy not defined
- ❌ Secret scanning in CI not enabled

**Add to CI**:
```yaml
# .github/workflows/ci.yml - add secret scanning
- name: Run secret scanning
  uses: trufflesecurity/trufflescan-github-action@main
```

---

### 14. Database Row-Level Security (RLS) Verification
**Current State**:
- ✅ RLS policies exist in schema
- ✅ Startup check for privileged roles
- ❌ RLS not tested in production
- ❌ Cross-tenant isolation not verified

**Test Case Needed**:
```python
# Add to backend/tests/test_rls.py
async def test_rls_tenant_isolation():
    """Verify company A cannot access company B data"""
    # Create users in different tenants
    # Attempt cross-tenant query
    # Assert returns empty or 403
```

---

### 15. Stripe Webhook Verification
**Current State**:
- ⚠️ Webhook verification exists
- ❌ Not tested in production
- ❌ Retry logic not verified
- ❌ Idempotency not tested with replays

**Verification Needed**:
```bash
# Test in Stripe Dashboard:
1. Send test webhook event
2. Verify order created/updated correctly
3. Trigger duplicate webhook (within replay window)
4. Verify idempotent behavior
5. Check event_id deduplication in database
```

---

### 16. Performance & Load Testing
**Status**: NOT DONE

**Before Launch - Execute**:
```bash
# Load testing targets:
1. Gateway: 100-250 req/sec per task
2. Database: 1000+ concurrent connections
3. Redis: Stream backlog capacity
4. AI endpoint: Provider quota limits
5. S3: Media upload/download throughput

Tools:
- Apache JMeter
- Locust
- k6 load testing

Key scenarios:
- Concurrent user signup (100-500 users)
- Bulk message ingestion (1000s msg/min)
- Analytics query under load
- AI request spike (sudden 10x traffic)
```

---

## 🟢 ALREADY IMPLEMENTED (Good)

✅ **Security Hardening**
- Structured JSON logging
- JWT token management with revocation
- CORS hardening (production origins only)
- Web socket TLS/security proxying
- HTTPS redirect enforced in production
- Database TLS configuration guards
- Password hashing (argon2)
- Rate limit fallback (in-process when Redis down)
- Provider health tracking

✅ **Frontend**
- Error boundary implementation
- Secure token storage (memory + HttpOnly cookies)
- Session-scoped storage instead of localStorage
- Global error boundary

✅ **Testing**
- 358 backend tests passing
- 14 frontend test suites
- 28 WhatsApp bridge tests
- Docker Compose validation in CI
- Python syntax validation

✅ **API Design**
- /health endpoint (basic)
- /ready endpoint (basic)
- /metrics endpoint (in-memory)
- Trace context and request IDs
- Structured error responses

---

## 📊 Deployment Readiness Score

| Category | Status | Score |
|----------|--------|-------|
| **Security** | Hardened locally | 7/10 |
| **Monitoring** | Not configured | 2/10 |
| **Error Tracking** | Not configured | 0/10 |
| **Database** | DDL not extracted | 4/10 |
| **Logging** | Structured, not exported | 4/10 |
| **High Availability** | Not configured | 0/10 |
| **Disaster Recovery** | Not tested | 0/10 |
| **Performance Testing** | Not done | 0/10 |
| **Testing** | Comprehensive | 9/10 |
| **Frontend Build** | Nearly ready | 8/10 |
| ****OVERALL** | **BLOCKED** | **3.4/10** |

---

## 🚀 Deployment Checklist

### Phase 1: Critical Blocker Resolution (Week 1)
- [ ] Extract runtime DDL to Alembic migrations (agent_orchestrator, data_pipeline, signup)
- [ ] Integrate Sentry error tracking (backend + frontend)
- [ ] Set up CloudWatch Logs export
- [ ] Configure CloudWatch Metrics publishing
- [ ] Create CloudWatch alarms (errors, latency, resource usage)
- [ ] Test Alembic migrations: `alembic upgrade head`

### Phase 2: Observability (Week 1-2)
- [ ] Complete health check endpoints
- [ ] Add distributed tracing export (X-Ray or Jaeger)
- [ ] Configure log aggregation dashboards
- [ ] Set up performance dashboards (RDS, Redis, ECS)
- [ ] Create on-call runbooks

### Phase 3: Infrastructure (Week 2-3)
- [ ] Provision AWS resources (RDS Multi-AZ, ElastiCache, S3, ALB, ECS)
- [ ] Configure backup policies (RDS 30-day retention)
- [ ] Enable S3 versioning and lifecycle policies
- [ ] Set up IAM roles and policies
- [ ] Configure security groups
- [ ] Enable VPC Flow Logs

### Phase 4: Deployment Automation (Week 2-3)
- [ ] Create ECS task definitions
- [ ] Set up deployment pipeline (CodePipeline/GitHub Actions)
- [ ] Create blue-green deployment strategy
- [ ] Document rollback procedure
- [ ] Test canary deployments

### Phase 5: Testing (Week 3-4)
- [ ] Run load tests (100-250 req/sec per task)
- [ ] Test database failover
- [ ] Test backup restoration
- [ ] Cross-tenant isolation test
- [ ] Stripe webhook integration test
- [ ] AI provider failover test

### Phase 6: Pre-Launch (Week 4)
- [ ] Security audit review
- [ ] Performance baseline established
- [ ] On-call training completed
- [ ] Incident response procedures documented
- [ ] Monitoring dashboards validated
- [ ] 48-hour soak test passed

---

## 📝 Deployment Configuration Template

```env
# .env.production (DO NOT commit)

# === REQUIRED BEFORE LAUNCH ===
ENVIRONMENT=production
SENTRY_DSN=https://your-sentry-dsn
AWS_REGION=us-east-1
CLOUDWATCH_LOG_GROUP=/ecs/pulse-engine

# === DATABASE ===
DATABASE_URL=postgresql://app_role:XXXXX@rds-endpoint:5432/pulse_engine
POSTGRES_SSLMODE=require

# === REDIS ===
REDIS_URL=redis://redis-endpoint:6379/0?ssl=true
CACHE_REDIS_URL=redis://redis-endpoint:6379/1?ssl=true

# === STORAGE ===
S3_BUCKET=pulse-engine-production
AWS_S3_REGION=us-east-1

# === AI PROVIDERS ===
GEMINI_API_KEY=XXXXX
OPENAI_API_KEY=XXXXX
ANTHROPIC_API_KEY=XXXXX

# === STRIPE ===
STRIPE_SECRET_KEY=sk_live_XXXXX
STRIPE_WEBHOOK_SECRET=whsec_XXXXX

# === OAUTH ===
GOOGLE_CLIENT_SECRET=XXXXX
FACEBOOK_APP_SECRET=XXXXX

# === SECURITY ===
JWT_SECRET=XXXXX (32+ random chars)
INTERNAL_SERVICE_SECRET=XXXXX (24+ random chars)
META_WEBHOOK_SECRET=XXXXX

# === CORS ===
CORS_ORIGINS=https://yourdomain.com,https://app.yourdomain.com
FRONTEND_URL=https://app.yourdomain.com
```

---

## ⚠️ Critical Reminders

1. **Databases**: Run Alembic migrations before deploying services
2. **Secrets**: Never commit `.env` files - use AWS Secrets Manager
3. **SSL/TLS**: All external connections must be encrypted
4. **Backups**: Test backup restoration before going live
5. **Monitoring**: Have dashboards visible to on-call team before launch
6. **Runbooks**: Document procedures for common incidents
7. **Load Testing**: Baseline performance before launch
8. **Capacity Planning**: Monitor and adjust ECS task sizing

---

## 📞 Next Steps

1. **Immediate** (This week): Fix critical blockers 1-4
2. **Short-term** (Next 2 weeks): Complete items 5-9
3. **Pre-launch** (Week 3-4): Items 10-16 + testing
4. **Daily syncs** once deployment phase begins
5. **Soak test**: 48-hour minimum before production switch

---

**Report Generated**: May 30, 2026  
**Recommendation**: **NO-GO for production launch** until critical blockers resolved.

For detailed implementation guide, refer to `AWS_DEPLOYMENT_GUIDE.md` and `PRODUCTION_READINESS_REPORT_2026-05-30.md`.
