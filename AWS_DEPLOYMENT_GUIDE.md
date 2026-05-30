# Pulse Engine AWS Deployment Guide

This guide targets a brand-new AWS account and a staging-first rollout. Do not promote to production until the readiness blockers in `PRODUCTION_READINESS_REPORT_2026-05-30.md` are closed.

## 1. Account Preparation

1. Enable MFA on the root account and stop using root for routine work.
2. Configure AWS Organizations if multiple environments will be separated into staging and production accounts.
3. Create an IAM Identity Center admin group, deployment group, read-only operations group, and security-audit group.
4. Enable CloudTrail organization trails, AWS Config, GuardDuty, Security Hub, IAM Access Analyzer, and billing alerts.
5. Set AWS Budgets alerts at expected monthly spend thresholds.

## 2. Networking

Create one VPC across three Availability Zones:

- Three public subnets for ALB and NAT gateways.
- Three private application subnets for ECS tasks.
- Three isolated data subnets for RDS and ElastiCache.
- Internet gateway attached to the VPC.
- One NAT gateway per AZ for production; one NAT gateway is acceptable only for staging cost reduction.
- Route tables that prevent direct internet ingress to ECS, RDS, and Redis.

Security groups:

| Group | Inbound | Outbound |
| --- | --- | --- |
| `alb-sg` | `443` from internet, optional `80` redirect | frontend and gateway task SGs |
| `frontend-sg` | `3000` from `alb-sg` | customer Socket.IO target, AWS services |
| `gateway-sg` | `8000` from `alb-sg` | internal service SG |
| `service-sg` | service ports only from `gateway-sg`, frontend Socket.IO exception to customer `8003` | RDS, Redis, AWS APIs, approved external APIs |
| `rds-sg` | `5432` from service SG and migration task SG | none required |
| `redis-sg` | `6379` TLS from service SG | none required |

Use VPC endpoints for S3, ECR API, ECR Docker, CloudWatch Logs, Secrets Manager, and KMS to reduce NAT exposure.

## 3. DNS, TLS, And Edge

1. Create or delegate the Route53 hosted zone.
2. Request ACM certificates for `app.example.com`, `api.example.com`, and `media.example.com`.
3. Create an internet-facing ALB with HTTPS listeners and HTTP-to-HTTPS redirect.
4. Route `/api/*` to the gateway target group.
5. Route `/socket.io/*` to the customer realtime target group.
6. Route frontend traffic to the frontend target group or serve the SPA from S3/CloudFront after a static-hosting migration.
7. Put CloudFront in front of media S3 and optionally frontend traffic.
8. Configure CloudFront OAC with signed origin requests for the private media bucket.
9. Add AWS WAF managed rules, rate-based rules, and request-size limits.

Why: Route53 supplies DNS, ACM supplies managed certificates, ALB performs health-aware service routing, CloudFront reduces media latency, and WAF adds edge filtering.

## 4. ECR And ECS

Create ECR repositories for:

```text
gateway auth user customer lead product ai agent-orchestrator analytics
data-pipeline email-campaign notification identity super-admin whatsapp-bridge frontend migrator
```

Use ECS Fargate with:

- Private subnets and no public IPs.
- ECS execution role for ECR pulls, CloudWatch Logs, and Secrets Manager injection.
- Separate task role per service with least privilege.
- Read-only root filesystem where compatible.
- CloudWatch log driver with retention policies.
- Health checks and deployment circuit breaker rollback.
- Two tasks minimum for stateless HTTP services across AZs.

Separate ECS services:

| Service family | Scaling model |
| --- | --- |
| Gateway, auth, user, customer, lead, product, analytics, notification, identity, super-admin | ALB request count, CPU, memory |
| AI and orchestrator | CPU, memory, queue depth, provider rate limits |
| Pipeline workers | Redis stream backlog |
| Campaign workers | Queue depth and email-provider rate limits |
| Scheduler | exactly one task with leader election or EventBridge-triggered jobs |
| WhatsApp bridge | dedicated stateful task strategy; test linked-device failure recovery |

Do not mount cloud credential files. On AWS, use IAM task roles for S3, Secrets Manager, logs, and metrics. For GCP Vertex, use Workload Identity Federation or store provider credentials in Secrets Manager and materialize them at task startup through a controlled entrypoint. Prefer AWS-native or API-key providers if federation is not implemented.

## 5. RDS PostgreSQL And pgvector

1. Create an RDS PostgreSQL 16 Multi-AZ instance in isolated subnets.
2. Enable storage encryption with a customer-managed KMS key.
3. Require TLS and set `POSTGRES_SSLMODE=require` at minimum; use `verify-full` where certificate validation is configured.
4. Attach a custom DB parameter group with `rds.force_ssl=1`.
5. Enable automated backups, point-in-time recovery, Performance Insights, Enhanced Monitoring, and deletion protection.
6. Create separate roles:

```sql
CREATE ROLE pulse_migrator LOGIN PASSWORD '<secret>';
CREATE ROLE pulse_app LOGIN PASSWORD '<secret>' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
```

7. Grant schema/table/sequence privileges required by the app role after migrations.
8. Install pgvector:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

9. Build `backend/migrator.Dockerfile`, then run its ECS task:

```bash
alembic upgrade head
```

10. Verify privileges:

```sql
SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname IN ('pulse_app', 'pulse_migrator');
```

11. Verify active sessions with `pg_stat_ssl`, then run cross-tenant tests using the `pulse_app` role.

Why: RDS provides managed backups and Multi-AZ failover. pgvector stores tenant-scoped embeddings alongside relational source data.

## 6. ElastiCache Redis

