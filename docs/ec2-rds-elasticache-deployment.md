# Pulse Engine EC2 + RDS + ElastiCache Deployment Guide

This guide deploys this repository on one EC2 instance with Docker Compose, RDS PostgreSQL for data, ElastiCache Redis/Valkey for queues/cache, S3 for media, and an Application Load Balancer (ALB) for HTTPS.

## Architecture

- EC2 runs Docker containers from `production docker-compose.yml`.
- Nginx is the only public container on the instance. It routes `/` to `frontend`, `/api/*` to `gateway`, and `/socket.io/*` to `customer`.
- RDS PostgreSQL replaces the local `postgres` container.
- ElastiCache Redis/Valkey replaces the local `redis` container.
- S3 replaces local upload storage. Production code requires `MEDIA_STORAGE_BACKEND=s3`.
- WhatsApp QR bridge runs as an internal container and stores linked-device session state in the `whatsapp-bridge-auth` volume.
- `followup-scheduler` runs as a single worker container for post-conversation order follow-ups.

## AWS Resources

1. Create a VPC with at least two public subnets and two private subnets.
2. Create security groups:
   - `pulse-alb-sg`: inbound 80/443 from the internet.
   - `pulse-ec2-sg`: inbound 80 only from `pulse-alb-sg`; SSH only from your admin IP.
   - `pulse-rds-sg`: inbound 5432 only from `pulse-ec2-sg`.
   - `pulse-cache-sg`: inbound 6379 only from `pulse-ec2-sg`.
3. Create RDS PostgreSQL 16 or 17 in private subnets. Use a parameter group and backups appropriate for production.
4. Create ElastiCache Redis OSS or Valkey in private subnets. Use TLS and AUTH token. If you use `/0`, `/1`, `/2`, `/3` Redis URLs as this app does, use cluster mode disabled.
5. Create an S3 bucket for media and attach an EC2 instance role that allows object read/write/delete for that bucket.
6. Create an ACM certificate for your app domain and attach it to an ALB HTTPS listener.

Official references:
- Docker Engine on Ubuntu: https://docs.docker.com/engine/install/ubuntu/
- RDS PostgreSQL extensions and pgvector support: https://docs.aws.amazon.com/AmazonRDS/latest/PostgreSQLReleaseNotes/postgresql-extensions.html
- ElastiCache in-transit encryption: https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/in-transit-encryption.html
- ALB HTTPS certificates: https://docs.aws.amazon.com/elasticloadbalancing/latest/application/https-listener-certificates.html

## EC2 Setup

Use Ubuntu 24.04 LTS or 22.04 LTS. A practical starting size is `t3.large` or `t3.xlarge`; use larger if running WhatsApp QR sessions and AI traffic on the same host.

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git postgresql-client

sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker ubuntu
```

Log out and back in, then verify:

```bash
docker --version
docker compose version
```

## Application Files

```bash
sudo mkdir -p /opt/pulse-engine
sudo chown -R ubuntu:ubuntu /opt/pulse-engine
cd /opt/pulse-engine
git clone <YOUR_REPO_URL> .
cp .env.production.example .env.production
mkdir -p secrets
```

Put the Google Vertex service account JSON at the path configured by `GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH`, for example:

```bash
nano /opt/pulse-engine/secrets/gcp-vertex-sa.json
chmod 600 /opt/pulse-engine/secrets/gcp-vertex-sa.json
```

If you use Gemini API instead of Vertex, set `GOOGLE_GENAI_USE_VERTEXAI=false`, `VERTEX_AI_ENABLED=false`, set `GEMINI_API_KEY`, and remove the `gcp_vertex_sa` secret usage from the Compose file.

## Production Environment

Edit `.env.production`:

```bash
nano .env.production
```

Required values:

- `FRONTEND_URL`, `BACKEND_PUBLIC_URL`, `REACT_APP_BACKEND_URL`, `REACT_APP_SOCKET_URL`: use your HTTPS app domain.
- `DATABASE_URL`: RDS PostgreSQL connection string with `sslmode=require`.
- `REDIS_URL`, `CACHE_REDIS_URL`, `RATE_LIMIT_REDIS_URL`, `BACKGROUND_QUEUE_URL`: ElastiCache URLs, preferably `rediss://` with AUTH token.
- `JWT_SECRET`, `INTERNAL_SERVICE_SECRET`, tenant keys/salts, webhook secrets, Meta encryption key.
- `AWS_S3_BUCKET`, `AWS_S3_REGION`.
- Stripe, Google OAuth, Facebook OAuth, Brevo/SMTP, Vertex/Gemini settings.

