# Environment Bootstrap Guide

This guide documents the process for creating a new environment (staging, demo, etc.) from scratch. This is only needed for **initial environment creation** - subsequent updates use normal `terraform apply`.

<!-- toc -->

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

### Phase 2: Build and Push Container Image

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

### Phase 3: Recreate App Runner Service

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

### Phase 4: Configure Custom Domain (Two-Stage Apply)

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

### Phase 5: Populate Secrets

**Goal:** Add actual credentials to Secrets Manager

```bash
# 10. Create secrets JSON file (don't commit this!)
cat > staging-secrets.json << 'EOF'
{
  "STRAVA_CLIENT_ID": "your_staging_client_id",
  "STRAVA_CLIENT_SECRET": "your_staging_client_secret",
  "STRAVA_VERIFY_TOKEN": "your_staging_verify_token",
  "STRAVA_REDIRECT_URI": "https://staging.kaizencoach.training/callback",
  "FLASK_SECRET_KEY": "your_flask_secret_key",
  "GOOGLE_APPLICATION_CREDENTIALS_JSON": "<paste entire service account JSON here>",
  "GARMIN_ENCRYPTION_KEY": "your_garmin_encryption_key"
}
EOF

# 11. Upload secrets to Secrets Manager
aws secretsmanager put-secret-value \
  --secret-id staging-kaizencoach-app-secrets \
  --secret-string file://staging-secrets.json \
  --region eu-west-1

# 12. Clean up local secrets file
rm staging-secrets.json

# 13. Restart App Runner to pick up secrets
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

### Phase 6: Verify Deployment

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
r53_zone_id     = "Z0920467KPHM0P6Q2MOG"  # ID of kaizencoach.training zone

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
3. Build & push Docker image
4. Apply Terraform again (fixes App Runner)
5. Enable DNS (`mv dns.temp dns.tf`)
6. Apply with target (custom domain association)
7. Apply full (DNS records)
8. Populate secrets
9. Verify

### Time Estimates
- Terraform applies: ~5 min each
- Docker build & push: ~10 min
- DNS propagation: 5-60 min
- **Total: 45-90 minutes** for complete bootstrap

### Account ID Reference
Replace `321490400104` with your AWS account ID throughout this guide.

### ECR Repository Pattern
```
{ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com/{ENVIRONMENT}-kaizencoach-app:latest
```

Example:
```
321490400104.dkr.ecr.eu-west-1.amazonaws.com/staging-kaizencoach-app:latest
```

## Environment Checklist

Use this checklist when creating a new environment:

- [ ] Create GCP project and service account
- [ ] Prepare environment-specific tfvars file
- [ ] Initialize Terraform with correct backend config
- [ ] Phase 1: Apply without DNS
- [ ] Phase 2: Build and push image
- [ ] Phase 3: Recreate App Runner service (verify RUNNING)
- [ ] Phase 4: Apply DNS (two-stage)
- [ ] Phase 5: Populate secrets
- [ ] Phase 6: Verify deployment
- [ ] Configure Strava OAuth callback domain
- [ ] Test application functionality
- [ ] Set up monitoring/alerting
- [ ] Document environment-specific details

## Files to Update for New Environments

When creating a new environment, ensure these files exist:

```
infra/
├── environments/
│   └── {env}/
│       ├── vars.tfvars         # Environment-specific variables
│       └── backend.tfbackend   # Terraform backend config
└── dns.tf                      # Temporarily renamed during bootstrap

.keys/
└── kaizencoach-{env}-sa-key.json  # GCP service account key
```

## Next Steps After Bootstrap

1. Configure GitHub Actions for automated deployments
2. Set up CloudWatch alarms for App Runner metrics
3. Configure AWS Budgets for cost alerts
4. Enable AWS Cost Allocation tags in billing console
5. Document environment-specific endpoints and credentials
6. Add environment to monitoring dashboard

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