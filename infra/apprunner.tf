# apprunner.tf

resource "aws_apprunner_service" "main_app_service" {
  service_name = "my-personal-coach-service"

  source_configuration {
    image_repository {
      image_identifier      = "${aws_ecr_repository.app_repository.repository_url}:latest"
      image_repository_type = "ECR"
      image_configuration {
        port = "8080" # The port exposed in your Dockerfile
        runtime_environment_variables = {
          FLASK_ENV      = "production"
          APP_DEBUG_MODE = "False" # Set to "True" to enable, "False" to disable
        }
      }
    }
    authentication_configuration {
      # App Runner needs an access role to be able to pull images from ECR.
      access_role_arn = aws_iam_role.apprunner_ecr_access_role.arn
    }
  }

  instance_configuration {
    cpu    = "1024" # 1 vCPU
    memory = "2048" # 2 GB
    instance_role_arn = aws_iam_role.apprunner_instance_role.arn
  }

  network_configuration {
    egress_configuration {
      egress_type = "DEFAULT"
    }
  }

  tags = {
    Project   = "My Personal Coach"
    ManagedBy = "Terraform"
  }
}

# --- ECR Access Role for App Runner ---
# This is a separate role specifically for allowing App Runner to access ECR.
resource "aws_iam_role" "apprunner_ecr_access_role" {
  name = "my-personal-coach-apprunner-ecr-access"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{
      Action    = "sts:AssumeRole",
      Effect    = "Allow",
      Principal = {
        Service = "build.apprunner.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_policy" "apprunner_ecr_policy" {
  name   = "my-personal-coach-apprunner-ecr-policy"
  policy = file("${path.module}/policies/apprunner-ecr-policy.json")
}

resource "aws_iam_role_policy_attachment" "apprunner_ecr_attachment" {
  role       = aws_iam_role.apprunner_ecr_access_role.name
  policy_arn = aws_iam_policy.apprunner_ecr_policy.arn
}

# --- Outputs ---
output "apprunner_service_url" {
  description = "The public URL of the App Runner service"
  value       = aws_apprunner_service.main_app_service.service_url
}