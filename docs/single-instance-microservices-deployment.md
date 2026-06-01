# Pulse Engine single-instance deployment guide

This guide deploys the current microservice stack on one 16 vCPU / 32 GB RAM
Linux instance for a presentation. It intentionally does not use RDS,
ElastiCache, S3, ECS, or Kubernetes. Postgres with pgvector, Redis, uploads,
and all services run through Docker Compose on the instance.

Use the root `docker-compose.yml`. Do not use `production docker-compose.yml`
for this setup because that file expects managed RDS, ElastiCache, S3, and
external production secrets.

## Services started

The Compose stack starts:

- `postgres` with pgvector
- `redis`
- `gateway`
- `frontend`
- `auth`
- `user`
- `customer`
- `lead`
- `product`
- `ai`
- `agent-orchestrator`
- `analytics`
- `data-pipeline`
- `notification`
- `identity`
- `super-admin`
- `email-campaign`
- `whatsapp-bridge`

## Instance setup

On Ubuntu 22.04/24.04:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git nginx
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo tee /etc/apt/keyrings/docker.asc >/dev/null
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
newgrp docker
```

Recommended OS limits:

```bash
sudo sysctl -w vm.max_map_count=262144
sudo sysctl -w fs.file-max=1048576
```

## Clone and configure

```bash
git clone https://github.com/BilalHq38/pulse_v3.git
cd pulse_v3
git checkout bilal4
touch .env
```

Create `.env` from the template below and replace all secrets before
deploying. The root `docker-compose.yml` also references `.env` through
`env_file`, so keep the file name as `.env`.

Edit `.env` for the instance:

```dotenv
ENVIRONMENT=presentation
APP_ENV=presentation
NODE_ENV=production

JWT_SECRET=CHANGE_ME_64_HEX
INTERNAL_SERVICE_SECRET=CHANGE_ME_64_HEX
DEFAULT_TENANT_ID=default
DEFAULT_TENANT_API_KEY=CHANGE_ME_64_HEX
DEFAULT_TENANT_SALT=CHANGE_ME_32_HEX
TENANT_API_KEYS=default:CHANGE_ME_64_HEX
TENANT_SALTS=default:CHANGE_ME_32_HEX
IDENTITY_ADMIN_EMAIL=admin@example.com
IDENTITY_ADMIN_PASSWORD=CHANGE_ME_STRONG_PASSWORD
META_WEBHOOK_SECRET=CHANGE_ME_32_HEX
WEB_CHAT_WEBHOOK_SECRET=CHANGE_ME_32_HEX
EXTERNAL_WEBHOOK_SECRET=CHANGE_ME_32_HEX

FRONTEND_URL=https://YOUR_DOMAIN_OR_IP
APP_URL=https://YOUR_DOMAIN_OR_IP
BACKEND_PUBLIC_URL=https://YOUR_DOMAIN_OR_IP
PUBLIC_BACKEND_URL=https://YOUR_DOMAIN_OR_IP
REACT_APP_BACKEND_URL=https://YOUR_DOMAIN_OR_IP
REACT_APP_SOCKET_URL=https://YOUR_DOMAIN_OR_IP

POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_SSLMODE=disable
POSTGRES_USER=pulse_engine_app
POSTGRES_PASSWORD=CHANGE_ME_DB_PASSWORD
POSTGRES_DB=pulse_engine
DATABASE_URL=postgresql://pulse_engine_app:CHANGE_ME_DB_PASSWORD@postgres:5432/pulse_engine

REDIS_URL=redis://redis:6379/0
CACHE_REDIS_URL=redis://redis:6379/1
RATE_LIMIT_REDIS_URL=redis://redis:6379/2
BACKGROUND_QUEUE_URL=redis://redis:6379/3

STORAGE_BACKEND=local
MEDIA_STORAGE_BACKEND=local
UPLOAD_STORAGE_DIR=/app/uploads

GOOGLE_GENAI_USE_VERTEXAI=false
VERTEX_AI_ENABLED=false
GEMINI_API_KEY=CHANGE_ME

STRIPE_ENABLED=false
STRIPE_OPTIONAL=false
DEMO_MODE=false
LOCAL_ML_ENABLED=false
```

Replace every `CHANGE_ME` secret before starting. Keep `POSTGRES_PASSWORD`
and the password inside `DATABASE_URL` identical.

Generate strong values with:

```bash
openssl rand -hex 32
```

For this single-instance presentation setup, prefer the Gemini Developer API
key path above. The root Compose file intentionally does not mount cloud
service-account files.

## Build and start

```bash
docker compose -p pulse-v3 -f docker-compose.yml --env-file .env build
docker compose -p pulse-v3 -f docker-compose.yml --env-file .env up -d
```

Check health:

```bash
docker compose -p pulse-v3 -f docker-compose.yml --env-file .env ps
curl -f http://127.0.0.1:8000/api/healthz
curl -f http://127.0.0.1:3000
```

Watch logs during the demo:

```bash
docker compose -p pulse-v3 -f docker-compose.yml --env-file .env logs -f --tail=100 gateway customer ai whatsapp-bridge
```

## Optional Nginx reverse proxy

If you use a domain, point DNS A record to the instance, then use Nginx:

```nginx
server {
    listen 80;
    server_name YOUR_DOMAIN;

    client_max_body_size 25m;

    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location /socket.io/ {
        proxy_pass http://127.0.0.1:8003/socket.io/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable it:

```bash
sudo nano /etc/nginx/sites-available/pulse-engine
sudo ln -s /etc/nginx/sites-available/pulse-engine /etc/nginx/sites-enabled/pulse-engine
sudo nginx -t
sudo systemctl reload nginx
```

For HTTPS, install Certbot and issue a certificate:

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d YOUR_DOMAIN
```

## Presentation operations

Restart without rebuilding:

```bash
docker compose -p pulse-v3 -f docker-compose.yml --env-file .env up -d
```

Rebuild after code changes:

```bash
git pull
docker compose -p pulse-v3 -f docker-compose.yml --env-file .env build
docker compose -p pulse-v3 -f docker-compose.yml --env-file .env up -d --no-build
```

Backup local Postgres:

```bash
docker compose -p pulse-v3 -f docker-compose.yml --env-file .env exec postgres pg_dump -U pulse_engine_app pulse_engine > pulse_engine_backup.sql
```

Stop the stack:

```bash
docker compose -p pulse-v3 -f docker-compose.yml --env-file .env down
```

Do not run `down -v` unless you intentionally want to delete Postgres, Redis,
uploads, WhatsApp bridge auth, and cached model volumes.

## Demo checklist

- `docker compose ps` shows all services healthy.
- `https://YOUR_DOMAIN/api/healthz` returns healthy.
- Frontend loads at `https://YOUR_DOMAIN`.
- Web chat sends and receives a message.
- Product list messages preserve line breaks.
- Image requests send image messages without a second fallback text.
- WhatsApp bridge shows ready/QR state in logs if using QR mode.
- Gateway logs show `conversation_engine.orchestrator` for customer replies.
