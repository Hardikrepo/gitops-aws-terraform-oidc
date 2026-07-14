# gitops-aws-oidc

GitOps for AWS with Terraform, promoted through GitHub Actions that assume
an AWS IAM role via OIDC — no long-lived `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` stored anywhere. Includes two small Claude-backed
agents: `lock-doctor` (detects and diagnoses stuck Terraform state locks) and
`plan-reviewer` (flags risky changes in a PR's Terraform plan before merge).

**Repo:** `Hardikrepo/gitops-aws-terraform-oidc` (GitHub) · **Local path:** `~/gitops-aws-oidc`

## How the trust works

1. AWS trusts GitHub's OIDC provider (`token.actions.githubusercontent.com`), created once in `bootstrap/`.
2. Each environment (`dev`, `staging`, `prod`) gets its own IAM role. The role's trust policy only accepts tokens whose `sub` claim is exactly `repo:<org>/<repo>:environment:<env>` — i.e. a workflow job that explicitly declared `environment: <env>`.
3. Each role's permissions are scoped to resources namespaced `gitops-aws-oidc-<env>-*`, plus its slice of the shared state bucket/lock table. A `dev` job cannot touch `prod` resources even if the workflow is compromised.
4. GitHub Environment protection rules (Settings → Environments → `prod` → required reviewers) are the actual promotion gate — merges to `main` don't auto-deploy prod without a human approval.
5. A separate, narrower `lock-monitor` role (read-only, not environment-gated) backs the `lock-doctor` detection workflow — see [Disaster recovery](#disaster-recovery-stuck-state-lock).

## Project structure and how each part runs

```
bootstrap/                  one-time, local, your own AWS creds
  main.tf                     OIDC provider, state bucket, lock table,
                               per-env IAM roles, lock-monitor role
  variables.tf                 github_org / github_repo / project / region
  outputs.tf                   state_bucket, lock_table, role_arns, lock_monitor_role_arn

modules/                    reusable Terraform modules — never applied directly
  oidc-role/                   IAM role + least-privilege policy factory, used by bootstrap
  app-stack/                    the actual workload, used by envs/*

envs/                       one Terraform root per environment — applied via CI
  dev/  staging/  prod/        own state file, own IAM role, applied independently

agents/lock-doctor/         Python script — invoked by lock-doctor.yml, not run standalone in CI
  check_lock.py                reads the lock table, asks Claude to assess staleness, opens an issue
  requirements.txt              boto3, anthropic

agents/plan-reviewer/       Python script — invoked by terraform-plan.yml, not run standalone in CI
  review_plan.py                classifies plan changes, asks Claude to assess risk, posts/updates a PR comment
  requirements.txt              anthropic

.github/workflows/          automation entry points
  terraform-plan.yml           runs on every PR touching envs/** or modules/** (also runs plan-reviewer)
  terraform-apply.yml          runs on every push to main touching envs/** or modules/**
  lock-doctor.yml               runs on a 30-minute schedule + after plan/apply + on demand
  unlock-approved.yml           runs only when a human triggers it manually
```

### `bootstrap/` — execute once, locally, by hand

This is the only piece that doesn't go through OIDC (chicken-and-egg: CI needs
a role to exist before it can assume one). Run it with your own AWS
credentials:

```bash
aws sts get-caller-identity        # confirm you're authenticated
cd bootstrap
terraform init
terraform plan                     # review what will be created
terraform apply
```

Creates 14 resources: the OIDC provider, the shared S3 state bucket +
DynamoDB lock table, one IAM role per environment (`dev`/`staging`/`prod`),
and the read-only `lock-monitor` role. Note the outputs — `state_bucket`,
`lock_table`, `role_arns`, `lock_monitor_role_arn` — you'll need all of them
in the GitHub setup step below. Re-run `terraform apply` here only when you
change `bootstrap/main.tf` itself (e.g. adding a new environment).

### `modules/oidc-role/` and `modules/app-stack/` — not executed directly

These are Terraform modules, referenced by `source = "../modules/..."` from
`bootstrap/main.tf` and `envs/*/main.tf`. They apply automatically as part of
whichever root module calls them — there's nothing to run inside these
directories themselves.

### `envs/{dev,staging,prod}/` — execute via CI (or manually for one env)

Normal path is automatic: `terraform-plan.yml` / `terraform-apply.yml` drive
these. To run one by hand (e.g. to debug), you need the backend values from
`bootstrap`'s outputs:

```bash
cd envs/dev
terraform init \
  -backend-config="bucket=<state_bucket output>" \
  -backend-config="dynamodb_table=<lock_table output>" \
  -backend-config="region=us-east-1"
terraform plan  -var="deployed_commit=local-test"
terraform apply -var="deployed_commit=local-test"
```

Manual runs still need AWS credentials for that environment's role (or your
own admin credentials) — CI is what actually uses the OIDC role.

### `.github/workflows/terraform-plan.yml` — automatic, on pull request

Triggers on any PR touching `envs/**`, `modules/**`, or the workflow file
itself. Matrix over `[dev, staging, prod]`; each job assumes that
environment's OIDC role, runs `fmt -check`, `validate`, and `plan`, then
hands the plan to `plan-reviewer` (below). No manual action needed — just
open the PR.

