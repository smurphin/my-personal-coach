# Environment Bootstrap Guide (Infra Pointer)

This file is a **pointer for infra engineers**. The complete, canonical environment bootstrap guide lives in:

- `docs/new_env_deployment.md`

That document covers:

- Prerequisites (AWS CLI, Terraform backend, Docker, GCP project + Vertex AI)
- Two-phase Terraform applies (without DNS, then with DNS)
- ECR image build/push
- Secrets population in AWS Secrets Manager
- App Runner domain + DNS setup
- Strava webhook subscription flow

## When you are in `infra/`

Use this quick checklist, but always refer back to `docs/new_env_deployment.md` for the exact commands and details:

1. **Terraform (phase 1 – no DNS)**
   - Temporarily disable DNS resources if bootstrapping a brand-new environment.
   - Run `terraform apply -var-file=environments/<env>/vars.tfvars`.

2. **Build & push image**
   - Log in to ECR and push an image tagged for the target environment using your own AWS account ID:

```bash
aws ecr get-login-password --region <AWS_REGION> \
  | sudo docker login --username AWS --password-stdin <AWS_ACCOUNT_ID>.dkr.ecr.<AWS_REGION>.amazonaws.com

sudo docker build -t <AWS_ACCOUNT_ID>.dkr.ecr.<AWS_REGION>.amazonaws.com/<ENVIRONMENT>-kaizencoach-app:latest .
sudo docker push <AWS_ACCOUNT_ID>.dkr.ecr.<AWS_REGION>.amazonaws.com/<ENVIRONMENT>-kaizencoach-app:latest
```

3. **Terraform (phase 2 – App Runner + DNS)**
   - Re-apply Terraform to replace the failed App Runner service with a running one.
   - Re-enable DNS resources and follow the two-stage apply pattern for custom domains and Route 53 records.

4. **Secrets & webhooks**
   - Populate the per-environment Secrets Manager secret as described in `docs/secrets-guide.md`.
   - Set up Strava webhooks for the environment following the steps in `docs/new_env_deployment.md`.

> **Security note:** The examples above use generic placeholders like `<AWS_ACCOUNT_ID>`, `<AWS_REGION>`, and `<ENVIRONMENT>`. Never commit real account IDs, zone IDs, secrets, or Strava credentials into this repository; keep all real values in Terraform variables, AWS Secrets Manager, or your local environment.*** End Patch```} ***!
