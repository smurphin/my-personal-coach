# dns.tf

locals {
  zone_id = var.env == "prod" ? aws_route53_zone.primary[0].zone_id : data.aws_route53_zone.primary[0].zone_id
  zone_name = var.env == "prod" ? aws_route53_zone.primary[0].name : data.aws_route53_zone.primary[0].name
}

# 1. Creates a hosted zone in AWS Route 53 for your domain
# Only prod creates/manages the hosted zone
resource "aws_route53_zone" "primary" {
  count = var.env == "prod" ? 1 : 0
  name = var.primary_domain_name
}

# Non-prod environments look up the existing zone
data "aws_route53_zone" "primary" {
  count = var.env != "prod" ? 1 : 0
  name  = var.primary_domain_name  # kaizencoach.training
}

# 2. Associates your custom domain with the App Runner service
resource "aws_apprunner_custom_domain_association" "main" {
  service_arn = aws_apprunner_service.main_app_service.arn
  domain_name = var.domain_name

  depends_on = [aws_apprunner_service.main_app_service]
}

# 3. Automatically creates the CNAME records needed to validate your SSL certificate
resource "aws_route53_record" "cert_validation" {
  for_each = {
    for record in aws_apprunner_custom_domain_association.main.certificate_validation_records : record.name => record
  }

  zone_id = local.zone_id
  name    = each.value.name
  type    = each.value.type
  records = [each.value.value]
  ttl     = 60 # A short TTL for validation records is fine


  depends_on = [aws_apprunner_custom_domain_association.main]

}

# 4. Creates the final A record to point www.kaizencoach.training to your app
resource "aws_route53_record" "www_subdomain" {
  zone_id = local.zone_id
  name    = "www.${var.domain_name}"
  type    = "A"

  alias {
    name                   = aws_apprunner_custom_domain_association.main.dns_target
    zone_id                = var.r53_zone_id #static AWS apprunner Zone ID in eu-west-1
    evaluate_target_health = true
  }
}

# 5. Creates the root domain A record to point kaizencoach.training to your app
resource "aws_route53_record" "subdomain" {
  zone_id = local.zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = aws_apprunner_custom_domain_association.main.dns_target
    zone_id                = var.r53_zone_id #static AWS apprunner Zone ID in eu-west-1
    evaluate_target_health = true
  }
}
