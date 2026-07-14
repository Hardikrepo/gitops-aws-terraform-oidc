variable "project" {
  description = "Short project slug, used for resource naming and the least-privilege ARN prefix."
  type        = string
}

variable "environment" {
  description = "Environment name (dev/staging/prod). Must match a GitHub Environment of the same name."
  type        = string
}

variable "oidc_provider" {
  description = "The aws_iam_openid_connect_provider resource for token.actions.githubusercontent.com."
  type = object({
    arn = string
  })
}

variable "github_org" {
  description = "GitHub organization (or user) that owns the repo."
  type        = string
}

variable "github_repo" {
  description = "GitHub repository name (without org)."
  type        = string
}

variable "state_bucket_arn" {
  description = "ARN of the shared Terraform state S3 bucket, so the role can read/write its env's state."
  type        = string
}

variable "lock_table_arn" {
  description = "ARN of the shared DynamoDB lock table."
  type        = string
}
