variable "project" {
  type    = string
  default = "gitops-aws-oidc"
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "deployed_commit" {
  description = "Git commit SHA being applied. Passed by CI via -var; defaults to 'local' for manual runs."
  type        = string
  default     = "local"
}
