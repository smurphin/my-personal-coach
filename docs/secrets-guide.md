# Secrets Management Guide

This guide explains every secret used in kAIzen Coach, what it's for, where it comes from, and how to generate it.

<!-- toc -->

- [Overview](#overview)
- [Required Secrets](#required-secrets)
  * [1. STRAVA_CLIENT_ID](#1-strava_client_id)
  * [2. STRAVA_CLIENT_SECRET](#2-strava_client_secret)
  * [3. STRAVA_VERIFY_TOKEN](#3-strava_verify_token)
  * [4. FLASK_SECRET_KEY](#4-flask_secret_key)
  * [5. GOOGLE_APPLICATION_CREDENTIALS_JSON](#5-google_application_credentials_json)
  * [6. GARMIN_ENCRYPTION_KEY](#6-garmin_encryption_key)
  * [7. GCP_PROJECT_ID (Optional - Can be derived)](#7-gcp_project_id-optional---can-be-derived)
  * [8. GCP_LOCATION (Optional - Can be hardcoded)](#8-gcp_location-optional---can-be-hardcoded)
  * [9. Runtime Configuration (Optional - Per-Environment Experiments)](#9-runtime-configuration-optional---per-environment-experiments)
- [Complete Secrets Template](#complete-secrets-template)
- [Secrets Per Environment](#secrets-per-environment)
  * [Production](#production)
  * [Staging](#staging)
  * [Demo Instances](#demo-instances)
- [Populating Secrets in AWS](#populating-secrets-in-aws)
  * [Step 1: Create Secrets JSON File](#step-1-create-secrets-json-file)
  * [Step 2: Upload to Secrets Manager](#step-2-upload-to-secrets-manager)
  * [Step 3: Clean Up Local File](#step-3-clean-up-local-file)
  * [Step 4: Restart App Runner](#step-4-restart-app-runner)
- [Verifying Secrets Loaded](#verifying-secrets-loaded)
- [Rotating Secrets](#rotating-secrets)
  * [When to Rotate](#when-to-rotate)
  * [How to Rotate](#how-to-rotate)
- [Security Best Practices](#security-best-practices)
- [Troubleshooting](#troubleshooting)
  * [App Can't Load Secrets](#app-cant-load-secrets)
  * [Strava OAuth Fails](#strava-oauth-fails)
  * [Webhook Verification Fails](#webhook-verification-fails)
  * [Garmin Credentials Won't Decrypt](#garmin-credentials-wont-decrypt)
  * [GCP Service Account Errors](#gcp-service-account-errors)
- [Quick Reference Commands](#quick-reference-commands)
- [Related Documentation](#related-documentation)

<!-- tocstop -->

## Overview

All application secrets are stored in **AWS Secrets Manager** and loaded at runtime by the Flask application. Terraform creates the secret placeholder, but you must manually populate it.

**Secret Name Pattern:**
- Prod: `my-personal-coach-app-secrets` (legacy)
- Other environments: `{environment}-kaizencoach-app-secrets`

## Required Secrets

### 1. STRAVA_CLIENT_ID

**What it is:** Your Strava API application's client ID (public identifier)

**Where it comes from:** Strava API Settings page

**How to get it:**
1. Go to https://www.strava.com/settings/api
2. Create a new API application (or use existing)
3. The "Client ID" is shown on the page (numeric value)

**Format:** Numeric string
```json
"STRAVA_CLIENT_ID": "176694"
```

**Used for:**
- OAuth authentication flow
- Strava API requests
- Webhook subscription management

---

### 2. STRAVA_CLIENT_SECRET

**What it is:** Your Strava API application's client secret (private key)

**Where it comes from:** Strava API Settings page

**How to get it:**
1. Same page as Client ID: https://www.strava.com/settings/api
2. The "Client Secret" is shown (hexadecimal string)
3. **Keep this secret!** Don't commit to git or share publicly

**Format:** Hexadecimal string
```json
"STRAVA_CLIENT_SECRET": "a1b2c3d4e5f6789012345678901234567890abcd"
```

**Used for:**
- OAuth token exchange
- Refreshing access tokens
- Webhook subscription management

---

### 3. STRAVA_VERIFY_TOKEN

**What it is:** A secret token YOU CREATE to verify webhook requests are from Strava

**Where it comes from:** **YOU GENERATE THIS YOURSELF**

**How to generate it:**
```bash
# Generate a random 40-character hex string
openssl rand -hex 20

# Example output:
# cb4fda9a37786db2cbfc7905e5458fe75874ed5a
```

**Format:** Any random string (recommend 40+ characters)
```json
"STRAVA_VERIFY_TOKEN": "cb4fda9a37786db2cbfc7905e5458fe75874ed5a"
```

**Used for:**
- Verifying Strava webhook subscription requests
- Preventing unauthorized webhook attempts
- Each environment can use the same or different tokens

**Important:** 
- You provide this token when creating the webhook subscription
- Strava sends it back with webhook verification requests
- Your app checks it matches before responding

---

### 4. FLASK_SECRET_KEY

**What it is:** Flask's session encryption key

**Where it comes from:** **YOU GENERATE THIS YOURSELF**

**How to generate it:**
```bash
# Generate a random 64-character hex string
openssl rand -hex 32

# Or use Python:
python3 -c "import secrets; print(secrets.token_hex(32))"

# Example output:
# 8f7d6e5c4b3a2918e7f6d5c4b3a29180f7e6d5c4b3a29180e7f6d5c4b3a2918
```

**Format:** Long random string (recommend 64+ characters)
```json
"FLASK_SECRET_KEY": "8f7d6e5c4b3a2918e7f6d5c4b3a29180f7e6d5c4b3a29180e7f6d5c4b3a2918"
```

**Used for:**
- Encrypting Flask session cookies
- Signing session data
- Preventing session tampering
- Flash messages and CSRF protection

**Security:**
- **CRITICAL:** Never share or commit this to git
- Use different keys for prod vs staging/dev
- If compromised, regenerate and restart app

---

### 5. GOOGLE_APPLICATION_CREDENTIALS_JSON

**What it is:** GCP service account credentials (entire JSON key file)

**Where it comes from:** Google Cloud Console

**How to get it:**
1. Go to GCP Console → IAM & Admin → Service Accounts
2. Find your service account (e.g., `vertex-ai-sa@kaizencoach-staging.iam.gserviceaccount.com`)
3. Click → Keys → Add Key → Create New Key → JSON
4. Download the JSON file

**How to format for Secrets Manager:**
```bash
# Convert to single-line JSON (no newlines)
cat .keys/kaizencoach-staging-sa-key.json | jq -c '.'

# This produces a compact single-line JSON string
```

**Format:** Complete JSON key file as a single-line string
```json
"GOOGLE_APPLICATION_CREDENTIALS_JSON": "{\"type\":\"service_account\",\"project_id\":\"kaizencoach-staging\",\"private_key_id\":\"abc123...\",\"private_key\":\"-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n\",\"client_email\":\"vertex-ai-sa@kaizencoach-staging.iam.gserviceaccount.com\",\"client_id\":\"123456789\",\"auth_uri\":\"https://accounts.google.com/o/oauth2/auth\",\"token_uri\":\"https://oauth2.googleapis.com/token\",\"auth_provider_x509_cert_url\":\"https://www.googleapis.com/oauth2/v1/certs\",\"client_x509_cert_url\":\"https://www.googleapis.com/robot/v1/metadata/x509/vertex-ai-sa%40kaizencoach-staging.iam.gserviceaccount.com\"}"
```

**Used for:**
- Authenticating to Google Vertex AI
- Making AI generation requests
- Required for all Gemini API calls

**Critical Notes:**
- Must be single-line JSON (no newlines except within private key)
- The private key inside contains `\n` - these must be preserved
- Each environment needs its own service account and key

---

### 6. GARMIN_ENCRYPTION_KEY

**What it is:** Symmetric encryption key for storing Garmin credentials

**Where it comes from:** **YOU GENERATE THIS YOURSELF**

**How to generate it:**
```bash
# Generate a 32-byte (256-bit) key, base64 encoded
openssl rand -base64 32

# Or use Python:
python3 -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"

# Example output:
# XyZ9AbC8dEf7GhI6JkL5MnO4PqR3StU2VwX1YzA0BcD=
```

**Format:** Base64-encoded 32-byte key
```json
"GARMIN_ENCRYPTION_KEY": "XyZ9AbC8dEf7GhI6JkL5MnO4PqR3StU2VwX1YzA0BcD="
```

**Used for:**
- Encrypting Garmin email/password before storing in DynamoDB
- Decrypting credentials when fetching Garmin data
- AES-256 encryption with Fernet

**Security:**
- **CRITICAL:** Never share or commit this
- If compromised, all stored Garmin credentials are exposed
- Use different keys for prod vs staging
- Store Garmin passwords already encrypted in DynamoDB

---

### 7. GCP_PROJECT_ID (Optional - Can be derived)

**What it is:** The GCP project ID for this environment

**Where it comes from:** Set when creating GCP project

**Format:** String
```json
"GCP_PROJECT_ID": "kaizencoach-staging"
```

**Used for:**
- Vertex AI initialization
- Can be parsed from GOOGLE_APPLICATION_CREDENTIALS_JSON

**Note:** This is technically optional as `config.py` can determine it from the environment variable or parse it from the service account JSON.

---

### 8. GCP_LOCATION (Optional - Can be hardcoded)

**What it is:** GCP region for Vertex AI

**Format:** String
```json
"GCP_LOCATION": "europe-west1"
```

**Note:** This is typically hardcoded in config.py and doesn't need to be in secrets.

---

### 9. Runtime Configuration (Optional - Per-Environment Experiments)

These allow you to tweak behaviour per environment without a code deploy. Useful for testing different AI models in staging while prod stays stable.

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `AI_MODEL` | string | `gemini-2.5-flash` | Gemini model name (e.g. `gemini-3-flash-preview` for experiments) |
| `AI_TEMPERATURE` | float | — | 0–2, controls creativity; lower = more deterministic |
| `AI_MAX_OUTPUT_TOKENS` | int | — | Max tokens per response |
| `WEBHOOK_DELAY_SECONDS` | int | `10` | Delay before processing Strava webhooks; set to 300 in prod secret for batching if needed |
| `AI_THINKING_LEVEL` | string | — | **Gemini 3 only.** MINIMAL, LOW, MEDIUM, HIGH. Ignored for 2.5 (API errors). When not set on 3, model default (HIGH) |

**Example (staging with Gemini v3 + faster responses):**
```json
"AI_MODEL": "gemini-3-flash-preview",
"AI_THINKING_LEVEL": "LOW"
```
*Note: AI_THINKING_LEVEL is ignored if AI_MODEL is Gemini 2.5—only set both when using Gemini 3.*

**Example (prod with longer webhook batching):**
```json
"WEBHOOK_DELAY_SECONDS": "300"
```

**To change:** Update the secret in AWS Console → Secrets Manager, then restart App Runner. No code deploy or Terraform change.

---

## Complete Secrets Template

```json
{
  "STRAVA_CLIENT_ID": "your_strava_app_client_id",
  "STRAVA_CLIENT_SECRET": "your_strava_app_client_secret",
  "STRAVA_VERIFY_TOKEN": "random_40_char_token_you_generated",
  "FLASK_SECRET_KEY": "random_64_char_key_you_generated",
  "GOOGLE_APPLICATION_CREDENTIALS_JSON": "{\"type\":\"service_account\",\"project_id\":\"kaizencoach-staging\",...}",
  "GARMIN_ENCRYPTION_KEY": "base64_encoded_32_byte_key"
}
```

## Secrets Per Environment

### Production
- **Strava App:** Production Strava app with callback `www.kaizencoach.training`
- **GCP Project:** `kaizencoach-prod`
- **Unique Keys:** Generate NEW Flask secret, Garmin key, and Strava verify token

### Staging
- **Strava App:** Separate Strava app with callback `staging.kaizencoach.training`
- **GCP Project:** `kaizencoach-staging`
- **Unique Keys:** Generate NEW Flask secret, Garmin key, and Strava verify token

### Demo Instances
- **Strava App:** Each demo needs separate Strava app (e.g., `demo-alice.kaizencoach.training`)
- **GCP Project:** `kaizencoach-demo` (can be shared or separate)
- **Unique Keys:** Can reuse staging keys OR generate new ones for isolation

## Populating Secrets in AWS

### Step 1: Create Secrets JSON File

```bash
# DON'T commit this file!
cat > /tmp/staging-secrets.json << 'EOF'
{
  "STRAVA_CLIENT_ID": "188207",
  "STRAVA_CLIENT_SECRET": "abc123def456...",
  "STRAVA_VERIFY_TOKEN": "cb4fda9a37786db2cbfc7905e5458fe75874ed5a",
  "FLASK_SECRET_KEY": "8f7d6e5c4b3a2918e7f6d5c4b3a29180...",
  "GOOGLE_APPLICATION_CREDENTIALS_JSON": "{\"type\":\"service_account\"...}",
  "GARMIN_ENCRYPTION_KEY": "XyZ9AbC8dEf7GhI6JkL5MnO4PqR3StU2VwX1YzA0BcD="
}
EOF
```

### Step 2: Upload to Secrets Manager

```bash
# For staging
aws secretsmanager put-secret-value \
  --secret-id staging-kaizencoach-app-secrets \
  --secret-string file:///tmp/staging-secrets.json \
  --region eu-west-1

# For prod
aws secretsmanager put-secret-value \
  --secret-id my-personal-coach-app-secrets \
  --secret-string file:///tmp/staging-secrets.json \
  --region eu-west-1
```

### Step 3: Clean Up Local File

```bash
rm /tmp/staging-secrets.json
```

### Step 4: Restart App Runner

```bash
# Force App Runner to reload secrets
aws apprunner start-deployment \
  --service-arn <your-service-arn> \
  --region eu-west-1
```

## Verifying Secrets Loaded

Check application logs after restart:

```bash
aws logs tail /aws/apprunner/staging-kaizencoach-service/service --since 5m --region eu-west-1 | grep "Secrets loaded"
```

Should show:
```
✅ Secrets loaded - STRAVA_CLIENT_ID: 188207...
```

## Rotating Secrets

### When to Rotate
- Flask secret key compromised
- Garmin encryption key compromised
- Strava credentials compromised
- Regular security practice (annually)

### How to Rotate

```bash
# 1. Generate new secrets (don't overwrite existing yet!)
openssl rand -hex 32  # New Flask key
openssl rand -base64 32  # New Garmin key

# 2. Update secrets in Secrets Manager
# (follow same steps as populating)

# 3. For Garmin key rotation:
# WARNING: Rotating Garmin key will invalidate all stored passwords!
# Users will need to re-authenticate with Garmin

# 4. Restart App Runner
aws apprunner start-deployment --service-arn <arn> --region eu-west-1

# 5. Verify new secrets loaded in logs
```

## Security Best Practices

1. **Never commit secrets to git**
   - Add to `.gitignore`: `*secrets*.json`, `*.key`, `.keys/`

2. **Use different secrets per environment**
   - Prod and staging should never share Flask keys or Garmin keys
   - Can reuse Strava verify tokens if desired (but not required)

3. **Restrict AWS Secrets Manager access**
   - Only App Runner IAM role should read secrets
   - Use principle of least privilege

4. **Audit secret access**
   - Enable CloudTrail logging for Secrets Manager
   - Monitor for unusual access patterns

5. **Backup secrets securely**
   - Store in password manager (1Password, LastPass, etc.)
   - Don't rely solely on AWS Secrets Manager

6. **Document secret generation**
   - Keep record of when secrets were created
   - Note which environments use which Strava apps

## Troubleshooting

### App Can't Load Secrets
**Symptom:** App crashes on startup with "SECRET_KEY not found"
**Fix:** Verify secrets exist in Secrets Manager and App Runner IAM role has `secretsmanager:GetSecretValue` permission

### Strava OAuth Fails
**Symptom:** "Invalid client_id" error
**Fix:** Check STRAVA_CLIENT_ID matches the Strava app for this environment's domain

### Webhook Verification Fails
**Symptom:** Webhook subscription fails with 403
**Fix:** Ensure STRAVA_VERIFY_TOKEN in secrets matches the token you're using in the API call

### Garmin Credentials Won't Decrypt
**Symptom:** Error decrypting Garmin password
**Fix:** GARMIN_ENCRYPTION_KEY may have changed - users need to re-authenticate

### GCP Service Account Errors
**Symptom:** "Could not load default credentials" or 403 from Vertex AI
**Fix:** 
- Check GOOGLE_APPLICATION_CREDENTIALS_JSON is valid JSON
- Verify service account has `roles/aiplatform.user` in GCP
- Ensure billing is enabled on the GCP project

## Quick Reference Commands

```bash
# Generate Flask secret
openssl rand -hex 32

# Generate Garmin encryption key
openssl rand -base64 32

# Generate Strava verify token
openssl rand -hex 20

# Format GCP service account JSON
cat key.json | jq -c '.'

# View secrets (sanitized)
aws secretsmanager get-secret-value \
  --secret-id staging-kaizencoach-app-secrets \
  --region eu-west-1 \
  --query SecretString --output text | jq .

# Update secrets
aws secretsmanager put-secret-value \
  --secret-id staging-kaizencoach-app-secrets \
  --secret-string file:///tmp/secrets.json \
  --region eu-west-1

# Force app to reload secrets
aws apprunner start-deployment --service-arn <arn> --region eu-west-1
```

## Related Documentation

- [BOOTSTRAP.md](BOOTSTRAP.md) - Full environment setup including secrets
- [MULTI_ENVIRONMENT_SETUP.md](MULTI_ENVIRONMENT_SETUP.md) - Environment architecture
- [AWS Secrets Manager](https://docs.aws.amazon.com/secretsmanager/)
- [Strava API Documentation](https://developers.strava.com/)