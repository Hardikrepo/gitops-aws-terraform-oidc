variable "aws_region" {
  description = "AWS region for the bootstrap resources (state backend, IAM roles)."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Short project slug used to name/prefix resources."
  type        = string
  default     = "gitops-aws-oidc"
}

variable "github_org" {
  description = "GitHub organization (or user) that owns the repo allowed to assume roles."
  type        = string
  default     = "Hardikrepo"
}

variable "github_repo" {
  description = "GitHub repository name (without org) allowed to assume roles."
  type        = string
  default     = "gitops-aws-terraform-oidc"
}

variable "environments" {
  description = "Environment names to create a scoped IAM role for. Each must match a GitHub Environment of the same name."
  type        = list(string)
  default     = ["dev", "staging", "prod"]
}

variable "bedrock_model_id" {
  description = "Bedrock foundation model ID the agents (lock-doctor, plan-reviewer) are allowed to invoke."
  type        = string
  default     = "anthropic.claude-sonnet-5"
}
