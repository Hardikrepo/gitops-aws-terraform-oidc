variable "project" {
  description = "Short project slug, used for resource naming."
  type        = string
}

variable "environment" {
  description = "Environment name (dev/staging/prod)."
  type        = string
}

variable "aws_region" {
  description = "AWS region for this environment's resources."
  type        = string
}

variable "deployed_commit" {
  description = "Git commit SHA being applied, recorded for traceability. Passed by the GitHub Actions workflow via -var."
  type        = string
  default     = "unset"
}
