# ecr.tf

resource "aws_ecr_repository" "app_repository" {
  name                 = "my-personal-coach-app"
  image_tag_mutability = "MUTABLE" # Allows you to overwrite tags like 'latest'

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Project   = "My Personal Coach"
    ManagedBy = "Terraform"
  }
}