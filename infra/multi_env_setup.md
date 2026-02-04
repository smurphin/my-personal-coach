# Multi-Environment Setup (Infra Summary)

This file is a **short infra-local summary**. The **canonical documentation** for multi-environment setup lives in the main docs:

- `docs/multi_env_setup.md` – overall multi-environment architecture and configuration
- `docs/new_env_deployment.md` – full environment bootstrap guide (staging/prod/demo), including DNS and Strava webhooks
- `docs/secrets-guide.md` – complete secrets management and per-environment guidance

Always start from those docs. This file is only a quick reminder while working in `infra/`.

## High-Level Checklist (see `docs/` for details)

- **Config & code**
  - Ensure `config.py` has your environment name in `REDIRECT_URIS` and `GCP_PROJECTS` before building images.
  - Confirm `ENVIRONMENT` and `FLASK_ENV` are set correctly for each App Runner service.

- **Terraform**
  - Use an `environments/{env}/vars.tfvars` file with:
    - `environment` (e.g. `staging`, `prod`, `demo-foo`)
    - `domain_name` for that environment
    - `r53_zone_id` for the existing `kaizencoach.training` hosted zone (look this up in Route 53, do **not** copy example IDs)
  - Apply in two stages for new environments: first without DNS, then with DNS (as described in `docs/new_env_deployment.md`).

- **AWS resources**
  - One DynamoDB table and S3 bucket per environment (prod keeps legacy names to avoid migration, as documented in `docs/multi_env_setup.md`).
  - One Secrets Manager secret per environment using the naming pattern defined in Terraform (`infra/secrets.tf`).

- **ECR & App Runner**
  - Build and push images to your own ECR registry using your **actual** AWS account ID and region:

```bash
aws ecr get-login-password --region <AWS_REGION> \
  | sudo docker login --username AWS --password-stdin <AWS_ACCOUNT_ID>.dkr.ecr.<AWS_REGION>.amazonaws.com

sudo docker build -t <AWS_ACCOUNT_ID>.dkr.ecr.<AWS_REGION>.amazonaws.com/<ENVIRONMENT>-kaizencoach-app:latest .
sudo docker push <AWS_ACCOUNT_ID>.dkr.ecr.<AWS_REGION>.amazonaws.com/<ENVIRONMENT>-kaizencoach-app:latest
```

- **Secrets**
  - Populate each environment’s Secrets Manager entry with environment-specific values (never reuse prod secrets).
  - Follow generation/rotation guidance in `docs/secrets-guide.md`.

> **Security note:** Any concrete IDs, ARNs, or values shown in `docs/` are illustrative only and should be treated as placeholders. Always use your own account IDs, zone IDs, and credentials, and never commit real secrets or private identifiers to this repository.

