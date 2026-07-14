#!/usr/bin/env python3
# Detects stuck Terraform state locks and opens/updates a GitHub issue
# with Claude's diagnosis of whether it's safe to clear. Never clears a
# lock itself - remediation is a separate, human-approved workflow
# (unlock-approved.yml) gated by the same GitHub Environment protection
# rules as terraform-apply.yml.

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import boto3

ISSUE_LABEL = "state-lock"


@dataclass
class LockInfo:
    lock_id: str
    who: str
    operation: str
    created: datetime
    version: str
    raw: dict

    @property
    def age_minutes(self) -> float:
        return (datetime.now(timezone.utc) - self.created).total_seconds() / 60


def lock_id_for_env(state_bucket: str, env: str) -> str:
    # Matches the S3 backend's DynamoDB LockID format: "<bucket>/<key>".
    # We don't use Terraform workspaces, so there's no ":<workspace>" suffix.
    return f"{state_bucket}/envs/{env}/terraform.tfstate"


def get_lock(dynamodb, lock_table: str, state_bucket: str, env: str) -> Optional[LockInfo]:
    lock_id = lock_id_for_env(state_bucket, env)
    resp = dynamodb.get_item(TableName=lock_table, Key={"LockID": {"S": lock_id}})
    item = resp.get("Item")
    if not item:
        return None

    info_raw = item.get("Info", {}).get("S", "{}")
    info = json.loads(info_raw)
    created_str = info.get("Created")
    created = (
        datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        if created_str
        else datetime.now(timezone.utc)
    )
    return LockInfo(
        lock_id=lock_id,
        who=info.get("Who", "unknown"),
        operation=info.get("Operation", "unknown"),
        created=created,
        version=info.get("Version", "unknown"),
        raw=info,
    )


def gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def get_recent_run_status(env: str) -> dict:
    # Best-effort correlation: has the workflow whose matrix includes
    # this env finished (and how), or does it look like it's still running?
    # Never fatal - if this fails, the assessment just proceeds without it.
    try:
        raw = gh(
            "run", "list",
            "--workflow=terraform-apply.yml",
            "--branch=main",
            "--limit=5",
            "--json=databaseId,status,conclusion,createdAt,event",
        )
        runs = json.loads(raw)
        if not runs:
            return {"available": False}

        latest = runs[0]
        jobs_raw = gh("run", "view", str(latest["databaseId"]), "--json=jobs")
        jobs = json.loads(jobs_raw).get("jobs", [])
        env_job = next((j for j in jobs if f"({env})" in j.get("name", "")), None)

        return {
            "available": True,
            "run_id": latest["databaseId"],
            "run_status": latest["status"],
            "run_conclusion": latest.get("conclusion"),
            "env_job_status": env_job.get("status") if env_job else "not_found",
            "env_job_conclusion": env_job.get("conclusion") if env_job else None,
        }
    except Exception as exc:  # noqa: BLE001 - best-effort, never blocks the assessment
        return {"available": False, "error": str(exc)}


def assess_with_claude(env: str, lock: LockInfo, run_status: dict, aws_region: str, model: str) -> dict:
    from anthropic import AnthropicBedrockMantle

    # Claude via Bedrock, authenticated with this job's own OIDC-issued AWS
    # credentials (already in the environment from configure-aws-credentials)
    # - no separate API key/secret to manage. See modules/oidc-role's
    # InvokeClaudeViaBedrock statement for the IAM side of this.
    client = AnthropicBedrockMantle(aws_region=aws_region)

    prompt = f"""A Terraform state lock in DynamoDB has been held for {lock.age_minutes:.0f} minutes \
in the "{env}" environment of an AWS GitOps project. Assess whether it looks like a stale lock \
left behind by a crashed/killed CI run (safe to clear) versus a legitimately in-progress operation \
(NOT safe to clear - clearing it mid-run risks state corruption).

Lock info from DynamoDB:
{json.dumps(lock.raw, indent=2)}

Most recent terraform-apply.yml run on main, correlated by GitHub Actions API:
{json.dumps(run_status, indent=2)}

Respond with ONLY a JSON object, no prose outside it:
{{
  "safe_to_unlock": true | false,
  "confidence": "low" | "medium" | "high",
  "reasoning": "2-3 sentences a human reviewer can act on"
}}

If the correlated run data is unavailable or ambiguous, default to safe_to_unlock=false and low confidence \
- a human should decide when the evidence is unclear."""

    response = client.messages.create(
        model=model,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "safe_to_unlock": False,
            "confidence": "low",
            "reasoning": f"Could not parse model response, defaulting to manual review. Raw: {text[:300]}",
        }


