# Admin Deployment Guide (Summary)

For full deployment and versioning details, use:

- `docs/versioning.md` – end-to-end versioning and deployment flow
- `deploy.txt` – canonical deployment commands
- `scripts/deploy.sh` – scripted, version-aware deployments

## Quick manual ECR build & push (rarely needed)

Only use this when you intentionally want to bypass `scripts/deploy.sh` (e.g., emergency debug image). Replace all placeholders with your own values:

```bash
cd ~/git/my-personal-coach

AWS_ACCOUNT_ID=<AWS_ACCOUNT_ID>
AWS_REGION=eu-west-1
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# 1. Authenticate with ECR
aws ecr get-login-password --region "$AWS_REGION" \
  | sudo docker login --username AWS --password-stdin "$ECR_REGISTRY"

# 2. Build the Docker image
sudo docker build -t "${ECR_REGISTRY}/my-personal-coach-app:latest" .

# 3. Push to ECR
sudo docker push "${ECR_REGISTRY}/my-personal-coach-app:latest"

# 4. Trigger App Runner deployment from the AWS Console or via:
# aws apprunner start-deployment --service-arn <APP_RUNNER_SERVICE_ARN> --region "$AWS_REGION"
```