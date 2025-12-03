# Multi-Environment Setup Guide for kAIzen Coach

## Overview
Your app now supports multiple environments (dev, staging, prod, demo) with separate GCP projects for cost tracking and security isolation.

<!-- toc -->

**Environment Logic:**
- Local dev: `FLASK_ENV=development` automatically means 'dev' environment
- Production deployments: `FLASK_ENV=production` + `ENVIRONMENT` variable (prod/staging/demo-xxx)

## Strava OAuth Configuration

### For Your Environments (dev, staging, prod)

You can use a **single Strava API app** for all your environments:

1. Go to https://www.strava.com/settings/api
2. Edit your existing application (or create new if needed)
3. In "Authorization Callback Domain", add ALL your callback URLs:
   ```
   127.0.0.1, localhost
   www.kaizencoach.training
   staging.kaizencoach.training
   ```

4. Your app will automatically select the correct callback based on environment:
   - dev: `http://127.0.0.1:5000/callback`
   - staging: `https://staging.kaizencoach.training/callback` (if you set up staging subdomain)
   - prod: `https://www.kaizencoach.training/callback`

**Note:** Strava API apps are limited to 1 user (the owner) by default unless you apply for production access.

### For Demo Instances (Friends)

Since Strava limits apps to 1 user, each friend needs their own Strava API app:

1. **Friend creates Strava API app:**
   - Go to https://www.strava.com/settings/api
   - Create new application
   - Set callback domain to their demo instance URL (e.g., `demo-john.kaizencoach.training`)

2. **Friend provides you:**
   - `STRAVA_CLIENT_ID`
   - `STRAVA_CLIENT_SECRET`
   - Their callback URL (e.g., `https://demo-john.kaizencoach.training/callback`)

3. **You configure their demo:**
   - Store credentials in AWS Secrets Manager under `kaizencoach/demo-john/app-secrets`
   - Add `STRAVA_REDIRECT_URI` environment variable to their deployment
   - Deploy with `ENVIRONMENT=demo-john`

**Alternative:** Apply for Strava API production access to support multiple users with one app.

## GCP Setup Steps

### 1. Create GCP Projects
```bash
gcloud projects create kaizencoach-dev --name="kAIzen Coach - Development"
gcloud projects create kaizencoach-staging --name="kAIzen Coach - Staging"
gcloud projects create kaizencoach-prod --name="kAIzen Coach - Production"
gcloud projects create kaizencoach-demo --name="kAIzen Coach - Demo"
```

### 2. Enable Vertex AI API for Each Project
```bash
for project in kaizencoach-dev kaizencoach-staging kaizencoach-prod kaizencoach-demo; do
  gcloud services enable aiplatform.googleapis.com --project=$project
  echo "✅ Enabled Vertex AI for $project"
done
```

### 3. Create Service Accounts (one per project)
```bash
# Example for prod (repeat for staging, demo)
gcloud iam service-accounts create vertex-ai-sa \
  --display-name="Vertex AI Service Account" \
  --project=kaizencoach-prod

# Grant necessary permissions
gcloud projects add-iam-policy-binding kaizencoach-prod \
  --member="serviceAccount:vertex-ai-sa@kaizencoach-prod.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

# Generate key for AWS Secrets Manager
gcloud iam service-accounts keys create prod-sa-key.json \
  --iam-account=vertex-ai-sa@kaizencoach-prod.iam.gserviceaccount.com \
  --project=kaizencoach-prod
```

### 4. Create Service Account for Local Dev
```bash
gcloud iam service-accounts create vertex-ai-sa \
  --display-name="Vertex AI Service Account" \
  --project=kaizencoach-dev

gcloud projects add-iam-policy-binding kaizencoach-dev \
  --member="serviceAccount:vertex-ai-sa@kaizencoach-dev.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

# Generate key for local development
gcloud iam service-accounts keys create ~/kaizencoach-dev-sa-key.json \
  --iam-account=vertex-ai-sa@kaizencoach-dev.iam.gserviceaccount.com \
  --project=kaizencoach-dev
```

## AWS Secrets Manager Setup