Generate strong local secrets:

```bash
openssl rand -hex 32
openssl rand -base64 48
uuidgen
```

## Database Bootstrap

From EC2, test RDS connectivity:

```bash
psql "$DATABASE_URL" -c "select version();"
```

Create pgvector if your RDS user has permission. The schema file handles missing pgvector gracefully, but semantic vector search requires it:

```bash
psql "$DATABASE_URL" -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

Apply the consolidated schema:

```bash
psql -v ON_ERROR_STOP=1 "$DATABASE_URL" -f backend/sql_schema.sql
```

Verify:

```bash
psql "$DATABASE_URL" -c "select count(*) as tables from information_schema.tables where table_schema='public';"
psql "$DATABASE_URL" -c "select extname from pg_extension where extname in ('vector','pgcrypto');"
```

## Build And Start

Validate the Compose model:

```bash
docker compose --env-file .env.production -f "production docker-compose.yml" config >/tmp/pulse-compose-rendered.yml
```

Build and start:

```bash
docker compose --env-file .env.production -f "production docker-compose.yml" up -d --build
```

Check health:

```bash
docker compose --env-file .env.production -f "production docker-compose.yml" ps
curl -f http://127.0.0.1/nginx-health
curl -f http://127.0.0.1/api/healthz
```

Logs:

```bash
docker compose --env-file .env.production -f "production docker-compose.yml" logs -f --tail 100 gateway
docker compose --env-file .env.production -f "production docker-compose.yml" logs -f --tail 100 customer
docker compose --env-file .env.production -f "production docker-compose.yml" logs -f --tail 100 followup-scheduler
```

## ALB And DNS

1. Create an ALB in public subnets.
2. Create a target group pointing to the EC2 instance on port 80.
3. Add an HTTPS listener on 443 using your ACM certificate.
4. Forward HTTPS traffic to the target group.
5. Redirect HTTP 80 to HTTPS at the ALB.
6. Create a Route 53 alias record for your domain to the ALB.

The EC2 security group should allow inbound port 80 only from the ALB security group, not from the public internet.

## OAuth, Stripe, Meta, And Webhooks

Set callback/webhook URLs:

- Google OAuth callback: `https://app.example.com/api/auth/google/callback`
- Facebook OAuth callback: `https://app.example.com/api/auth/facebook/callback`
- Stripe webhook: `https://app.example.com/api/billing/webhook`
- Meta webhook: `https://app.example.com/api/webhook/meta`
- WhatsApp webhook path used by the bridge: `/api/webhook/meta/whatsapp`

Meta Cloud API credentials are stored encrypted by the application using `META_CREDENTIALS_ENCRYPTION_KEY`. Do not rotate that key without a re-encryption plan.

## Smoke Test

```bash
curl -f https://app.example.com/
curl -f https://app.example.com/api/healthz
docker compose --env-file .env.production -f "production docker-compose.yml" exec gateway \
  python /app/backend/scripts/customer_service_qa_runner.py \
  --base-url http://127.0.0.1:8000 \
  --wait 0 \
  --max-conversations 1 \
  --output-json /tmp/customer_service_qa_10.json
```

Confirm the runner prints `PASSED: 10 | FAILED: 0`.

## Updates

```bash
cd /opt/pulse-engine
git pull
docker compose --env-file .env.production -f "production docker-compose.yml" build
docker compose --env-file .env.production -f "production docker-compose.yml" up -d
docker compose --env-file .env.production -f "production docker-compose.yml" ps
```

Run `psql -v ON_ERROR_STOP=1 "$DATABASE_URL" -f backend/sql_schema.sql` only for first bootstrap. For later schema changes, use the project migration flow if you keep production data.

## Backups And Operations

- Enable automated RDS backups and test restore.
- Enable RDS deletion protection.
- Enable S3 versioning for media if retention matters.
- Ship Docker logs to CloudWatch if this EC2 instance will be long-lived.
- Monitor ALB 5xx, EC2 CPU/memory/disk, RDS connections/CPU/storage, and ElastiCache memory/evictions.
- Rotate application secrets on a planned schedule; rotate `META_CREDENTIALS_ENCRYPTION_KEY` only with data migration.

## Rollback

Use image tags instead of always deploying `latest`:

```bash
PULSE_IMAGE_TAG=2026-05-31 docker compose --env-file .env.production -f "production docker-compose.yml" up -d
```

If a release fails after containers start:

```bash
docker compose --env-file .env.production -f "production docker-compose.yml" logs --tail 200
git checkout <previous_commit>
docker compose --env-file .env.production -f "production docker-compose.yml" up -d --build
```
