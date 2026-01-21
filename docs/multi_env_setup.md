# Multi-Environment Setup Guide for kAIzen Coach

## Overview
Your app now supports multiple environments (dev, staging, prod, demo) with separate GCP projects for cost tracking and security isolation.

<!-- toc -->

- [Strava OAuth Configuration](#strava-oauth-configuration)
  * [For Your Environments (dev, staging, prod)](#for-your-environments-dev-staging-prod)
  * [For Demo Instances (Friends)](#for-demo-instances-friends)
- [Application Configuration (config.py)](#application-configuration-configpy)
- [GCP Setup Steps](#gcp-setup-steps)
  * [1. Create GCP Projects](#1-create-gcp-projects)
  * [2. Enable Billing for Each Project](#2-enable-billing-for-each-project)
  * [3. Enable Vertex AI API for Each Project](#3-enable-vertex-ai-api-for-each-project)
  * [4. Create Service Accounts (one per project)](#4-create-service-accounts-one-per-project)
- [AWS Secrets Manager Setup](#aws-secrets-manager-setup)
  * [1. Update Secret Structure](#1-update-secret-structure)
  * [2. Populate Secrets with Values](#2-populate-secrets-with-values)
- [Local Development](#local-development)
  * [1. Update Your Local .env](#1-update-your-local-env)
  * [2. Alternative: Use Application Default Credentials](#2-alternative-use-application-default-credentials)
- [Deployment Configuration](#deployment-configuration)
  * [App Runner Environment Variables](#app-runner-environment-variables)
- [Terraform Configuration Reference](#terraform-configuration-reference)
  * [Environment Variable](#environment-variable)
  * [Resource Naming with Conditional Logic](#resource-naming-with-conditional-logic)
  * [Resource Tagging](#resource-tagging)
  * [4. Update App Runner Config](#4-update-app-runner-config)
- [Cost Tracking](#cost-tracking)
  * [In AWS Cost Explorer](#in-aws-cost-explorer)
  * [In GCP Billing](#in-gcp-billing)
- [Common Issues & Troubleshooting](#common-issues--troubleshooting)
  * [Billing Not Enabled Error](#billing-not-enabled-error)
  * [Service Account Permission Issues](#service-account-permission-issues)
  * [Vertex AI API Not Enabled](#vertex-ai-api-not-enabled)
- [Migration Checklist](#migration-checklist)
- [Testing Each Environment](#testing-each-environment)
  * [Critical: Update Hardcoded AWS Resources](#critical-update-hardcoded-aws-resources)
  * [Local Dev](#local-dev)
  * [Staging](#staging)
  * [Prod](#prod)

<!-- tocstop -->

**Environment Logic:**
- Local dev: `FLASK_ENV=development` automatically means 'dev' environment
- Production deployments: `FLASK_ENV=production` + `ENVIRONMENT` variable (prod/staging/demo-xxx)

## Strava OAuth Configuration

### For Your Environments (dev, staging, prod)

You can use a **single Strava API app** for all your environments:

1. Go to https://www.strava.com/settings/api
2. Edit your existing application (or create new if needed)
3. In "Authorization Callback Domain", add your specific callback URLs:
   ```
   kaizencoach.training
   ```

localhost & 127.0.0.1 are allow listed by default

**Note:** Strava API apps are limited to 1 user (the owner) by default unless you apply for production access.

### For Demo Instances (Friends)

Since Strava limits apps to 1 user, each friend needs their own Strava API app:

process detailed [here](https://docs.google.com/document/d/1Bef3STD8HHJ_RzrM8mPvkXwhotWv2xqkqkjWkT9UyV0/edit?tab=t.0)

1. **Friend creates Strava API app:**
   - Go to https://www.strava.com/settings/api
   - Create new application
   - Set callback domain to their demo instance URL (e.g., `demo-john.kaizencoach.training`)

2. **Friend provides you:**
   - `STRAVA_CLIENT_ID`
   - `STRAVA_CLIENT_SECRET`

3. **You configure their demo:**
   - Store credentials in AWS Secrets Manager under `kaizencoach/demo-john/app-secrets`
   - Add `STRAVA_REDIRECT_URI` environment variable to their deployment
   - Deploy with `ENVIRONMENT=demo-john`

**Alternative:** Apply for Strava API production access to support multiple users with one app.

## Application Configuration (config.py)

**⚠️ CRITICAL: Update config.py for every new environment BEFORE building Docker image!**

The application needs to know about each environment for OAuth and GCP to work correctly.

**Edit `config.py` to add new environments:**

```python
# Around line 35-39: Add callback URLs for OAuth
REDIRECT_URIS = {
    'dev': 'http://127.0.0.1:5000/callback',
    'staging': 'https://staging.kaizencoach.training/callback',
    'prod': 'https://www.kaizencoach.training/callback',
    'demo-shane': 'https://demo-shane.kaizencoach.training/callback',  # ADD NEW
}

# Around line 45-50: Add GCP project IDs  
GCP_PROJECTS = {
    'dev': 'kaizencoach-dev',
    'staging': 'kaizencoach-staging',
    'prod': 'kaizencoach-prod',
    'demo': 'kaizencoach-demo',
    'demo-shane': 'kaizencoach-shane',  # ADD NEW
}
```

**What These Do:**
- `REDIRECT_URIS`: Tells Strava OAuth where to send users after authentication
- `GCP_PROJECTS`: Maps environment name to GCP project for Vertex AI

**Without These:**
- ❌ OAuth callback errors ("redirect_uri mismatch") 
- ❌ App starts with wrong GCP project → AI features fail
- ❌ Need to rebuild and redeploy to fix

**When to Update:**
- ✅ Before building Docker image for new environment
- ✅ Before deploying to new domain
- ✅ Any time you add a demo instance

## GCP Setup Steps

### 1. Create GCP Projects
```bash
gcloud projects create kaizencoach-dev --name="kAIzen Coach - Development"
gcloud projects create kaizencoach-staging --name="kAIzen Coach - Staging"
gcloud projects create kaizencoach-prod --name="kAIzen Coach - Production"
gcloud projects create kaizencoach-demo --name="kAIzen Coach - Demo"
```
or single project 

```bash
gcloud projects create kaizencoach-PROJECT --name="kAIzen Coach - PROJECT"
```

### 2. Enable Billing for Each Project

**CRITICAL:** Vertex AI requires billing to be enabled. Without this, you'll get "BILLING_DISABLED" errors.

**First, find your billing account ID:**
```bash
gcloud billing accounts list
```

Output looks like:
```
ACCOUNT_ID            NAME                OPEN  MASTER_ACCOUNT_ID
01234-56789-CDEF01  My Billing Account  True
```

**Then link billing to each project:**
```bash
# Set your billing account ID (replace with your actual ID from above)
BILLING_ACCOUNT_ID="xxxxxxxxxx"

# Link billing to all projects
for project in kaizencoach-dev kaizencoach-staging kaizencoach-prod kaizencoach-demo; do
  gcloud billing projects link $project --billing-account=$BILLING_ACCOUNT_ID
  echo "✅ Enabled billing for $project"
done
```

**Or enable it for a single project:**
```bash
gcloud billing projects link kaizencoach-PROJECT --billing-account=$BILLING_ACCOUNT_ID
```

**Verify billing is enabled:**
```bash
for project in kaizencoach-dev kaizencoach-staging kaizencoach-prod kaizencoach-demo; do
  gcloud billing projects describe $project --format="value(billingEnabled)"
done
```
**Or check for a single project:**
```bash
gcloud billing projects describe kaizencoach-PROJECT --format="value(billingEnabled)"
```
Should output "True" for each project.

### 3. Enable Vertex AI API for Each Project
```bash
for project in kaizencoach-dev kaizencoach-staging kaizencoach-prod kaizencoach-demo; do
  gcloud services enable aiplatform.googleapis.com --project=$project
  echo "✅ Enabled Vertex AI for $project"
done
```
**Or check for a single project:**
```bash
gcloud services enable aiplatform.googleapis.com --project=kaizencoach-PROJECT
```

### 4. Create Service Accounts (one per project)
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

## AWS Secrets Manager Setup

### 1. Update Secret Structure
Your secrets are created by Terraform with these names:

**Secret Names:**
- `dev-kaizencoach-app-secrets` (if deploying dev to AWS)
- `staging-kaizencoach-app-secrets`
- `my-personal-coach-app-secrets` (prod - legacy name)
- `demo-kaizencoach-app-secrets` (or `demo-{name}-kaizencoach-app-secrets`)

**⚠️ CRITICAL: Each Environment Must Have Unique Secrets**

**Read [SECRETS_GUIDE.md](SECRETS_GUIDE.md) FIRST** - it explains what each secret is and how to generate them.

**NEVER copy secrets between environments!** Generate new values for:
- `FLASK_SECRET_KEY` - Different per environment (security isolation)
- `GARMIN_ENCRYPTION_KEY` - Different per environment (security isolation)
- `STRAVA_VERIFY_TOKEN` - Can be same or different (your choice)
- `STRAVA_CLIENT_ID/SECRET` - Must be different (each environment has its own Strava app)
- `GOOGLE_APPLICATION_CREDENTIALS_JSON` - Must be different (each environment has its own GCP service account)

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

**Note:** Terraform creates the secret placeholders. You populate them manually using AWS Console or CLI.

### 2. Populate Secrets with Values

**Generate new secrets first (see [SECRETS_GUIDE.md](SECRETS_GUIDE.md) for details):**
```bash
# Generate NEW Flask secret key for this environment
openssl rand -hex 32

# Generate NEW Garmin encryption key for this environment
openssl rand -base64 32

# Generate NEW Strava verify token (optional - can reuse)
openssl rand -hex 20
```

**Prepare the Service Account JSON:**

Go to https://jsonformatter.org/json-to-one-line and convert the JSON block generated by GCP to a single line JSON

Then go to https://www.freeformatter.com/json-escape.html paste the single line JSON in and escape it.

This will give the correct format for the value for the **GOOGLE_APPLICATION_CREDENTIALS_JSON** key in AWS secretsmanager

**Populate via AWS CLI:**
```bash
# For prod (Terraform already created my-personal-coach-app-secrets)
aws secretsmanager put-secret-value \
  --secret-id my-personal-coach-app-secrets \
  --secret-string file://prod-secrets.json \
  --region eu-west-1

# For staging (Terraform already created staging-kaizencoach-app-secrets)
aws secretsmanager put-secret-value \
  --secret-id staging-kaizencoach-app-secrets \
  --secret-string file://staging-secrets.json \
  --region eu-west-1
```

**Or via AWS Console:**
1. Go to AWS Secrets Manager in eu-west-1
2. Find the secret created by Terraform
3. Click "Retrieve secret value" → "Edit"
4. Paste your JSON content
5. Save

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
**Note:** These are configured in Terraform and automatically set when App Runner service is deployed.

For each environment, the following variables are set:
- `FLASK_ENV=production`
- `ENVIRONMENT=prod` (or staging, demo)

The app will then:
1. Load secrets from appropriate AWS Secrets Manager:
   - Prod: `my-personal-coach-app-secrets` (legacy name)
   - Others: `{ENVIRONMENT}-kaizencoach-app-secrets`
2. Connect to appropriate DynamoDB table:
   - Prod: `my-personal-coach-users` (legacy name)
   - Others: `{ENVIRONMENT}-kaizencoach-users`
3. Use appropriate S3 bucket:
   - Prod: `kaizencoach-data` (legacy name)
   - Others: `{ENVIRONMENT}-kaizencoach-data`
4. Initialize Vertex AI with the appropriate GCP project

## Terraform Configuration Reference

This documents how the Terraform is structured to support multi-environment deployments with conditional legacy naming for prod.

### Environment Variable
```hcl
variable "environment" {
  description = "Environment name (dev, staging, prod, demo)"
  type        = string
}
```

### Resource Naming with Conditional Logic
```hcl
# DynamoDB - prod keeps legacy name
resource "aws_dynamodb_table" "users" {
  name = var.environment == "prod" ? "my-personal-coach-users" : "${var.environment}-kaizencoach-users"
  # ...
}

# S3 - prod keeps legacy name
resource "aws_s3_bucket" "data" {
  bucket = var.environment == "prod" ? "kaizencoach-data" : "${var.environment}-kaizencoach-data"
  # ...
}

# Secrets Manager - prod keeps legacy name
resource "aws_secretsmanager_secret" "app_secrets" {
  name = var.environment == "prod" ? "my-personal-coach-app-secrets" : "${var.environment}-kaizencoach-app-secrets"
  # ...
}
```

**Note:** Production keeps legacy names to avoid data migration. Update in future when ready.

### Resource Tagging
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

## Common Issues & Troubleshooting

### Billing Not Enabled Error

**Symptom:**
```
Error generating content from prompt: 403 This API method requires billing to be enabled. 
Please enable billing on project #kaizencoach-prod
[reason: "BILLING_DISABLED"]
```

**Cause:** Vertex AI requires billing to be enabled on the GCP project.

**Solution:**
```bash
# 1. Get your billing account ID
gcloud billing accounts list

# 2. Link billing to the project
gcloud billing projects link kaizencoach-prod --billing-account=YOUR_BILLING_ACCOUNT_ID

# 3. Verify billing is enabled
gcloud billing projects describe kaizencoach-prod --format="value(billingEnabled)"
# Should output: True

# 4. Wait 2-3 minutes for changes to propagate
# 5. Restart App Runner or test locally
```

**Note:** This is the most common issue when setting up new GCP projects!

### Service Account Permission Issues

**Symptom:** Authentication errors or "permission denied" when calling Vertex AI

**Solution:**
```bash
# Verify service account has correct role
gcloud projects get-iam-policy kaizencoach-prod \
  --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount:vertex-ai-sa@kaizencoach-prod.iam.gserviceaccount.com"

# Should show: roles/aiplatform.user
# If not, add it:
gcloud projects add-iam-policy-binding kaizencoach-prod \
  --member="serviceAccount:vertex-ai-sa@kaizencoach-prod.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"
```

### Vertex AI API Not Enabled

**Symptom:** "API not enabled" errors

**Solution:**
```bash
gcloud services enable aiplatform.googleapis.com --project=kaizencoach-prod
```

## Migration Checklist

- [ ] Create 4 GCP projects (dev, staging, prod, demo)
- [ ] **Enable billing on all GCP projects** ⚠️ CRITICAL
- [ ] Enable Vertex AI API on all projects
- [ ] Create service accounts for each project
- [ ] Generate service account keys
- [ ] **Update config.py** (add all environments to REDIRECT_URIS and GCP_PROJECTS) ⚠️ CRITICAL
- [ ] Update AWS Secrets Manager with new structure
- [ ] Deploy Terraform infrastructure for each environment (handles DynamoDB, S3, ECR, App Runner, etc.)
- [ ] Test local dev with kaizencoach-dev project
- [ ] Deploy to staging and test
- [ ] Deploy prod (infrastructure already exists, just config updates)
- [ ] Enable cost allocation tags in AWS Billing

**Future cleanup (track in GitHub issue):**
- [ ] Migrate prod DynamoDB: `my-personal-coach-users` → `kaizencoach-users-prod` (via Terraform)
- [ ] Migrate prod S3: `kaizencoach-data` → `kaizencoach-data-prod` (via Terraform)
- [ ] Update config.py to remove conditional naming (use consistent pattern)
- [ ] Rename prod ECR repo: `my-personal-coach-app` → `prod-kaizencoach-app` (optional)

## Testing Each Environment

### Critical: Update Hardcoded AWS Resources

Before deploying to staging/demo, ensure these files use `Config` instead of hardcoded values:

**Files that need updating:**
1. `data_manager.py` - Line 92-93: Use `Config.DYNAMODB_TABLE` and `Config.AWS_REGION`
2. `s3_manager.py` - Line 11-13: Use `Config.S3_BUCKET` and `Config.AWS_REGION`

These files will work in dev (local file backend) but fail in staging/prod if not updated.

### Local Dev
```bash
# Just run normally - FLASK_ENV=development automatically uses 'dev'
python app.py
# Should connect to kaizencoach-dev GCP project
# Uses dev AWS resources (DynamoDB, S3) or local development setup
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