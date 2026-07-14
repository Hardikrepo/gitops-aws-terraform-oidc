locals {
  name = "${var.project}-${var.environment}"
}

data "aws_caller_identity" "current" {}

# Stand-in workload: a versioned, encrypted S3 bucket plus an SSM
# parameter recording which commit last deployed it. Swap this module's
# contents for real infra once the pipeline itself is proven out - the
# naming convention (${project}-${environment}-*) is what the IAM role
# in modules/oidc-role scopes permissions to, so keep new resources
# under that prefix or widen the role's policy to match.

resource "aws_s3_bucket" "app" {
  bucket = "${local.name}-app-${data.aws_caller_identity.current.account_id}"

  tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_s3_bucket_versioning" "app" {
  bucket = aws_s3_bucket.app.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "app" {
  bucket = aws_s3_bucket.app.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "app" {
  bucket                  = aws_s3_bucket.app.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_ssm_parameter" "deployed_commit" {
  name  = "/${local.name}/deployed-commit"
  type  = "String"
  value = var.deployed_commit

  tags = {
    Project     = var.project
    Environment = var.environment
  }
}
