output "state_bucket" {
  description = "S3 bucket holding Terraform remote state. Reference this in envs/*/backend.tf."
  value       = aws_s3_bucket.tf_state.bucket
}

output "lock_table" {
  description = "DynamoDB table used for state locking. Reference this in envs/*/backend.tf."
  value       = aws_dynamodb_table.tf_lock.name
}

output "role_arns" {
  description = "Map of environment name -> IAM role ARN for GitHub Actions to assume via OIDC."
  value       = { for env, mod in module.env_roles : env => mod.role_arn }
}