def find_open_issue(env: str) -> Optional[str]:
    raw = gh(
        "issue", "list",
        f"--label={ISSUE_LABEL}",
        f"--search=env:{env} in:title",
        "--state=open",
        "--json=number",
        "--limit=1",
    )
    issues = json.loads(raw)
    return str(issues[0]["number"]) if issues else None


def upsert_issue(env: str, lock: LockInfo, run_status: dict, assessment: dict) -> None:
    title = f"[state-lock] {env} lock held {lock.age_minutes:.0f}m - env:{env}"
    body = f"""**Environment:** {env}
**Lock ID:** `{lock.lock_id}`
**Held by:** {lock.who}
**Operation:** {lock.operation}
**Age:** {lock.age_minutes:.0f} minutes

### Correlated CI run
```json
{json.dumps(run_status, indent=2)}
```

### Claude's assessment
- **Safe to unlock:** {assessment['safe_to_unlock']}
- **Confidence:** {assessment['confidence']}
- **Reasoning:** {assessment['reasoning']}

### To remediate
This agent never clears locks itself. If you agree it's safe, run the \
`unlock-approved` workflow (Actions tab -> unlock-approved -> Run workflow) with:
- `environment`: `{env}`
- `lock_id`: `{lock.lock_id}`

That workflow reuses the same environment-gated IAM role as `terraform-apply.yml`, \
so `prod` still requires an approval before the lock is cleared.
"""
    existing = find_open_issue(env)
    if existing:
        gh("issue", "comment", existing, f"--body={body}")
    else:
        gh("issue", "create", f"--title={title}", f"--body={body}", f"--label={ISSUE_LABEL}")


def close_resolved_issue(env: str) -> None:
    existing = find_open_issue(env)
    if existing:
        gh("issue", "close", existing, "--comment=Lock no longer present - auto-resolved by lock-doctor.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-bucket", required=True)
    parser.add_argument("--lock-table", required=True)
    parser.add_argument("--envs", default="dev,staging,prod")
    parser.add_argument("--stale-minutes", type=float, default=15.0)
    parser.add_argument("--aws-region", required=True)
    parser.add_argument("--model", default="anthropic.claude-sonnet-5")
    args = parser.parse_args()

    dynamodb = boto3.client("dynamodb")
    exit_code = 0

    for env in args.envs.split(","):
        env = env.strip()
        try:
            lock = get_lock(dynamodb, args.lock_table, args.state_bucket, env)
        except Exception as exc:  # noqa: BLE001
            print(f"[{env}] ERROR reading lock table: {exc}", file=sys.stderr)
            exit_code = 1
            continue

        if lock is None:
            print(f"[{env}] no lock held")
            close_resolved_issue(env)
            continue

        if lock.age_minutes < args.stale_minutes:
            print(f"[{env}] lock held {lock.age_minutes:.0f}m - under {args.stale_minutes}m threshold, skipping")
            continue

        print(f"[{env}] lock held {lock.age_minutes:.0f}m - assessing")
        run_status = get_recent_run_status(env)
        assessment = assess_with_claude(env, lock, run_status, args.aws_region, args.model)
        upsert_issue(env, lock, run_status, assessment)
        print(f"[{env}] assessment: {assessment}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
