# This resource creates the "container" for our secrets in AWS Secrets Manager.
# The actual secret values will be added manually in the AWS Console.
resource "aws_secretsmanager_secret" "app_secrets" {
  name        = var.env == "prod" ? "${var.name}-app-secrets" : "${var.env}-${var.name}-app-secrets"
  description = "Secrets for the My Personal Coach application"

  tags = local.common_tags
}