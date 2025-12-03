locals {
  common_tags = {
    Application = "kaizencoach"
    Project   = var.env == "prod" ? "My Personal Coach" : "${var.env}-${var.name}"
    ManagedBy = "Terraform"
    Environment = var.env
    ManagedBy   = "Terraform"
    CostCenter  = "kaizencoach-${var.env}"
  }
}