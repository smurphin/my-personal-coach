# dynamodb.tf

resource "aws_dynamodb_table" "users_table" {
  name         = var.env == "prod" ? "${var.name}-users" : "${var.env}-${var.name}-users"
  billing_mode = "PAY_PER_REQUEST" # This is cost-effective for sporadic traffic
  hash_key     = "athlete_id"

  attribute {
    name = "athlete_id"
    type = "S" # S for String, as the Strava ID will be the key
  }

  tags = local.common_tags
}