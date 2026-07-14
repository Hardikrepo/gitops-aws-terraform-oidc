output "bucket_name" {
  value = aws_s3_bucket.app.bucket
}

output "deployed_commit_parameter" {
  value = aws_ssm_parameter.deployed_commit.name
}
