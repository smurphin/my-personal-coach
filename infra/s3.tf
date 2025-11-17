# s3.tf

resource "aws_s3_bucket" "data_overflow" {
  bucket = "kaizencoach-data"

  tags = {
    Project   = "My Personal Coach"
    ManagedBy = "Terraform"
    Purpose   = "DynamoDB Overflow Storage"
  }
}

# Enable versioning for data protection
resource "aws_s3_bucket_versioning" "data_overflow_versioning" {
  bucket = aws_s3_bucket.data_overflow.id
  
  versioning_configuration {
    status = "Enabled"
  }
}

# Server-side encryption
resource "aws_s3_bucket_server_side_encryption_configuration" "data_overflow_encryption" {
  bucket = aws_s3_bucket.data_overflow.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Lifecycle policy to reduce storage costs
# Current versions: kept indefinitely with automatic archival to cheaper storage
# Non-current versions: deleted after 7 days (short-term backup only)
resource "aws_s3_bucket_lifecycle_configuration" "data_overflow_lifecycle" {
  bucket = aws_s3_bucket.data_overflow.id

  rule {
    id     = "archive-current-data"
    status = "Enabled"

    # Archive current versions to cheaper storage over time
    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 180
      storage_class = "GLACIER"
    }

    # No expiration - keep current versions indefinitely
  }

  rule {
    id     = "cleanup-old-versions"
    status = "Enabled"

    # Delete old versions after 7 days
    # These are just replaced versions - we don't need long-term history of every update
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
  }
}

# Block public access (security best practice)
resource "aws_s3_bucket_public_access_block" "data_overflow_public_access_block" {
  bucket = aws_s3_bucket.data_overflow.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Output the bucket name for reference
output "s3_data_bucket_name" {
  description = "Name of the S3 bucket for data overflow"
  value       = aws_s3_bucket.data_overflow.id
}

output "s3_data_bucket_arn" {
  description = "ARN of the S3 bucket for data overflow"
  value       = aws_s3_bucket.data_overflow.arn
}
