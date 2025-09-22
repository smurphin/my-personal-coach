# dns.tf

# 1. Creates a hosted zone in AWS Route 53 for your domain
resource "aws_route53_zone" "primary" {
  name = var.domain_name
}

# 2. Associates your custom domain with the App Runner service
resource "aws_apprunner_custom_domain_association" "main" {
  service_arn = aws_apprunner_service.main_app_service.arn
  domain_name = aws_route53_zone.primary.name

  depends_on = [aws_apprunner_service.main_app_service]
}

# 3. Automatically creates the CNAME records needed to validate your SSL certificate
resource "aws_route53_record" "cert_validation" {
  for_each = {
    for record in aws_apprunner_custom_domain_association.main.certificate_validation_records : record.name => record
  }

  zone_id = aws_route53_zone.primary.zone_id
  name    = each.value.name
  type    = each.value.type
  records = [each.value.value]
  ttl     = 60 # A short TTL for validation records is fine


  depends_on = [aws_apprunner_custom_domain_association.main]

}

# 4. Creates the final A record to point www.kaizencoach.training to your app
resource "aws_route53_record" "www" {
  zone_id = aws_route53_zone.primary.zone_id
  name    = "www.${aws_route53_zone.primary.name}"
  type    = "A"

  alias {
    name                   = aws_apprunner_custom_domain_association.main.dns_target
    zone_id                = "Z087551914Z2PCAU0QHMW" # Static zone ID for App Runner in eu-west-1
    evaluate_target_health = true
  }
}

# 5. Creates the root domain A record to point kaizencoach.training to your app
resource "aws_route53_record" "root" {
  zone_id = aws_route53_zone.primary.zone_id
  name    = aws_route53_zone.primary.name
  type    = "A"

  alias {
    name                   = aws_apprunner_custom_domain_association.main.dns_target
    zone_id                = "Z087551914Z2PCAU0QHMW" # Static zone ID for App Runner in eu-west-1
    evaluate_target_health = true
  }
}
