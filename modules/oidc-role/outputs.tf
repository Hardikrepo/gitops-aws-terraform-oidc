output "role_arn" {
  description = "ARN of the environment-scoped IAM role GitHub Actions assumes via OIDC."
  value       = aws_iam_role.this.arn
}

output "role_name" {
  value = aws_iam_role.this.name
}
