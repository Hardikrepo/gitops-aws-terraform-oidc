locals {
  role_name = "${var.project}-${var.environment}-gha"

  # Everything this role is allowed to touch is namespaced under this
  # prefix by app-stack's naming convention, so a compromised/misused
  # dev role can't reach staging or prod resources.
  resource_prefix = "${var.project}-${var.environment}"
}

data "aws_iam_policy_document" "trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Scoped to this exact repo AND this exact GitHub Environment.
    # A workflow run only gets this claim if the job declares
    # `environment: <name>` - which is also where GitHub Environment
    # protection rules (required reviewers, wait timers) attach.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_org}/${var.github_repo}:environment:${var.environment}"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = local.role_name
  assume_role_policy = data.aws_iam_policy_document.trust.json

  tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform-bootstrap"
  }
}

data "aws_iam_policy_document" "permissions" {
  # Remote state: read/write only this environment's state file.
  statement {
    sid       = "StateObjectAccess"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${var.state_bucket_arn}/envs/${var.environment}/*"]
  }

  statement {
    sid       = "StateBucketList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [var.state_bucket_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["envs/${var.environment}/*"]
    }
  }

  statement {
    sid       = "StateLock"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = [var.lock_table_arn]
  }

  # Application infra: only resources namespaced with this env's prefix.
  statement {
    sid    = "AppS3"
    effect = "Allow"
    actions = [
      "s3:CreateBucket",
      "s3:DeleteBucket",
      "s3:PutBucketVersioning",
      "s3:PutBucketTagging",
      "s3:PutEncryptionConfiguration",
      "s3:PutBucketPublicAccessBlock",
      "s3:GetBucket*",
      "s3:ListBucket",
    ]
    resources = ["arn:aws:s3:::${local.resource_prefix}-*"]
  }

  statement {
    sid       = "AppSSM"
    effect    = "Allow"
    actions   = ["ssm:PutParameter", "ssm:GetParameter", "ssm:DeleteParameter", "ssm:AddTagsToResource"]
    resources = ["arn:aws:ssm:*:*:parameter/${local.resource_prefix}/*"]
  }

  # Lets plan-reviewer (running under this role in terraform-plan.yml) call
  # Claude via Bedrock using the job's own OIDC-issued AWS credentials -
  # no separate API key/secret needed. Scoped to one model, any region.
  statement {
    sid       = "InvokeClaudeViaBedrock"
    effect    = "Allow"
    actions   = ["bedrock:InvokeModel"]
    resources = ["arn:aws:bedrock:*::foundation-model/${var.bedrock_model_id}"]
  }
}

resource "aws_iam_role_policy" "this" {
  name   = "${local.role_name}-policy"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.permissions.json
}
