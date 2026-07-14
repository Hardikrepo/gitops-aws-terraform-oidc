# gitops-aws-oidc

GitOps for AWS with Terraform, promoted through GitHub Actions that assume
an AWS IAM role via OIDC — no long-lived `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` stored anywhere.

## How the trust works

1. AWS trusts GitHub's OIDC provider (`token.actions.githubusercontent.com`),
   created once in `bootstrap/`.
2. Each environment (`dev`, `staging`, `prod`) gets its own IAM role
   (`modules/oidc-role`). The role's trust policy only accepts tokens whose
   `sub` claim is exactly
   `repo:<org>/<repo>:environment:<env>` — i.e. a workflow job that
   explicitly declared `environment: <env>`.
3. Each role's permissions are scoped to resources namespaced
   `gitops-aws-oidc-<env>-*`, plus its slice of the shared state bucket/lock
   table. A `dev` job cannot touch `prod` resources even if the workflow is
   compromised.
4. GitHub Environment protection rules (Settings → Environments →
   `prod` → required reviewers) are the actual promotion gate — merges to
   `main` don't auto-deploy prod without a human approval.

## Layout

```
bootstrap/        one-time setup: OIDC provider, state bucket + lock table,
                   one IAM role per environment. Apply by hand, locally,
                   with your own AWS credentials — chicken-and-egg problem
                   otherwise (CI needs a role to exist before it can assume one).
modules/
  oidc-role/       reusable: IAM role + least-privilege policy for one env
  app-stack/       the actual workload (currently a stand-in: S3 bucket + SSM param)
envs/
  dev/ staging/ prod/   one Terraform root per environment, own state file,
                         own IAM role, applied independently
.github/workflows/
  terraform-plan.yml    on PR: fmt/validate/plan for all three envs
  terraform-apply.yml   on push to main: apply dev -> staging -> prod, in order
```

## First-time setup

1. **Fill in the placeholders.** `bootstrap/variables.tf` has
   `github_org = "your-org"` and `github_repo = "your-repo"` — point these at
   the real repo this will live in (the OIDC trust condition is exact-match).

2. **Apply bootstrap locally**, with your own AWS credentials (this is the
   only step that doesn't go through OIDC, by necessity):
   ```
   cd bootstrap
   terraform init
   terraform apply
   ```
   Note the outputs: `state_bucket`, `lock_table`, `role_arns`.

3. **Push this repo to GitHub**, then configure it:
   - Settings → Environments: create `dev`, `staging`, `prod`. Add a
     required-reviewer protection rule on `prod` (and optionally `staging`).
   - For each Environment, add variable `AWS_ROLE_ARN` = the matching
     `role_arns[<env>]` output from step 2.
   - Repo-level (Settings → Secrets and variables → Actions → Variables):
     `AWS_REGION`, `TF_STATE_BUCKET` (= `state_bucket` output),
     `TF_LOCK_TABLE` (= `lock_table` output).

4. Open a PR touching `envs/**` — `terraform-plan.yml` runs plan for all
   three environments using each one's scoped role. Merge to `main` —
   `terraform-apply.yml` applies dev, then staging, then prod (pausing for
   approval if you set a reviewer gate on prod).

## Replacing the demo workload

`modules/app-stack` is intentionally minimal so the pipeline can be proven
end-to-end before real infra goes in. Anything you add there must keep the
`gitops-aws-oidc-<environment>-*` naming convention, or you'll need to widen
the resource ARNs in `modules/oidc-role/main.tf` to match.
