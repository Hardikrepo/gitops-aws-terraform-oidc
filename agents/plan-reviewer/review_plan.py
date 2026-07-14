#!/usr/bin/env python3
# Reads a `terraform show -json` plan, flags destructive/security-relevant
# changes, and asks Claude to write a human-readable risk assessment as a PR
# comment. Informational only - it never fails the CI job or blocks a merge;
# a human reviewing the PR decides what to do with it.

import argparse
import json
import subprocess
import sys
from typing import Optional

MARKER_PREFIX = "<!-- plan-risk-review:"

# Resource types where even a plain "update" is worth a human's attention -
# permissions, public access, and the trust boundary itself.
SENSITIVE_TYPES = {
    "aws_iam_role",
    "aws_iam_role_policy",
    "aws_iam_policy",
    "aws_iam_openid_connect_provider",
    "aws_s3_bucket_public_access_block",
    "aws_s3_bucket_policy",
    "aws_security_group",
    "aws_security_group_rule",
    "aws_dynamodb_table",
}

RISK_EMOJI = {"low": "\U0001F7E2", "medium": "\U0001F7E1", "high": "\U0001F534", "unknown": "⚪"}


def load_resource_changes(plan_json_path: str) -> list:
    with open(plan_json_path, encoding="utf-8") as f:
        plan = json.load(f)
    return plan.get("resource_changes", [])


def summarize(changes: list) -> tuple[dict, list]:
    counts = {"create": 0, "update": 0, "delete": 0, "replace": 0, "no-op": 0}
    flagged = []

    for change in changes:
        actions = change["change"]["actions"]
        address = change["address"]
        rtype = change["type"]

        if not actions or actions == ["no-op"]:
            counts["no-op"] += 1
        elif set(actions) == {"delete", "create"}:
            counts["replace"] += 1
            flagged.append({"address": address, "type": rtype, "action": "replace"})
        elif actions == ["delete"]:
            counts["delete"] += 1
            flagged.append({"address": address, "type": rtype, "action": "delete"})
        elif actions == ["create"]:
            counts["create"] += 1
        elif actions == ["update"]:
            counts["update"] += 1
            if rtype in SENSITIVE_TYPES:
                flagged.append({"address": address, "type": rtype, "action": "update"})

    return counts, flagged


def read_truncated(path: str, limit: int) -> str:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n... truncated ({len(text) - limit} more characters)"


def assess_with_claude(env: str, counts: dict, flagged: list, plan_text: str, api_key: str, model: str) -> dict:
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)

    prompt = f"""Review this Terraform plan for the "{env}" environment of an AWS GitOps project and assess its risk.

Change summary: {counts['create']} create, {counts['update']} update, {counts['delete']} delete, {counts['replace']} replace.

Flagged resources (deletions, replacements, or updates to IAM/security/public-access resources):
{json.dumps(flagged, indent=2) if flagged else "(none)"}

Full plan output (may be truncated):
{plan_text}

Respond with ONLY a JSON object, no prose outside it:
{{
  "risk_level": "low" | "medium" | "high",
  "summary": "2-4 sentences a human reviewer can act on - what's changing and why it matters",
  "concerns": ["specific concern 1", "specific concern 2"]
}}

"high" means a destructive or security-relevant change that could cause an outage, data loss, or a
permission widening if merged without scrutiny. "low" means routine, additive, easily-reversible changes.
"concerns" should be empty if risk_level is "low"."""

    response = client.messages.create(
        model=model,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "risk_level": "unknown",
            "summary": f"Automated review could not parse a response - review the plan manually. Raw: {text[:300]}",
            "concerns": [],
        }


def render_comment(env: str, counts: dict, assessment: dict) -> str:
    emoji = RISK_EMOJI.get(assessment["risk_level"], RISK_EMOJI["unknown"])
    concerns = "".join(f"- {c}\n" for c in assessment.get("concerns", []))
    concerns_section = f"\n**Concerns:**\n{concerns}" if concerns else ""

    return f"""{MARKER_PREFIX}{env} -->
### Plan risk review - `{env}`

**Changes:** {counts['create']} create, {counts['update']} update, {counts['delete']} delete, {counts['replace']} replace
**Risk level:** {emoji} {assessment['risk_level']}

{assessment['summary']}
{concerns_section}
<sub>Automated by `agents/plan-reviewer`, powered by Claude. Informational only - does not block merge.</sub>"""


def render_no_change_comment(env: str) -> str:
    return f"""{MARKER_PREFIX}{env} -->
### Plan risk review - `{env}`

No infrastructure changes in the latest plan.

<sub>Automated by `agents/plan-reviewer`, powered by Claude. Informational only - does not block merge.</sub>"""


def gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def find_existing_comment(repo: str, pr_number: str, env: str) -> Optional[str]:
    marker = f"{MARKER_PREFIX}{env} -->"
    raw = gh("api", f"repos/{repo}/issues/{pr_number}/comments", "--paginate", "--jq", ".[] | {id, body}")
    for line in raw.strip().splitlines():
        if not line:
            continue
        comment = json.loads(line)
        if marker in comment.get("body", ""):
            return str(comment["id"])
    return None


def upsert_comment(repo: str, pr_number: str, env: str, body: str) -> None:
    existing = find_existing_comment(repo, pr_number, env)
    if existing:
        gh("api", f"repos/{repo}/issues/comments/{existing}", "-X", "PATCH", "-f", f"body={body}")
    else:
        gh("api", f"repos/{repo}/issues/{pr_number}/comments", "-X", "POST", "-f", f"body={body}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True)
    parser.add_argument("--plan-json", required=True)
    parser.add_argument("--plan-text", required=True)
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--pr-number", required=True)
    parser.add_argument("--anthropic-api-key", required=True)
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--plan-text-limit", type=int, default=15000)
    args = parser.parse_args()

    changes = load_resource_changes(args.plan_json)
    counts, flagged = summarize(changes)
    total_real_changes = counts["create"] + counts["update"] + counts["delete"] + counts["replace"]

    if total_real_changes == 0:
        body = render_no_change_comment(args.env)
    else:
        plan_text = read_truncated(args.plan_text, args.plan_text_limit)
        assessment = assess_with_claude(args.env, counts, flagged, plan_text, args.anthropic_api_key, args.model)
        body = render_comment(args.env, counts, assessment)

    upsert_comment(args.repo, args.pr_number, args.env, body)
    print(f"[{args.env}] posted plan risk review ({total_real_changes} real changes, {len(flagged)} flagged)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - never fail the CI job over a review-tool bug
        print(f"plan-reviewer failed (non-fatal): {exc}", file=sys.stderr)
        sys.exit(0)
