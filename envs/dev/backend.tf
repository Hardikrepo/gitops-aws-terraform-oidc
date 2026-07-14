terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Bucket/table/region are intentionally omitted here (partial config).
  # Local dev: pass them via -backend-config flags, or a backend.hcl
  # you keep untracked. CI: the GitHub Actions workflow supplies them
  # from repo variables set after `bootstrap` has been applied once.
  #
  #   terraform init \
  #     -backend-config="bucket=<bootstrap output: state_bucket>" \
  #     -backend-config="dynamodb_table=<bootstrap output: lock_table>" \
  #     -backend-config="region=us-east-1"
  backend "s3" {
    key = "envs/dev/terraform.tfstate"
  }
}
