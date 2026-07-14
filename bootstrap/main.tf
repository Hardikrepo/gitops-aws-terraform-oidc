terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Local backend on purpose: this stack creates the remote backend
  # (S3 + DynamoDB) that envs/* use. Apply it once, by hand, with your
  # own AWS credentials before any GitHub Actions run exists.
}

provider "aws" {
  region = var.aws_region
}

# --- Remote state backend, shared by envs/dev, envs/staging, envs/prod ---

resource "aws_s3_bucket" "tf_state" {
  bucket        = "${var.project}-tf-state-${data.aws_caller_identity.current.account_id}"
  force_destroy = false

  tags = {
    Project   = var.project
    ManagedBy = "terraform-bootstrap"
  }
}

resource "aws_s3_bucket_versioning" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tf_state" {
  bucket                  = aws_s3_bucket.tf_state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "tf_lock" {
  name         = "${var.project}-tf-lock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = {
    Project   = var.project
    ManagedBy = "terraform-bootstrap"
  }
}

data "aws_caller_identity" "current" {}

# --- GitHub OIDC provider (one per AWS account) ---

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # GitHub's OIDC intermediate cert thumbprint. AWS validates the full
  # chain against its own trust store, not just this value, but the
  # field is still required.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

# --- One IAM role per environment, trust-scoped to the matching ---
# --- GitHub Environment (repo:ORG/REPO:environment:NAME).        ---

module "env_roles" {
  source = "../modules/oidc-role"

  for_each = toset(var.environments)

  project          = var.project
  environment      = each.value
  oidc_provider    = aws_iam_openid_connect_provider.github
  github_org       = var.github_org
  github_repo      = var.github_repo
  state_bucket_arn = aws_s3_bucket.tf_state.arn
  lock_table_arn   = aws_dynamodb_table.tf_lock.arn
}

# --- Read-only lock-monitor role, for the lock-doctor workflow. ---
# Deliberately NOT trusted via `environment:<name>` like the roles
# above: this runs on a schedule with no human in the loop, so it must
# not sit behind an environment's required-reviewer gate. It can only
# GetItem the lock table - nothing else - so an unattended/automated
# run of this role has nothing destructive it could do even if abused.
# Actual remediation (DeleteItem to clear a lock) still goes through
# the per-environment roles above, which *are* environment-gated.

data "aws_iam_policy_document" "lock_monitor_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Matches schedule/workflow_dispatch/workflow_run runs on main -
    # those events carry `ref:refs/heads/main` in the sub claim rather
    # than an `environment:` claim.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_org}/${var.github_repo}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "lock_monitor" {
  name               = "${var.project}-lock-monitor-gha"
  assume_role_policy = data.aws_iam_policy_document.lock_monitor_trust.json

  tags = {
    Project   = var.project
    ManagedBy = "terraform-bootstrap"
  }
}

data "aws_iam_policy_document" "lock_monitor_permissions" {
  statement {
    sid       = "ReadLockTableOnly"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.tf_lock.arn]
  }
}

resource "aws_iam_role_policy" "lock_monitor" {
  name   = "${var.project}-lock-monitor-policy"
  role   = aws_iam_role.lock_monitor.id
  policy = data.aws_iam_policy_document.lock_monitor_permissions.json
}