### `agents/plan-reviewer/review_plan.py` — invoked by terraform-plan.yml (or manually)

Runs automatically as the last step of every `terraform-plan.yml` job. It
reads the `terraform show -json` plan, classifies each resource change
(create/update/delete/replace), flags deletions, replacements, and updates to
IAM/security/public-access resource types, and — only if there are real
changes — asks Claude to write a 2-4 sentence risk assessment. The result is
posted as a single PR comment per environment, updated in place on every
push (not re-posted). **It never fails the CI job or blocks the merge** —
review failures are caught and logged, not raised, and there's no
`risk_level` check gating anything; a human reading the PR decides what to
do with it. To run it by hand against a plan you generated locally:

```bash
pip install -r agents/plan-reviewer/requirements.txt
python agents/plan-reviewer/review_plan.py \
  --env dev \
  --plan-json plan.json \
  --plan-text plan.txt \
  --repo Hardikrepo/gitops-aws-terraform-oidc \
  --pr-number 12 \
  --anthropic-api-key "$ANTHROPIC_API_KEY"
```

### `.github/workflows/terraform-apply.yml` — automatic, on push to `main`

Triggers on push to `main` (i.e. a merged PR) touching the same paths.
Applies `dev` → `staging` → `prod` in order (`max-parallel: 1`), pausing for
approval at any environment with a reviewer gate configured. No manual
action needed beyond merging — and approving the prod gate, if you set one.

### `agents/lock-doctor/check_lock.py` — invoked by lock-doctor.yml (or manually)

Not meant to be run by hand in normal operation, but you can for testing —
requires AWS credentials with `dynamodb:GetItem` on the lock table, `gh` CLI
authenticated (for issue creation), and an Anthropic API key:

```bash
pip install -r agents/lock-doctor/requirements.txt
python agents/lock-doctor/check_lock.py \
  --state-bucket <state_bucket output> \
  --lock-table <lock_table output> \
  --envs dev,staging,prod \
  --anthropic-api-key "$ANTHROPIC_API_KEY"
```

### `.github/workflows/lock-doctor.yml` — automatic (scheduled)

Runs every 30 minutes (cron), immediately after every `terraform-plan` /
`terraform-apply` run, and on demand via the Actions tab
(`workflow_dispatch`). Uses the read-only `lock-monitor` role — no human
input required; it only ever opens/updates a GitHub issue.

### `.github/workflows/unlock-approved.yml` — manual only

Never fires automatically. Run it from the Actions tab (`workflow_dispatch`)
with the `environment` and `lock_id` values from the issue `lock-doctor`
opened. It reuses the same environment-gated IAM role as
`terraform-apply.yml`, so clearing a `prod` lock still needs whatever
reviewer approval is configured on that Environment.

## First-time setup

1. **Placeholders are already filled in.** `bootstrap/variables.tf` points at `github_org = "Hardikrepo"`, `github_repo = "gitops-aws-terraform-oidc"`.
2. **Apply `bootstrap/`** as shown above. Save the four outputs.
3. **Push this repo to GitHub** as `Hardikrepo/gitops-aws-terraform-oidc` (rename the local branch `master` → `main` first, since the workflows trigger on `main`).
4. **Configure GitHub:**
   - Settings → Environments: create `dev`, `staging`, `prod`; add a required-reviewer rule on `prod` (optionally `staging`).
   - Per-Environment variable `AWS_ROLE_ARN` = the matching entry from `role_arns`.
   - Repo-level variables: `AWS_REGION`, `TF_STATE_BUCKET` (= `state_bucket`), `TF_LOCK_TABLE` (= `lock_table`), `LOCK_MONITOR_ROLE_ARN` (= `lock_monitor_role_arn`).
   - Repo-level secret: `ANTHROPIC_API_KEY` (used by both `lock-doctor` and `plan-reviewer`).
5. **Open a PR** touching `envs/**` to see `terraform-plan.yml` run; **merge to `main`** to see `terraform-apply.yml` apply dev → staging → prod.

## Disaster recovery: stuck state lock

If a CI run gets killed mid-apply (cancelled, runner died, etc.), Terraform's
DynamoDB lock can be left held, blocking every future plan/apply for that
environment. Detection is automatic and read-only; remediation is manual and
human-approved — same split as the rest of this project.

- **`lock-doctor.yml`** — detects a stale-looking lock, asks Claude to weigh
  the lock's age against the correlated GitHub Actions run status, and opens
  a GitHub issue labeled `state-lock` with the reasoning. Never clears a lock
  itself.
- **`unlock-approved.yml`** — the human-triggered fix. See above.

## Replacing the demo workload

`modules/app-stack` is intentionally minimal (an S3 bucket + SSM parameter)
so the pipeline can be proven end-to-end before real infra goes in. Anything
you add there must keep the `gitops-aws-oidc-<environment>-*` naming
convention, or you'll need to widen the resource ARNs in
`modules/oidc-role/main.tf` to match.