### 1. Update Secret Structure
Your secrets now need to be stored per environment:

**Secret Names:**
- `kaizencoach/dev/app-secrets`
- `kaizencoach/staging/app-secrets`
- `kaizencoach/prod/app-secrets`
- `kaizencoach/demo/app-secrets`

**Secret Content (JSON):**
```json
{
  "STRAVA_CLIENT_ID": "your_client_id",
  "STRAVA_CLIENT_SECRET": "your_client_secret",
  "STRAVA_VERIFY_TOKEN": "your_verify_token",
  "FLASK_SECRET_KEY": "your_flask_secret",
  "GOOGLE_APPLICATION_CREDENTIALS_JSON": "<contents of service account JSON>",
  "GARMIN_ENCRYPTION_KEY": "your_garmin_key"
}
```

**For demo instances with friends' Strava apps, also add:**
```json
{
  "STRAVA_CLIENT_ID": "friends_client_id",
  "STRAVA_CLIENT_SECRET": "friends_client_secret",
  "STRAVA_REDIRECT_URI": "https://demo-john.kaizencoach.training/callback",
  ...
}
```

### 2. Create Secrets via AWS CLI
```bash
# For prod
aws secretsmanager create-secret \
  --name kaizencoach/prod/app-secrets \
  --secret-string file://prod-secrets.json \
  --region eu-west-1

# For staging
aws secretsmanager create-secret \
  --name kaizencoach/staging/app-secrets \
  --secret-string file://staging-secrets.json \
  --region eu-west-1

# For demo
aws secretsmanager create-secret \
  --name kaizencoach/demo/app-secrets \
  --secret-string file://demo-secrets.json \
  --region eu-west-1
```

### 3. Prepare the Service Account JSON for Secrets Manager
```bash
# Read the service account JSON and format for insertion
cat prod-sa-key.json | jq -c '.' | sed 's/"/\\"/g'

# Then manually insert into your secrets JSON under GOOGLE_APPLICATION_CREDENTIALS_JSON
```

## AWS Resource Setup

### 1. Create DynamoDB Tables per Environment

**Production table:** Keep existing `my-personal-coach-users` (no changes needed)

**Other environments:**
```bash
# Staging
aws dynamodb create-table \
  --table-name kaizencoach-users-staging \
  --attribute-definitions AttributeName=athlete_id,AttributeType=N \
  --key-schema AttributeName=athlete_id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region eu-west-1

# Dev (if you want separate dev table)
aws dynamodb create-table \
  --table-name kaizencoach-users-dev \
  --attribute-definitions AttributeName=athlete_id,AttributeType=N \
  --key-schema AttributeName=athlete_id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region eu-west-1

# Demo instances (as needed)
aws dynamodb create-table \
  --table-name kaizencoach-users-demo \
  --attribute-definitions AttributeName=athlete_id,AttributeType=N \
  --key-schema AttributeName=athlete_id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region eu-west-1
```

**Note:** Production keeps legacy name `my-personal-coach-users` to avoid data migration during testing phase. This can be cleaned up later.

### 2. Create S3 Buckets per Environment

**Production bucket:** Keep existing `kaizencoach-data` (no changes needed)

**Other environments:**
```bash
aws s3 mb s3://kaizencoach-data-staging --region eu-west-1
aws s3 mb s3://kaizencoach-data-dev --region eu-west-1
aws s3 mb s3://kaizencoach-data-demo --region eu-west-1
```

**Note:** Production keeps existing name `kaizencoach-data` to avoid data migration during testing phase. This can be cleaned up later.

## Local Development

### 1. Update Your Local .env
```bash
FLASK_ENV=development
# No ENVIRONMENT variable needed - automatically uses 'dev'
GOOGLE_APPLICATION_CREDENTIALS=/path/to/kaizencoach-dev-sa-key.json
# ... other credentials
```

### 2. Alternative: Use Application Default Credentials
```bash
# Authenticate with your dev project
gcloud auth application-default login --project=kaizencoach-dev

# Then you don't need GOOGLE_APPLICATION_CREDENTIALS in .env
```

## Deployment Configuration

