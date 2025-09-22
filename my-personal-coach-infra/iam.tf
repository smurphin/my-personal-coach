# iam.tf

# 1. Create the IAM Role that App Runner will use
resource "aws_iam_role" "apprunner_instance_role" {
  name = "my-personal-coach-apprunner-role"

  # This "Assume Role Policy" allows the App Runner service to use this role.
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [
      {
        Action    = "sts:AssumeRole",
        Effect    = "Allow",
        Principal = {
          Service = "tasks.apprunner.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Project   = "My Personal Coach"
    ManagedBy = "Terraform"
  }
}

# 2. Create the IAM Policy with specific permissions
resource "aws_iam_policy" "app_permissions_policy" {
  name        = "my-personal-coach-app-permissions"
  description = "Permissions for the My Personal Coach App Runner service"

  # This policy document grants read access to our secret and full access to our DynamoDB table.
  policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [
      {
        Action   = "secretsmanager:GetSecretValue",
        Effect   = "Allow",
        Resource = aws_secretsmanager_secret.app_secrets.arn
      },
      {
        Action   = "dynamodb:*",
        Effect   = "Allow",
        Resource = aws_dynamodb_table.users_table.arn
      }
    ]
  })
}

# 3. Attach the Policy to the Role
resource "aws_iam_role_policy_attachment" "app_permissions_attachment" {
  role       = aws_iam_role.apprunner_instance_role.name
  policy_arn = aws_iam_policy.app_permissions_policy.arn
}