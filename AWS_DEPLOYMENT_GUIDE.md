# Pulse Engine — AWS Production Deployment Guide

Complete step-by-step guide from a brand-new AWS account to a running production deployment.

---

## Prerequisites

| Tool | Minimum Version | Install |
|------|----------------|---------|
| AWS CLI | 2.x | `brew install awscli` / [aws.amazon.com/cli](https://aws.amazon.com/cli/) |
| Docker | 24.x | [docs.docker.com](https://docs.docker.com/engine/install/) |
| Terraform | 1.7+ | `brew install terraform` |
| psql | 15+ | `brew install postgresql` |
| jq | 1.6+ | `brew install jq` |

```bash
aws configure   # Enter Access Key ID, Secret, region (e.g. us-east-1), output json
aws sts get-caller-identity   # Verify credentials
```

---

## 1. Account Bootstrapping

### 1.1 Enable Cost Alerts
```bash
aws budgets create-budget --account-id $(aws sts get-caller-identity --query Account --output text) \
  --budget '{"BudgetName":"pulse-monthly","BudgetLimit":{"Amount":"500","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}' \
  --notifications-with-subscribers '[{"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":80},"Subscribers":[{"SubscriptionType":"EMAIL","Address":"YOUR_EMAIL"}]}]'
```

### 1.2 Create IAM Roles
```bash
# ECS Execution Role (pulls images from ECR, reads Secrets Manager)
aws iam create-role --role-name pulse-ecs-execution-role \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

aws iam attach-role-policy --role-name pulse-ecs-execution-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy

# Allow Secrets Manager read
aws iam put-role-policy --role-name pulse-ecs-execution-role \
  --policy-name SecretsManagerRead \
  --policy-document '{
    "Version":"2012-10-17",
    "Statement":[{
      "Effect":"Allow",
      "Action":["secretsmanager:GetSecretValue","secretsmanager:DescribeSecret"],
      "Resource":"arn:aws:secretsmanager:*:*:secret:pulse/*"
    }]
  }'

# ECS Task Role (app-level permissions: S3, SES, etc.)
aws iam create-role --role-name pulse-ecs-task-role \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

aws iam put-role-policy --role-name pulse-ecs-task-role \
  --policy-name PulseAppPermissions \
  --policy-document '{
    "Version":"2012-10-17",
    "Statement":[
      {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject"],"Resource":"arn:aws:s3:::pulse-media-*/*"},
      {"Effect":"Allow","Action":["s3:ListBucket"],"Resource":"arn:aws:s3:::pulse-media-*"},
      {"Effect":"Allow","Action":["ssmmessages:*","ssm:UpdateInstanceInformation","ec2messages:*"],"Resource":"*"}
    ]
  }'
```

---

## 2. Networking (VPC)

```bash
REGION="us-east-1"
VPC_CIDR="10.0.0.0/16"

# Create VPC
VPC_ID=$(aws ec2 create-vpc --cidr-block $VPC_CIDR --region $REGION \
  --tag-specifications 'ResourceType=vpc,Tags=[{Key=Name,Value=pulse-prod-vpc}]' \
  --query 'Vpc.VpcId' --output text)

aws ec2 modify-vpc-attribute --vpc-id $VPC_ID --enable-dns-hostnames

# Internet Gateway
IGW_ID=$(aws ec2 create-internet-gateway \
  --tag-specifications 'ResourceType=internet-gateway,Tags=[{Key=Name,Value=pulse-igw}]' \
  --query 'InternetGateway.InternetGatewayId' --output text)
aws ec2 attach-internet-gateway --vpc-id $VPC_ID --internet-gateway-id $IGW_ID

# Public Subnets (for ALB)
PUB_SUBNET_1=$(aws ec2 create-subnet --vpc-id $VPC_ID --cidr-block 10.0.1.0/24 \
  --availability-zone ${REGION}a --query 'Subnet.SubnetId' --output text)
PUB_SUBNET_2=$(aws ec2 create-subnet --vpc-id $VPC_ID --cidr-block 10.0.2.0/24 \
  --availability-zone ${REGION}b --query 'Subnet.SubnetId' --output text)

# Private Subnets (for ECS, RDS, Redis)
PRIV_SUBNET_1=$(aws ec2 create-subnet --vpc-id $VPC_ID --cidr-block 10.0.10.0/24 \
  --availability-zone ${REGION}a --query 'Subnet.SubnetId' --output text)
PRIV_SUBNET_2=$(aws ec2 create-subnet --vpc-id $VPC_ID --cidr-block 10.0.11.0/24 \
  --availability-zone ${REGION}b --query 'Subnet.SubnetId' --output text)

# NAT Gateway (private subnets need outbound internet for ECR pulls, AI APIs)
EIP_ALLOC=$(aws ec2 allocate-address --domain vpc --query 'AllocationId' --output text)
NAT_GW=$(aws ec2 create-nat-gateway --subnet-id $PUB_SUBNET_1 \
  --allocation-id $EIP_ALLOC --query 'NatGateway.NatGatewayId' --output text)
echo "Waiting for NAT gateway..." && aws ec2 wait nat-gateway-available --nat-gateway-ids $NAT_GW

# Route tables
PUB_RT=$(aws ec2 create-route-table --vpc-id $VPC_ID --query 'RouteTable.RouteTableId' --output text)
aws ec2 create-route --route-table-id $PUB_RT --destination-cidr-block 0.0.0.0/0 --gateway-id $IGW_ID
aws ec2 associate-route-table --route-table-id $PUB_RT --subnet-id $PUB_SUBNET_1
aws ec2 associate-route-table --route-table-id $PUB_RT --subnet-id $PUB_SUBNET_2

PRIV_RT=$(aws ec2 create-route-table --vpc-id $VPC_ID --query 'RouteTable.RouteTableId' --output text)
aws ec2 create-route --route-table-id $PRIV_RT --destination-cidr-block 0.0.0.0/0 --nat-gateway-id $NAT_GW
aws ec2 associate-route-table --route-table-id $PRIV_RT --subnet-id $PRIV_SUBNET_1
aws ec2 associate-route-table --route-table-id $PRIV_RT --subnet-id $PRIV_SUBNET_2

echo "VPC=$VPC_ID PRIV_SUBNET_1=$PRIV_SUBNET_1 PRIV_SUBNET_2=$PRIV_SUBNET_2"
```

---

## 3. Security Groups

```bash
# ALB Security Group
ALB_SG=$(aws ec2 create-security-group --group-name pulse-alb-sg \
  --description "Pulse ALB" --vpc-id $VPC_ID --query 'GroupId' --output text)
aws ec2 authorize-security-group-ingress --group-id $ALB_SG --protocol tcp --port 443 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --group-id $ALB_SG --protocol tcp --port 80 --cidr 0.0.0.0/0

# Backend ECS Security Group
BACKEND_SG=$(aws ec2 create-security-group --group-name pulse-backend-sg \
  --description "Pulse Backend ECS" --vpc-id $VPC_ID --query 'GroupId' --output text)
aws ec2 authorize-security-group-ingress --group-id $BACKEND_SG \
  --protocol tcp --port 8000-8020 --source-group $ALB_SG

# RDS Security Group
RDS_SG=$(aws ec2 create-security-group --group-name pulse-rds-sg \
  --description "Pulse RDS" --vpc-id $VPC_ID --query 'GroupId' --output text)
aws ec2 authorize-security-group-ingress --group-id $RDS_SG \
  --protocol tcp --port 5432 --source-group $BACKEND_SG

# Redis Security Group
REDIS_SG=$(aws ec2 create-security-group --group-name pulse-redis-sg \
  --description "Pulse Redis" --vpc-id $VPC_ID --query 'GroupId' --output text)
aws ec2 authorize-security-group-ingress --group-id $REDIS_SG \
  --protocol tcp --port 6379 --source-group $BACKEND_SG

echo "ALB_SG=$ALB_SG BACKEND_SG=$BACKEND_SG RDS_SG=$RDS_SG REDIS_SG=$REDIS_SG"
```

---

## 4. RDS PostgreSQL 16

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# DB Subnet Group
aws rds create-db-subnet-group \
  --db-subnet-group-name pulse-rds-subnet-group \
  --db-subnet-group-description "Pulse RDS Subnet Group" \
  --subnet-ids $PRIV_SUBNET_1 $PRIV_SUBNET_2

# Parameter Group — enable pgvector, enforce SSL
aws rds create-db-cluster-parameter-group \
  --db-cluster-parameter-group-name pulse-pg16 \
  --db-parameter-group-family aurora-postgresql16 \
  --description "Pulse PostgreSQL 16"

# Create Aurora PostgreSQL 16 cluster (Multi-AZ for HA)
aws rds create-db-cluster \
  --db-cluster-identifier pulse-prod-cluster \
  --engine aurora-postgresql \
  --engine-version 16.2 \
  --db-cluster-parameter-group-name pulse-pg16 \
  --master-username pulse_migrator \
  --master-user-password "$(openssl rand -base64 32)" \
  --vpc-security-group-ids $RDS_SG \
  --db-subnet-group-name pulse-rds-subnet-group \
  --storage-encrypted \
  --backup-retention-period 7 \
  --deletion-protection \
  --enable-cloudwatch-logs-exports '["postgresql"]' \
  --no-publicly-accessible

# Writer instance
aws rds create-db-instance \
  --db-instance-identifier pulse-prod-writer \
  --db-cluster-identifier pulse-prod-cluster \
  --db-instance-class db.r7g.large \
  --engine aurora-postgresql \
  --publicly-accessible false

echo "Waiting for RDS cluster (takes ~10 mins)..."
aws rds wait db-cluster-available --db-cluster-identifier pulse-prod-cluster

DB_HOST=$(aws rds describe-db-clusters \
  --db-cluster-identifier pulse-prod-cluster \
  --query 'DBClusters[0].Endpoint' --output text)
echo "DB_HOST=$DB_HOST"
```

### 4.1 Database Setup
```bash
# Connect as master user and create application roles
psql "postgresql://pulse_migrator:PASSWORD@$DB_HOST:5432/postgres" \
  -f backend/scripts/create_db_roles.sql

# Create database
psql "postgresql://pulse_migrator:PASSWORD@$DB_HOST:5432/postgres" \
  -c "CREATE DATABASE pulse_prod WITH OWNER pulse_migrator;"

# Install pgvector
psql "postgresql://pulse_migrator:PASSWORD@$DB_HOST:5432/pulse_prod" \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"

# Run Alembic migrations (via migrator container or locally with DATABASE_URL set)
export DATABASE_URL="postgresql+asyncpg://pulse_migrator:PASSWORD@$DB_HOST:5432/pulse_prod"
cd backend && alembic upgrade head

# Verify migration heads
alembic heads
alembic current
```

### 4.2 Verify RDS Role Security
```bash
# CRITICAL: Verify pulse_app role has NEITHER superuser NOR bypassrls
psql "postgresql://pulse_migrator:PASSWORD@$DB_HOST:5432/pulse_prod" -c \
  "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname IN ('pulse_app', 'pulse_migrator');"
# Expected: rolsuper=false, rolbypassrls=false for pulse_app
```

---

## 5. ElastiCache Redis 7

```bash
# Subnet group
aws elasticache create-cache-subnet-group \
  --cache-subnet-group-name pulse-redis-subnet-group \
  --cache-subnet-group-description "Pulse Redis" \
  --subnet-ids $PRIV_SUBNET_1 $PRIV_SUBNET_2

# Redis 7 cluster (single-AZ for cost; enable Multi-AZ for production HA)
aws elasticache create-cache-cluster \
  --cache-cluster-id pulse-prod-redis \
  --engine redis \
  --engine-version 7.2 \
  --cache-node-type cache.r7g.large \
  --num-cache-nodes 1 \
  --cache-subnet-group-name pulse-redis-subnet-group \
  --security-group-ids $REDIS_SG \
  --snapshot-retention-limit 3 \
  --transit-encryption-enabled \
  --at-rest-encryption-enabled

echo "Waiting for Redis..."
aws elasticache wait cache-cluster-available --cache-cluster-id pulse-prod-redis

REDIS_HOST=$(aws elasticache describe-cache-clusters \
  --cache-cluster-id pulse-prod-redis --show-cache-node-info \
  --query 'CacheClusters[0].CacheNodes[0].Endpoint.Address' --output text)
echo "REDIS_HOST=$REDIS_HOST"
```

---

## 6. S3 Media Bucket

```bash
BUCKET="pulse-media-${ACCOUNT_ID}-prod"
aws s3api create-bucket --bucket $BUCKET --region $REGION \
  --create-bucket-configuration LocationConstraint=$REGION

# Block public access
aws s3api put-public-access-block --bucket $BUCKET \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

# Enable versioning and encryption
aws s3api put-bucket-versioning --bucket $BUCKET \
  --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket $BUCKET \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

echo "MEDIA_BUCKET=$BUCKET"
```

---

## 7. Secrets Manager

```bash
# Store all secrets (replace values with actual secrets)
for secret_name in database_url redis_url secret_key gemini_api_key \
    openai_api_key anthropic_api_key stripe_secret_key stripe_webhook_secret \
    sendgrid_api_key whatsapp_token whatsapp_phone_number_id \
    facebook_app_secret instagram_app_secret; do
  echo "Creating secret pulse/prod/${secret_name}..."
  aws secretsmanager create-secret \
    --name "pulse/prod/${secret_name}" \
    --description "Pulse Engine ${secret_name}" \
    --secret-string "REPLACE_ME"
done

# Update with actual values:
aws secretsmanager put-secret-value \
  --secret-id "pulse/prod/database_url" \
  --secret-string "postgresql+asyncpg://pulse_app:APP_PASSWORD@${DB_HOST}:5432/pulse_prod"

aws secretsmanager put-secret-value \
  --secret-id "pulse/prod/redis_url" \
  --secret-string "rediss://:@${REDIS_HOST}:6379/0"

aws secretsmanager put-secret-value \
  --secret-id "pulse/prod/secret_key" \
  --secret-string "$(openssl rand -base64 64)"
```

---

## 8. ECR Repositories and Image Build

```bash
# Create ECR repositories
for repo in pulse-backend pulse-frontend pulse-node-bridge; do
  aws ecr create-repository --repository-name $repo \
    --image-scanning-configuration scanOnPush=true \
    --encryption-configuration encryptionType=AES256
done

ECR_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# Login and build
aws ecr get-login-password --region $REGION | \
  docker login --username AWS --password-stdin $ECR_REGISTRY

IMAGE_TAG=$(git rev-parse --short HEAD)

# Build and push backend
docker build -t pulse-backend:$IMAGE_TAG ./backend
docker tag pulse-backend:$IMAGE_TAG $ECR_REGISTRY/pulse-backend:$IMAGE_TAG
docker tag pulse-backend:$IMAGE_TAG $ECR_REGISTRY/pulse-backend:latest
docker push $ECR_REGISTRY/pulse-backend:$IMAGE_TAG
docker push $ECR_REGISTRY/pulse-backend:latest

# Build and push frontend
docker build -t pulse-frontend:$IMAGE_TAG ./frontend
docker tag pulse-frontend:$IMAGE_TAG $ECR_REGISTRY/pulse-frontend:$IMAGE_TAG
docker push $ECR_REGISTRY/pulse-frontend:$IMAGE_TAG

echo "ECR_REGISTRY=$ECR_REGISTRY IMAGE_TAG=$IMAGE_TAG"
```

---

## 9. ECS Cluster and Services

```bash
# Create ECS cluster
aws ecs create-cluster --cluster-name pulse-prod \
  --capacity-providers FARGATE FARGATE_SPOT \
  --default-capacity-provider-strategy \
    capacityProvider=FARGATE,weight=1,base=1

# CloudWatch log groups
for service in api customer-service super-admin data-pipeline \
    followup-scheduler background-worker; do
  aws logs create-log-group --log-group-name /pulse/$service
  aws logs put-retention-policy --log-group-name /pulse/$service --retention-in-days 30
done

# Register task definitions (substitute variables first)
export AWS_ACCOUNT_ID=$ACCOUNT_ID AWS_REGION=$REGION \
       ECR_REGISTRY=$ECR_REGISTRY IMAGE_TAG=$IMAGE_TAG \
       PRIVATE_SUBNET_1=$PRIV_SUBNET_1 PRIVATE_SUBNET_2=$PRIV_SUBNET_2 \
       BACKEND_SG_ID=$BACKEND_SG

for task_def in infra/ecs/*.json; do
  # Skip service definitions
  [[ $task_def == *-service.json ]] && continue
  envsubst < $task_def > /tmp/task_def.json
  aws ecs register-task-definition --cli-input-json file:///tmp/task_def.json
  echo "Registered $task_def"
done
```

### 9.1 Create Services

```bash
# Follow-up scheduler — SINGLETON (desiredCount=1, never scale up)
envsubst < infra/ecs/followup-scheduler-service.json > /tmp/scheduler-service.json
aws ecs create-service --cluster pulse-prod --cli-input-json file:///tmp/scheduler-service.json

# Background worker — auto-scaled by queue depth
envsubst < infra/ecs/background-worker-service.json > /tmp/worker-service.json
aws ecs create-service --cluster pulse-prod --cli-input-json file:///tmp/worker-service.json

# Register queue-depth auto-scaling for background worker
aws application-autoscaling register-scalable-target \
  --service-namespace ecs \
  --resource-id service/pulse-prod/pulse-background-worker \
  --scalable-dimension ecs:service:DesiredCount \
  --min-capacity 1 \
  --max-capacity 10

# Scale policy: 100 pending jobs per worker
aws application-autoscaling put-scaling-policy \
  --service-namespace ecs \
  --resource-id service/pulse-prod/pulse-background-worker \
  --scalable-dimension ecs:service:DesiredCount \
  --policy-name pulse-worker-queue-depth-scaling \
  --policy-type TargetTrackingScaling \
  --target-tracking-scaling-policy-configuration \
  '{"TargetValue":100,"CustomizedMetricSpecification":{"MetricName":"RedisStreamPendingJobs","Namespace":"PulseEngine","Dimensions":[{"Name":"Queue","Value":"default"}],"Statistic":"Average"},"ScaleInCooldown":300,"ScaleOutCooldown":60}'
```

---

## 10. Application Load Balancer

```bash
# Create ALB
ALB_ARN=$(aws elbv2 create-load-balancer \
  --name pulse-prod-alb \
  --subnets $PUB_SUBNET_1 $PUB_SUBNET_2 \
  --security-groups $ALB_SG \
  --scheme internet-facing \
  --type application \
  --ip-address-type ipv4 \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text)

ALB_DNS=$(aws elbv2 describe-load-balancers \
  --load-balancer-arns $ALB_ARN \
  --query 'LoadBalancers[0].DNSName' --output text)

echo "ALB_DNS=$ALB_DNS"

# ACM Certificate (must validate via DNS or email first)
CERT_ARN=$(aws acm request-certificate \
  --domain-name "api.yourdomain.com" \
  --subject-alternative-names "*.yourdomain.com" \
  --validation-method DNS \
  --query 'CertificateArn' --output text)

# Create target group for main API
TG_ARN=$(aws elbv2 create-target-group \
  --name pulse-api-tg \
  --protocol HTTP \
  --port 8000 \
  --vpc-id $VPC_ID \
  --target-type ip \
  --health-check-path /health \
  --health-check-interval-seconds 30 \
  --healthy-threshold-count 2 \
  --unhealthy-threshold-count 3 \
  --query 'TargetGroups[0].TargetGroupArn' --output text)

# HTTPS listener (after certificate is validated)
aws elbv2 create-listener \
  --load-balancer-arn $ALB_ARN \
  --protocol HTTPS --port 443 \
  --certificates CertificateArn=$CERT_ARN \
  --default-actions Type=forward,TargetGroupArn=$TG_ARN

# HTTP redirect to HTTPS
aws elbv2 create-listener \
  --load-balancer-arn $ALB_ARN \
  --protocol HTTP --port 80 \
  --default-actions \
  'Type=redirect,RedirectConfig={Protocol=HTTPS,Port=443,StatusCode=HTTP_301}'
```

---

## 11. Database Migration (Alembic)

Run migrations before deploying ECS services to avoid startup failures:

```bash
# Option A: Run from local machine with DATABASE_URL pointing to RDS
export DATABASE_URL="postgresql+asyncpg://pulse_migrator:PASS@${DB_HOST}:5432/pulse_prod"
cd backend
alembic upgrade head
alembic heads   # Should show single head with no (+1) pending
alembic current # Should match latest revision

# Option B: Run as a one-off ECS task (recommended for CI/CD)
aws ecs run-task \
  --cluster pulse-prod \
  --task-definition pulse-migrator \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$PRIV_SUBNET_1],securityGroups=[$BACKEND_SG],assignPublicIp=DISABLED}" \
  --overrides '{"containerOverrides":[{"name":"migrator","command":["alembic","upgrade","head"]}]}'
```

---

## 12. DNS Configuration

```bash
# Create Route 53 hosted zone (or use existing)
HOSTED_ZONE_ID=$(aws route53 create-hosted-zone \
  --name yourdomain.com \
  --caller-reference $(date +%s) \
  --query 'HostedZone.Id' --output text)

# A record for API pointing to ALB
aws route53 change-resource-record-sets \
  --hosted-zone-id $HOSTED_ZONE_ID \
  --change-batch '{
    "Changes":[{
      "Action":"CREATE",
      "ResourceRecordSet":{
        "Name":"api.yourdomain.com",
        "Type":"A",
        "AliasTarget":{"HostedZoneId":"Z35SXDOTRQ7X7K","DNSName":"'$ALB_DNS'","EvaluateTargetHealth":true}
      }
    }]
  }'
```

---

## 13. Post-Deployment Verification

### Health Check All Services
```bash
# API health
curl -sf https://api.yourdomain.com/health | jq .

# Check ECS service stability
aws ecs describe-services --cluster pulse-prod \
  --services pulse-followup-scheduler pulse-background-worker \
  --query 'services[*].{name:serviceName,running:runningCount,desired:desiredCount,deployments:deployments[*].status}'
```

### Cross-Tenant Isolation Test
```bash
# Verify RLS prevents cross-tenant data leakage
psql "postgresql://pulse_app:PASS@$DB_HOST:5432/pulse_prod" << 'EOF'
-- Set context to tenant A
SET app.current_company = 'tenant-a-uuid';
SELECT COUNT(*) FROM analytics_service.raw_events;  -- Should only see tenant A rows

-- Attempt to read tenant B data without setting context
RESET app.current_company;
SELECT COUNT(*) FROM analytics_service.raw_events;  -- Should return 0 (RLS blocks)
EOF
```

### Scheduler Singleton Validation
```bash
# Confirm only 1 scheduler task is running
aws ecs list-tasks --cluster pulse-prod \
  --service-name pulse-followup-scheduler \
  --query 'taskArns' | jq 'length'
# Must return 1

# Verify it's processing (check logs)
aws logs filter-log-events \
  --log-group-name /pulse/followup-scheduler \
  --filter-pattern "followup_scheduler_started" \
  --start-time $(date -d '5 minutes ago' +%s)000
```

---

## 14. Monitoring and Alerting

```bash
# CloudWatch alarm: API error rate > 5%
aws cloudwatch put-metric-alarm \
  --alarm-name pulse-api-error-rate \
  --metric-name HTTPCode_Target_5XX_Count \
  --namespace AWS/ApplicationELB \
  --statistic Sum \
  --period 300 \
  --threshold 50 \
  --comparison-operator GreaterThanThreshold \
  --dimensions Name=LoadBalancer,Value=$ALB_ARN \
  --evaluation-periods 2 \
  --alarm-actions "arn:aws:sns:${REGION}:${ACCOUNT_ID}:pulse-alerts"

# RDS CPU alarm
aws cloudwatch put-metric-alarm \
  --alarm-name pulse-rds-cpu-high \
  --metric-name CPUUtilization \
  --namespace AWS/RDS \
  --statistic Average \
  --period 300 \
  --threshold 80 \
  --comparison-operator GreaterThanThreshold \
  --dimensions Name=DBClusterIdentifier,Value=pulse-prod-cluster \
  --evaluation-periods 3 \
  --alarm-actions "arn:aws:sns:${REGION}:${ACCOUNT_ID}:pulse-alerts"
```

---

## 15. Backup Verification Drill

```bash
# Trigger manual RDS snapshot
aws rds create-db-cluster-snapshot \
  --db-cluster-identifier pulse-prod-cluster \
  --db-cluster-snapshot-identifier pulse-manual-$(date +%Y%m%d)

# List latest snapshots
aws rds describe-db-cluster-snapshots \
  --db-cluster-identifier pulse-prod-cluster \
  --query 'DBClusterSnapshots[*].{id:DBClusterSnapshotIdentifier,status:Status,created:SnapshotCreateTime}' \
  | jq 'sort_by(.created) | reverse | .[0:3]'

# Restore to a test cluster to validate backup integrity (do this in staging)
aws rds restore-db-cluster-from-snapshot \
  --db-cluster-identifier pulse-restore-test \
  --snapshot-identifier pulse-manual-$(date +%Y%m%d) \
  --engine aurora-postgresql \
  --engine-version 16.2 \
  --vpc-security-group-ids $RDS_SG \
  --db-subnet-group-name pulse-rds-subnet-group
```

---

## Estimated Monthly Costs (us-east-1)

| Service | Spec | Est. Cost/mo |
|---------|------|-------------|
| Aurora PostgreSQL | db.r7g.large writer, 1 reader | ~$350 |
| ElastiCache Redis | cache.r7g.large | ~$130 |
| ECS Fargate (API services) | 2 tasks × 1 vCPU / 2GB | ~$180 |
| ECS Fargate (workers/scheduler) | 3 tasks × 0.5 vCPU / 1GB | ~$90 |
| ALB | 1 ALB + data processing | ~$30 |
| NAT Gateway | 10 GB/mo outbound | ~$50 |
| S3 + ECR storage | 100 GB | ~$15 |
| CloudWatch Logs | 50 GB/mo | ~$25 |
| **Total** | | **~$870/mo** |

Staging environment (smaller instances, 1 AZ): ~$250/mo
