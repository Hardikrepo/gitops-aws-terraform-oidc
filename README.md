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
agents/
  lock-doctor/     script behind the lock-doctor workflow (see below)
.github/workflows/
  terraform-plan.yml    on PR: fmt/validate/plan for all three envs
  terraform-apply.yml   on push to main: apply dev -> staging -> prod, in order
  lock-doctor.yml        scheduled: detects stuck state locks, opens a diagnosis issue
  unlock-approved.yml    manual, human-triggered: clears a lock once approved
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
     `TF_LOCK_TABLE` (= `lock_table` output),
     `LOCK_MONITOR_ROLE_ARN` (= `lock_monitor_role_arn` output).
   - Repo-level secret: `ANTHROPIC_API_KEY` (used by lock-doctor's diagnosis step).

4. Open a PR touching `envs/**` — `terraform-plan.yml` runs plan for all
   three environments using each one's scoped role. Merge to `main` —
   `terraform-apply.yml` applies dev, then staging, then prod (pausing for
   approval if you set a reviewer gate on prod).

## Disaster recovery: stuck state lock

If a CI run gets killed mid-apply (cancelled, runner died, etc.), Terraform's
DynamoDB lock can be left held, blocking every future plan/apply for that
environment. Two workflows handle this, split by autonomy level — same
principle as the rest of this project: read is unattended, write is
human-approved.

- **`lock-doctor.yml`** runs every 30 minutes (plus after every
  plan/apply, plus on demand). It uses a dedicated, read-only IAM role
  (`lock_monitor_role_arn` — see `bootstrap/main.tf`) that can only
  `dynamodb:GetItem` the lock table, nothing else, and isn't gated by a
  GitHub Environment (it has to run unattended). If a lock looks
  stale, it asks Claude to weigh the lock's age against the correlated
  GitHub Actions run status and writes its reasoning — not just a
  timeout — into a GitHub issue labeled `state-lock`. It never clears
  a lock itself.
- **`unlock-approved.yml`** is manual (`workflow_dispatch` only). You
  run it by hand from the Actions tab with the `environment` and
  `lock_id` from the issue. It reuses the *same* per-environment role
  and `environment:` gate as `terraform-apply.yml` — so unlocking
  `prod` still needs whatever reviewer approval you configured on that
  Environment in step 3.

## Replacing the demo workload

`modules/app-stack` is intentionally minimal so the pipeline can be proven
end-to-end before real infra goes in. Anything you add there must keep the
`gitops-aws-oidc-<environment>-*` naming convention, or you'll need to widen
the resource ARNs in `modules/oidc-role/main.tf` to match.
