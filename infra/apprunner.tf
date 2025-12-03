# apprunner.tf

resource "aws_apprunner_service" "main_app_service" {
  service_name = var.name == "prod" ? "${var.name}-service" : "${var.env}-${var.name}-service"

  source_configuration {
    image_repository {
      image_identifier      = "${aws_ecr_repository.app_repository.repository_url}:latest"
      image_repository_type = "ECR"
      image_configuration {
        port = var.app_port # The port exposed in your Dockerfile
        runtime_environment_variables = {
          FLASK_ENV      = var.flask_env
          ENVIRONMENT    = var.env
          APP_DEBUG_MODE = var.app_debug_mode # Set to "True" to enable, "False" to disable
        }
      }
    }
    authentication_configuration {
      # App Runner needs an access role to be able to pull images from ECR.
      access_role_arn = aws_iam_role.apprunner_ecr_access_role.arn
    }
  }

  instance_configuration {
    cpu    = var.cpu
    memory = var.memory
    instance_role_arn = aws_iam_role.apprunner_instance_role.arn
  }

  network_configuration {
    egress_configuration {
      egress_type = "DEFAULT"
    }
  }

  tags = local.common_tags
}

# --- ECR Access Role for App Runner ---
# This is a separate role specifically for allowing App Runner to access ECR.
resource "aws_iam_role" "apprunner_ecr_access_role" {
  name = var.env == "prod" ? "${var.name}-apprunner-ecr-access" : "${var.env}-${var.name}-apprunner-ecr-access"
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
  name = var.env == "prod" ? "${var.name}-apprunner-ecr-policy" : "${var.env}-${var.name}-apprunner-ecr-policy"
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