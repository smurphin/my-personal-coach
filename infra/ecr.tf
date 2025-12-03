# ecr.tf

resource "aws_ecr_repository" "app_repository" {
  name                 = var.env == "prod" ? "${var.name}-app" : "${var.env}-${var.name}-app"
  image_tag_mutability = "MUTABLE" # Allows you to overwrite tags like 'latest'

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.common_tags
}