### App Runner Environment Variables
For each deployed environment, set in App Runner:
- `FLASK_ENV=production`
- `ENVIRONMENT=prod` (or staging, demo)

The app will then:
1. Load secrets from `kaizencoach/{ENVIRONMENT}/app-secrets`
2. Connect to appropriate DynamoDB table:
   - Prod: `my-personal-coach-users` (legacy name)
   - Others: `kaizencoach-users-{ENVIRONMENT}`
3. Use appropriate S3 bucket:
   - Prod: `kaizencoach-data` (legacy name)
   - Others: `kaizencoach-data-{ENVIRONMENT}`
4. Initialize Vertex AI with the appropriate GCP project

## Terraform Updates Needed

### 1. Add Variables
```hcl
variable "environment" {
  description = "Environment name (dev, staging, prod, demo)"
  type        = string
}
```

### 2. Update Resource Names
```hcl
# DynamoDB - prod keeps legacy name
resource "aws_dynamodb_table" "users" {
  name = var.environment == "prod" ? "my-personal-coach-users" : "kaizencoach-users-${var.environment}"
  # ...
}

# S3 - prod keeps legacy name
resource "aws_s3_bucket" "data" {
  bucket = var.environment == "prod" ? "kaizencoach-data" : "kaizencoach-data-${var.environment}"
  # ...
}
```

**Note:** Production keeps legacy names to avoid data migration. Update in future when ready.

### 3. Add Tags
```hcl
locals {
  common_tags = {
    Application = "kaizencoach"
    Environment = var.environment
    ManagedBy   = "terraform"
    CostCenter  = "kaizencoach-${var.environment}"
  }
}

# Apply to all resources
tags = local.common_tags
```

### 4. Update App Runner Config
```hcl
resource "aws_apprunner_service" "app" {
  # ...
  
  source_configuration {
    image_repository {
      image_configuration {
        runtime_environment_variables = {
          FLASK_ENV   = "production"
          ENVIRONMENT = var.environment
        }
      }
    }
  }
}
```

## Cost Tracking

### In AWS Cost Explorer
1. Go to AWS Cost Explorer
2. Filter by tags: `Environment = prod` (or staging, demo)
3. Group by: `Cost Center` or `Environment`

### In GCP Billing
1. Go to Billing > Reports
2. Filter by Project: `kaizencoach-prod` (or staging, demo, dev)
3. Each project appears as a separate line item

This gives you clean separation of costs across all environments!

## Migration Checklist

- [ ] Create 4 GCP projects (dev, staging, prod, demo)
- [ ] Enable Vertex AI API on all projects
- [ ] Create service accounts for each project
- [ ] Generate service account keys
- [ ] Update AWS Secrets Manager with new structure
- [ ] Create DynamoDB tables for staging/dev/demo (prod keeps `my-personal-coach-users`)
- [ ] Create S3 buckets for staging/dev/demo (prod keeps `kaizencoach-data`)
- [ ] Update Terraform with environment variables
- [ ] Test local dev with kaizencoach-dev project
- [ ] Deploy to staging and test
- [ ] Deploy prod (no resource changes, just config updates)
- [ ] Enable cost allocation tags in AWS Billing

**Future cleanup (track in GitHub issue):**
- [ ] Migrate prod DynamoDB: `my-personal-coach-users` → `kaizencoach-users-prod`
- [ ] Migrate prod S3: `kaizencoach-data` → `kaizencoach-data-prod`
- [ ] Update config.py to use consistent naming pattern for all environments

## Testing Each Environment

### Local Dev
```bash
# Just run normally - FLASK_ENV=development automatically uses 'dev'
python app.py
# Should connect to kaizencoach-dev GCP project
# Should use local DynamoDB/S3 or dev versions
```

### Staging
```bash
# Deploy with ENVIRONMENT=staging and FLASK_ENV=production
# Verify logs show: "Environment: staging", "Project: kaizencoach-staging"
```

### Prod
```bash
# Deploy with ENVIRONMENT=prod and FLASK_ENV=production
# Verify logs show: "Environment: prod", "Project: kaizencoach-prod"
```