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

  # This policy document grants read access to our secret, full access to our DynamoDB table,
  # and S3 access for data overflow storage.
  policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [
      {
        Sid      = "SecretsManagerAccess"
        Action   = "secretsmanager:GetSecretValue",
        Effect   = "Allow",
        Resource = aws_secretsmanager_secret.app_secrets.arn
      },
      {
        Sid      = "DynamoDBAccess"
        Action   = "dynamodb:*",
        Effect   = "Allow",
        Resource = aws_dynamodb_table.users_table.arn
      },
      {
        Sid      = "S3DataOverflowAccess"
        Action   = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ],
        Effect   = "Allow",
        Resource = [
          aws_s3_bucket.data_overflow.arn,
          "${aws_s3_bucket.data_overflow.arn}/*"
        ]
      }
    ]
  })
}

# 3. Attach the Policy to the Role
resource "aws_iam_role_policy_attachment" "app_permissions_attachment" {
  role       = aws_iam_role.apprunner_instance_role.name
  policy_arn = aws_iam_policy.app_permissions_policy.arn
}
