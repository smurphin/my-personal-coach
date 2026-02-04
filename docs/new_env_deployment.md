# Environment Bootstrap Guide

This guide documents the process for creating a new environment (staging, demo, etc.) from scratch. This is only needed for **initial environment creation** - subsequent updates use normal `terraform apply`.

<!-- toc -->

  * [Prerequisites](#prerequisites)
  * [Overview](#overview)
  * [Bootstrap Process](#bootstrap-process)
    + [Phase 1: Infrastructure Without DNS](#phase-1-infrastructure-without-dns)
    + [Phase 2: Update Application Configuration](#phase-2-update-application-configuration)
    + [Phase 3: Build and Push Container Image](#phase-3-build-and-push-container-image)
    + [Phase 4: Populate Secrets](#phase-4-populate-secrets)
    + [Phase 5: Recreate App Runner Service](#phase-5-recreate-app-runner-service)
    + [Phase 6: Configure Custom Domain (Two-Stage Apply)](#phase-6-configure-custom-domain-two-stage-apply)
    + [Phase 7: Verify Deployment](#phase-7-verify-deployment)
    + [Phase 8: Setup Strava Webhook Subscription](#phase-8-setup-strava-webhook-subscription)
  * [DNS Configuration Notes](#dns-configuration-notes)
    + [Correct Setup (Recommended)](#correct-setup-recommended)
    + [Incorrect Setup (Avoid)](#incorrect-setup-avoid)
  * [Environment-Specific Variables](#environment-specific-variables)
  * [Common Issues & Troubleshooting](#common-issues--troubleshooting)
    + [App Runner Stays in CREATE_FAILED](#app-runner-stays-in-create_failed)
    + [App Runner Fails After Image Push](#app-runner-fails-after-image-push)
    + [DNS Not Resolving](#dns-not-resolving)
    + [Terraform "for_each" Error on DNS](#terraform-for_each-error-on-dns)
    + [Secrets Not Loading](#secrets-not-loading)
  * [Post-Bootstrap: Normal Operations](#post-bootstrap-normal-operations)
  * [Quick Reference](#quick-reference)
    + [Bootstrap Order](#bootstrap-order)
    + [Time Estimates](#time-estimates)
    + [Account ID Reference](#account-id-reference)
    + [ECR Repository Pattern](#ecr-repository-pattern)
  * [Environment Checklist](#environment-checklist)
  * [Files to Update for New Environments](#files-to-update-for-new-environments)
  * [Next Steps After Bootstrap](#next-steps-after-bootstrap)
  * [Support](#support)

<!-- tocstop -->

## Prerequisites

- AWS CLI configured with appropriate credentials
- Docker installed and running
- Terraform initialized with correct backend configuration
- GCP project created with Vertex AI enabled and service account keys ready
- Environment-specific tfvars file prepared

## Overview

The bootstrap process handles several chicken-and-egg problems:
1. **ECR Registry**: Terraform creates it, but App Runner needs an image in it to start
2. **App Runner Service**: Needs an image to run, but can't create custom domain until running
3. **Custom Domain**: Needs validation records from App Runner before DNS can be configured
4. **Secrets**: Must be manually populated for security (not in Terraform)

## Bootstrap Process

### Phase 1: Infrastructure Without DNS

**Goal:** Create all AWS resources except custom domain configuration

```bash
# 1. Temporarily disable DNS resources
cd infra
mv dns.tf dns.temp

# 2. Create base infrastructure
# This creates: ECR, IAM roles/policies, DynamoDB, S3, Secrets Manager, App Runner
# Note: App Runner will fail (CREATE_FAILED) - this is expected, no image exists yet
terraform apply -var-file=environments/staging/vars.tfvars

# Terraform output will show App Runner in CREATE_FAILED state - ignore this for now
```

**Resources Created:**
- ✅ ECR Repository: `staging-kaizencoach-app`
- ✅ DynamoDB Table: `staging-kaizencoach-users`
- ✅ S3 Bucket: `staging-kaizencoach-data` (with lifecycle, encryption, versioning)
- ✅ IAM Roles & Policies
- ✅ Secrets Manager Secret: `staging-kaizencoach-app-secrets` (empty)
- ⚠️ App Runner Service: Created but failed (no image)

### Phase 2: Update Application Configuration

**⚠️ CRITICAL: Do this BEFORE building the Docker image!**

**Goal:** Add new environment to config.py so OAuth and GCP work correctly

The application needs to know about your new environment BEFORE you build the Docker image. Without this step, you'll get **OAuth callback errors** when users try to log in.

**Edit `config.py` (around lines 35-50):**

```python
# Add your new environment to REDIRECT_URIS
REDIRECT_URIS = {
    'dev': 'http://127.0.0.1:5000/callback',
    'staging': 'https://staging.kaizencoach.training/callback',
    'prod': 'https://www.kaizencoach.training/callback',
    'demo-shane': 'https://demo-shane.kaizencoach.training/callback',  # ADD NEW ENV
}

# Add your new environment to GCP_PROJECTS
GCP_PROJECTS = {
    'dev': 'kaizencoach-dev',
    'staging': 'kaizencoach-staging',
    'prod': 'kaizencoach-prod',
    'demo': 'kaizencoach-demo',
    'demo-shane': 'kaizencoach-shane',  # ADD NEW ENV
}
```

**Why This Matters:**
- `REDIRECT_URIS`: Strava OAuth will reject logins if the callback URL doesn't match
- `GCP_PROJECTS`: Vertex AI will fail if it can't find the correct GCP project

**Consequences of Skipping:**
- ❌ Users get "OAuth callback error" when trying to log in
- ❌ App starts but uses wrong GCP project → AI features fail
- ❌ Have to rebuild Docker image and redeploy

**Verify Your Changes:**
```bash
grep -A 10 "REDIRECT_URIS = {" config.py
grep -A 10 "GCP_PROJECTS = {" config.py
```

### Phase 3: Build and Push Container Image

**Goal:** Provide the image that App Runner needs to start

```bash
# 3. Get ECR login credentials
aws ecr get-login-password --region eu-west-1 | \
  sudo docker login --username AWS --password-stdin \
  321490400104.dkr.ecr.eu-west-1.amazonaws.com

# 4. Build the application image
cd ../  # Back to project root
sudo docker build -t 321490400104.dkr.ecr.eu-west-1.amazonaws.com/staging-kaizencoach-app:latest .

# 5. Push to ECR
sudo docker push 321490400104.dkr.ecr.eu-west-1.amazonaws.com/staging-kaizencoach-app:latest

# Note: Replace 321490400104 with your AWS account ID
```

**Verify Image Exists:**
```bash
aws ecr describe-images \
  --repository-name staging-kaizencoach-app \
  --region eu-west-1
```
### Phase 4: Populate Secrets

**Goal:** Add actual credentials to Secrets Manager

**⚠️ CRITICAL: Generate NEW Secrets for Each Environment**

**DO NOT copy secrets from prod to staging or between environments!**

Before proceeding, read **[SECRETS_GUIDE.md](SECRETS_GUIDE.md)** which explains:
- What each secret is for
- How to generate each one
- Security best practices

**Quick secret generation commands:**
```bash
# Generate NEW Flask secret key (different for each environment!)
openssl rand -hex 32

# Generate NEW Garmin encryption key (different for each environment!)
openssl rand -base64 32

# Generate NEW Strava verify token (can be same or different per environment)
openssl rand -hex 20

# Get Strava credentials from https://www.strava.com/settings/api
# (each environment needs its own Strava app with unique callback domain)

# Format GCP service account JSON as single-line
cat .keys/kaizencoach-staging-sa-key.json | jq -c '.'
```

**Prepare the Service Account JSON:**

Go to [JSON Formatter](https://jsonformatter.org/json-to-one-line) and convert the JSON block generated by GCP to a single line JSON

Then go to [JSON Escaper](https://www.freeformatter.com/json-escape.html) paste the single line JSON in and escape it.

This will give the correct format for the value for the **GOOGLE_APPLICATION_CREDENTIALS_JSON** key in AWS secretsmanager

Prepare the secrets file based on the following template

```json
{
  "STRAVA_CLIENT_ID": "CLIENT ID FROM STRAVA API SETTINGS HERE",
  "STRAVA_CLIENT_SECRET": "CLIENT SECRET FROM STRAVA API SETTINGS HERE",
  "STRAVA_VERIFY_TOKEN": "STRAVA VERIFY TOKEN FROM ABOVE HERE",
  "FLASK_SECRET_KEY": "FLASK SECRET KEY FROM ABOVE HERE",
  "GCP_PROJECT_ID": "GCP PROJECT HERE",
  "GCP_LOCATION": "GCP REGION HERE",
  "GARMIN_ENCRYPTION_KEY": "GARMIN ENCRYPTION KEY FROM ABOVE HERE",
  "GOOGLE_APPLICATION_CREDENTIALS_JSON": "SINGLE LINE JSON HERE"
}
```

# Restart App Runner to pick up secrets
aws apprunner start-deployment \
  --service-arn $(terraform output -raw apprunner_service_arn) \
  --region eu-west-1
```

**Prepare Service Account JSON:**
```bash
# Get the service account JSON from your .keys directory
cat .keys/kaizencoach-staging-sa-key.json

# Format it as a single line (no newlines) for the secrets JSON:
cat .keys/kaizencoach-staging-sa-key.json | jq -c '.'
```

### Phase 5: Recreate App Runner Service

**Goal:** Get App Runner to RUNNING state now that image exists

```bash
# 6. Apply Terraform again - this will replace the failed App Runner service
cd infra
terraform apply -var-file=environments/staging/vars.tfvars

# This time App Runner will:
# - Destroy the failed service
# - Create new service
# - Pull the image from ECR
# - Start successfully and reach RUNNING state
```

**Verify App Runner is Running:**
```bash
# Check in AWS Console: App Runner > staging-kaizencoach-service
# Status should show: RUNNING (not CREATE_FAILED)

# Or via CLI:
aws apprunner describe-service \
  --service-arn $(terraform output -raw apprunner_service_arn) \
  --region eu-west-1 \
  --query 'Service.Status'
```

### Phase 6: Configure Custom Domain (Two-Stage Apply)

**Goal:** Add custom domain and DNS records

```bash
# 7. Re-enable DNS resources
mv dns.temp dns.tf

# 8. Create custom domain association (Stage 1)
# This generates the certificate validation records
terraform apply \
  -var-file=environments/staging/vars.tfvars \
  -target=aws_apprunner_custom_domain_association.main

# Wait ~30 seconds for certificate validation to be ready

# 9. Create DNS validation and A records (Stage 2)
terraform apply -var-file=environments/staging/vars.tfvars

# This creates:
# - CNAME records for certificate validation
# - A record for staging.kaizencoach.training
# - A record for www.staging.kaizencoach.training
```

**Why Two Stages?**
Terraform's `for_each` requires all keys at plan time. App Runner's certificate validation records are only available after the custom domain association is created. This is a Terraform limitation, not a bug.

### Phase 7: Verify Deployment

**Goal:** Confirm everything works

```bash
# 14. Wait for DNS propagation (5-60 minutes)
watch dig staging.kaizencoach.training

# 15. Check SSL certificate status in AWS Console
# App Runner > staging-kaizencoach-service > Custom domains
# Status should show: Active (not Pending validation)

# 16. Test the application
curl https://staging.kaizencoach.training
curl https://www.staging.kaizencoach.training

# 17. Check logs
aws logs tail /aws/apprunner/staging-kaizencoach-service --follow
```

### Phase 8: Setup Strava Webhook Subscription

**Goal:** Enable real-time activity notifications from Strava

**CRITICAL:** Each environment needs its own webhook subscription. This is a manual step and **cannot be automated in Terraform**.

```bash
# 18. Get your Strava credentials from Secrets Manager
aws secretsmanager get-secret-value \
  --secret-id staging-kaizencoach-app-secrets \
  --region eu-west-1 \
  --query SecretString --output text | jq -r '.STRAVA_CLIENT_ID, .STRAVA_CLIENT_SECRET, .STRAVA_VERIFY_TOKEN'

# Note these three values - you'll need them for the webhook setup

# 19. Check for any existing webhook subscriptions
curl -G https://www.strava.com/api/v3/push_subscriptions \
  -d client_id=YOUR_CLIENT_ID \
  -d client_secret=YOUR_CLIENT_SECRET

# If a webhook exists, delete it first (replace SUBSCRIPTION_ID with the id from above):
curl -X DELETE \
  "https://www.strava.com/api/v3/push_subscriptions/SUBSCRIPTION_ID" \
  -F client_id=YOUR_CLIENT_ID \
  -F client_secret=YOUR_CLIENT_SECRET

# 20. Create new webhook subscription
curl -X POST https://www.strava.com/api/v3/push_subscriptions \
  -F client_id=YOUR_CLIENT_ID \
  -F client_secret=YOUR_CLIENT_SECRET \
  -F callback_url=https://staging.kaizencoach.training/strava_webhook \
  -F verify_token=YOUR_VERIFY_TOKEN

# Should respond with:
# {
#   "id": 305097,
#   "callback_url": "https://staging.kaizencoach.training/strava_webhook",
#   "created_at": "2025-12-03T19:00:00Z"
# }

# SAVE THE SUBSCRIPTION ID - you'll need it to manage this webhook later

# 21. Test webhook endpoint
curl "https://staging.kaizencoach.training/strava_webhook?hub.mode=subscribe&hub.challenge=test12345&hub.verify_token=YOUR_VERIFY_TOKEN"

# Should respond with:
# {"hub.challenge": "test12345"}

# 22. Test end-to-end: Edit a Strava activity and watch logs
aws logs tail /aws/apprunner/staging-kaizencoach-service/service --follow --region eu-west-1 | grep "Webhook event"

# Should see: --- Webhook event received: {...} ---
```

**Why Manual?**
- Strava API requires HTTP verification callback during webhook creation
- The endpoint must be live and responding before subscription succeeds
- This is a POST API call, not infrastructure

**For Demo Environments:**
Each demo instance needs:
1. Separate Strava app (unique callback domain)
2. Separate webhook subscription
3. Same process as above, just change the callback_url

## DNS Configuration Notes

**Important:** Use a **single hosted zone** for all environments, not separate zones per subdomain.

### Correct Setup (Recommended)
```
kaizencoach.training hosted zone contains:
├── kaizencoach.training           A → prod App Runner
├── www.kaizencoach.training       A → prod App Runner  
├── staging.kaizencoach.training   A → staging App Runner
├── www.staging.kaizencoach.training A → staging App Runner
└── demo-xxx.kaizencoach.training  A → demo App Runner
```

Your `dns.tf` should use `data.aws_route53_zone.primary` to reference the existing zone, not create new hosted zones.

### Incorrect Setup (Avoid)
```
❌ Separate hosted zones:
├── kaizencoach.training (Zone 1)
└── staging.kaizencoach.training (Zone 2) ← Requires delegation, adds complexity
```

## Environment-Specific Variables

Ensure your `environments/{env}/vars.tfvars` has:

```hcl
# environments/staging/vars.tfvars
app_name        = "kaizencoach"
environment     = "staging"
domain_name     = "staging.kaizencoach.training"
r53_zone_id     = "<ROUTE53_ZONE_ID_FOR_KAIZENCOACH_TRAINING>"  # Look this up in Route 53

common_tags = {
  Application = "kaizencoach"
  Environment = "staging"
  ManagedBy   = "terraform"
  CostCenter  = "kaizencoach-staging"
  Project     = "staging-kaizencoach"
}
```

## Common Issues & Troubleshooting

### App Runner Stays in CREATE_FAILED
**Cause:** No image in ECR
**Solution:** Build and push Docker image (Phase 2)

### App Runner Fails After Image Push
**Cause:** Missing or invalid secrets
**Solution:** Populate secrets in Secrets Manager (Phase 5)

### DNS Not Resolving
**Causes:**
1. DNS propagation not complete (wait 5-60 minutes)
2. Using separate hosted zone without delegation
3. Certificate validation not complete

**Check:**
```bash
# DNS propagation
dig staging.kaizencoach.training

# Certificate status
aws apprunner describe-custom-domains \
  --service-arn $(terraform output -raw apprunner_service_arn) \
  --region eu-west-1
```

### Terraform "for_each" Error on DNS
**Cause:** Trying to create validation records before custom domain association exists
**Solution:** Use two-stage apply (Phase 4, steps 8-9)

### Secrets Not Loading
**Cause:** App Runner cached the empty secrets from initial deploy
**Solution:** Force new deployment after populating secrets:
```bash
aws apprunner start-deployment --service-arn <arn>
```

## Post-Bootstrap: Normal Operations

Once bootstrapped, the environment works like normal:

```bash
# Regular updates (code changes, config changes)
terraform apply -var-file=environments/staging/vars.tfvars

# Single apply works fine after initial setup
# Two-stage DNS apply only needed for bootstrap
```

## Quick Reference

### Bootstrap Order
1. Disable DNS (`mv dns.tf dns.temp`)
2. Apply Terraform (creates ECR, fails App Runner)
3. **Update config.py** (add environment to REDIRECT_URIS and GCP_PROJECTS)
4. Build & push Docker image
5. Apply Terraform again (fixes App Runner)
6. Enable DNS (`mv dns.temp dns.tf`)
7. Apply with target (custom domain association)
8. Apply full (DNS records)
9. Populate secrets (see SECRETS_GUIDE.md)
10. **Setup Strava webhooks** (CRITICAL - manual API call)
11. Verify & test

### Time Estimates
- Terraform applies: ~5 min each
- Docker build & push: ~10 min
- DNS propagation: 5-60 min
- Secrets population: ~5 min
- Webhook setup: ~2 min
- **Total: 50-95 minutes** for complete bootstrap

### Account ID Reference
Throughout this guide, treat any concrete AWS account IDs as **examples only**. When running commands, always substitute your own values:

- `<AWS_ACCOUNT_ID>` – your AWS account ID
- `<AWS_REGION>` – the target AWS region (e.g. `eu-west-1`)

### ECR Repository Pattern
```
<AWS_ACCOUNT_ID>.dkr.ecr.<AWS_REGION>.amazonaws.com/<ENVIRONMENT>-kaizencoach-app:latest
```

## Environment Checklist

Use this checklist when creating a new environment:

- [ ] Create GCP project and service account
- [ ] Prepare environment-specific tfvars file
- [ ] Initialize Terraform with correct backend config
- [ ] Phase 1: Apply without DNS
- [ ] Phase 2: **Update config.py** (REDIRECT_URIS and GCP_PROJECTS) ← CRITICAL
- [ ] Phase 3: Build and push image
- [ ] Phase 4: Populate secrets (see SECRETS_GUIDE.md)
- [ ] Phase 5: Recreate App Runner service (verify RUNNING)
- [ ] Phase 6: Apply DNS (two-stage)
- [ ] Phase 7: Verify deployment
- [ ] Phase 8: Setup Strava webhook subscription (CRITICAL - manual step)
- [ ] Test Strava OAuth login flow
- [ ] Test webhook by editing a Strava activity
- [ ] Verify AI feedback generation works
- [ ] Set up monitoring/alerting
- [ ] Document environment-specific details

## Files to Update for New Environments

When creating a new environment, ensure these files exist/are updated:

```
# Application code (CRITICAL - update BEFORE building Docker image)
config.py
├── REDIRECT_URIS dict       # Add new environment's callback URL
└── GCP_PROJECTS dict         # Add new environment's GCP project ID

# Infrastructure configuration
infra/
├── environments/
│   └── {env}/
│       ├── vars.tfvars         # Environment-specific variables
│       └── backend.tfbackend   # Terraform backend config
└── dns.tf                      # Temporarily renamed during bootstrap

# Service account keys
.keys/
└── kaizencoach-{env}-sa-key.json  # GCP service account key
```

**⚠️ Missing config.py updates will cause OAuth callback errors!**

## Next Steps After Bootstrap

1. Configure GitHub Actions for automated deployments
2. Set up CloudWatch alarms for App Runner metrics
3. Configure AWS Budgets for cost alerts
4. Enable AWS Cost Allocation tags in billing console
5. Add environment to monitoring dashboard
6. Consider implementing scale-to-zero for non-prod environments (see COST_OPTIMIZATION.md)

## Support

If you encounter issues during bootstrap:
1. Check CloudWatch logs for App Runner service
2. Verify ECR image exists and is tagged correctly
3. Confirm secrets are populated in Secrets Manager
4. Check DNS records in Route 53
5. Verify IAM roles have correct permissions

For help, reference:
- `MULTI_ENVIRONMENT_SETUP.md` - Overall architecture
- `CUSTOM_DOMAIN_SETUP.md` - DNS configuration details
- AWS App Runner documentation
- Terraform AWS provider documentation