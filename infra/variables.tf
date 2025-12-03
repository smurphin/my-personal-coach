variable "primary_domain_name" {
  description = "The root domain for all instances"
  type = string
  default = "kaizencoach.training"
}

variable "domain_name" {
  description = "The custom domain name for the application."
  type        = string
}

variable "env" {
  description = "The environment for the deployment (e.g., dev, staging, prod)."
  type        = string
}

variable "name" {
  description = "The name prefix for resources."
  type        = string
  default    = "kaizencoach"
}

variable "region" {
  description = "The AWS region to deploy resources in."
  type        = string
  default     = "eu-west-1"
}

variable "app_port" {
  description = "The port on which the application listens."
  type        = string
  default     = "8080"
}

variable "flask_env" {
  description = "The Flask environment setting (e.g., development, production)."
  type        = string
}

variable "app_debug_mode" {
  description = "Enable or disable debug mode for the application."
  type        = string
  default     = "False"
}

variable "cpu" {
  description = "The CPU configuration for the App Runner instance."
  type        = string
  default     = "1024" # 1 vCPU
}

variable "memory" {
  description = "The memory configuration for the App Runner instance."
  type        = string
  default     = "2048" # 2 GB
}

variable "r53_zone_id" {
  description = "The Route 53 Hosted Zone ID for the domain."
  type        = string
  default = "Z087551914Z2PCAU0QHMW" # Static zone ID for App Runner in eu-west-1"
}