Create an ElastiCache Redis replication group with:

- Multi-AZ and automatic failover.
- Encryption in transit and at rest.
- Auth token or IAM authentication where supported by the selected mode.
- Private data subnets only.
- Memory alarms and eviction monitoring.
- Parameter group with an eviction policy appropriate for caches and bounded queue retention.

Use separate logical key prefixes for caching, rate limiting, billing cache, and background queues. Review whether queue streams need a separate Redis replication group if cache eviction could affect job durability.

## 7. S3 Media And CloudFront

1. Create a private versioned S3 bucket for media.
2. Enable block-public-access, SSE-KMS encryption, lifecycle transitions, and access logging.
3. Create a CloudFront distribution with Origin Access Control.
4. Set OAC signing behavior to `always`, and allow CloudFront access through the bucket policy.
5. Configure `MEDIA_STORAGE_BACKEND=s3`, `MEDIA_S3_BUCKET`, `MEDIA_PUBLIC_BASE_URL=https://media.example.com`, and `AWS_REGION`.
6. Grant product/customer task roles `s3:PutObject`, `s3:GetObject`, and scoped delete permissions only for the media prefix.
7. Migrate existing local uploads before cutover.

Why: ECS local disk is ephemeral and cannot safely serve shared media across tasks.

## 8. Secrets Manager

Store secrets individually or as environment bundles:

```text
JWT_SECRET INTERNAL_SERVICE_SECRET DATABASE_URL REDIS_URL CACHE_REDIS_URL
RATE_LIMIT_REDIS_URL BACKGROUND_QUEUE_URL STRIPE_SECRET_KEY STRIPE_WEBHOOK_SECRET
META_WEBHOOK_SECRET WEB_CHAT_WEBHOOK_SECRET EXTERNAL_WEBHOOK_SECRET
WHATSAPP_BRIDGE_SECRET SMTP_PASSWORD BREVO_API_KEY OAUTH_CLIENT_SECRETS
AI_PROVIDER_KEYS META_CREDENTIALS_ENCRYPTION_KEY APP_ENCRYPTION_KEY
```

Use Secrets Manager rotation for database credentials where practical. Reference secrets from ECS task definitions; do not commit `.env` files or credential JSON.

## 9. Monitoring, Logging, Backups, And DR

CloudWatch:

- Log groups per service with 30-90 day retention.
- Alarms for ALB `5xx`, ECS task restarts, CPU, memory, RDS connections, RDS storage, replication lag, Redis memory, Redis evictions, queue backlog, and webhook failure rates.
- Dashboards for HTTP latency, AI latency, provider failures, billing errors, webhook replay rejection, and tenant-isolation failures.

Backups:

- RDS automated backups and manual pre-release snapshots.
- AWS Backup policy for RDS.
- S3 versioning and lifecycle rules.
- Quarterly restore drill into an isolated staging environment.

DR:

1. Document RPO and RTO.
2. Store infrastructure as code in version control.
3. Test restoring RDS and re-pointing ECS secrets.
4. Test rebuilding Redis caches and recovering durable queue state.
5. Test S3 object recovery from versions.

## 10. CI/CD Pipeline

Use GitHub Actions with OIDC federation into a scoped AWS deployment role:

1. Run Python compile, backend tests, frontend tests/build, bridge tests, secret scan, and Compose validation.
2. Build immutable images tagged with commit SHA.
3. Push images to ECR.
4. Run a one-off ECS migrator task with `alembic upgrade head`.
5. Deploy staging ECS services.
6. Run smoke tests, cross-tenant tests, webhook signature tests, Stripe test-mode flow, and AI safety tests.
7. Require manual production approval.
8. Deploy production with ECS rolling deployment and circuit-breaker rollback.

## 11. Production Environment Variables

Set at minimum:

```text
ENVIRONMENT=production
NODE_ENV=production
DEMO_MODE=false
STRIPE_OPTIONAL=false
POSTGRES_SSLMODE=require
PGSSLROOTCERT=/app/certs/rds-global-bundle.pem
GATEWAY_ALLOWED_ORIGINS=https://app.example.com
FRONTEND_URL=https://app.example.com
BACKEND_PUBLIC_URL=https://api.example.com
ALLOW_UNSIGNED_WEB_CHAT_WIDGET=false
ALLOW_LEGACY_EXTERNAL_WEBHOOK_QUERY_TOKEN=false
ALLOW_INSECURE_DB_ROLE=false
MEDIA_STORAGE_BACKEND=s3
MEDIA_S3_BUCKET=<bucket>
MEDIA_PUBLIC_BASE_URL=https://media.example.com
```

Use Secrets Manager references for secret values and plain ECS environment values only for non-sensitive configuration.

## 12. Cost Estimate

Typical staging: approximately USD 250-600/month.

Typical small production baseline: approximately USD 900-2,500/month, driven mainly by NAT gateways, ALB, Multi-AZ RDS, ElastiCache, ECS task count, CloudWatch ingestion, and AI-provider usage.

Costs scale with message volume, AI tokens, media transfer, database size, and high-availability requirements. Use AWS Pricing Calculator with measured staging traffic before launch.

## 13. AWS Reference Links

- [RDS PostgreSQL TLS and `rds.force_ssl`](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL.Concepts.General.SSL.html)
- [ECS Secrets Manager injection](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-envvar-secrets-manager.html)
- [CloudFront OAC for private S3 origins](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html)
- [ElastiCache in-transit encryption](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/in-transit-encryption.html)
- [ElastiCache Multi-AZ automatic failover](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/AutoFailover.html)
- [GitHub Actions OIDC for AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)
