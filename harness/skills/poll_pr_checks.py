"""
poll_pr_checks — deterministic CODE skill.

Polls a PR for:
1. Standard GitHub check runs (CI)
2. Prisma Cloud security scan comment (posted by CI bot)

Classifies the result so the pr_validator agent can decide next action.
"""
import os
import re
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from step_types.github_api import _require_kim_codefresh_repo, github_request


def _get_check_status(repo: str, pr_number: int) -> dict:
    """Get all check runs for the PR head commit."""
    pr = github_request("GET", f"/repos/{repo}/pulls/{pr_number}")
    if not pr:
        return {"status": "unknown"}
    sha = pr["head"]["sha"]
    checks = github_request("GET", f"/repos/{repo}/commits/{sha}/check-runs")
    if not checks:
        return {"status": "pending", "sha": sha}
    runs = checks.get("check_runs", [])
    if not runs:
        return {"status": "pending", "sha": sha}
    statuses = {r["conclusion"] for r in runs if r.get("conclusion")}
    if "failure" in statuses or "cancelled" in statuses:
        failed = [r["name"] for r in runs if r.get("conclusion") in ("failure", "cancelled")]
        return {"status": "failed", "failed_checks": failed, "sha": sha}
    if all(r.get("conclusion") == "success" for r in runs):
        return {"status": "passed", "sha": sha}
    return {"status": "pending", "sha": sha}


def _find_security_scan_comment(repo: str, pr_number: int) -> dict:
    """Look for Prisma Cloud / security scan result in PR comments."""
    comments = github_request("GET", f"/repos/{repo}/issues/{pr_number}/comments?per_page=50")
    if not comments:
        return {"found": False}
    for comment in reversed(comments):
        body = comment.get("body", "")
        if "Security Report" in body or "Fixed CVEs" in body or "Prisma" in body:
            fixed = re.search(r"Fixed CVEs:\s*(\d+)", body)
            new_vulns = re.search(r"New CVEs:\s*(\d+)", body)
            return {
                "found": True,
                "fixed_cves": int(fixed.group(1)) if fixed else 0,
                "new_cves": int(new_vulns.group(1)) if new_vulns else None,
                "scan_url": re.search(r"https://app\.prismacloud[^\s\)]+", body),
                "comment_url": comment.get("html_url"),
            }
    return {"found": False}


def execute(state: dict, config: dict) -> dict:
    repo = _require_kim_codefresh_repo()
    pr = state.get("pr", {})
    pr_number = pr.get("number")
    max_wait = config.get("max_wait_seconds", 600)
    poll_interval = config.get("poll_interval_seconds", 30)

    if not pr_number:
        raise ValueError("pr.number missing from state")

    print(f"[poll_pr_checks] Polling PR #{pr_number} for checks (max {max_wait}s)...")
    elapsed = 0

    while elapsed < max_wait:
        check_result = _get_check_status(repo, pr_number)
        scan_result = _find_security_scan_comment(repo, pr_number)

        print(f"[poll_pr_checks] t={elapsed}s checks={check_result['status']} scan={'found' if scan_result['found'] else 'waiting'}")

        if check_result["status"] == "failed":
            return {
                "checks_status": "failed",
                "failed_checks": check_result.get("failed_checks", []),
                "security_scan": scan_result,
                "failure_class": "ci_failed",
            }

        if check_result["status"] == "passed" and scan_result["found"]:
            new_cves = scan_result.get("new_cves", 0) or 0
            return {
                "checks_status": "passed",
                "security_scan": scan_result,
                "failure_class": None if new_cves == 0 else "scan_still_vulnerable",
            }

        time.sleep(poll_interval)
        elapsed += poll_interval

    # Timeout
    return {
        "checks_status": "timeout",
        "failure_class": "infra_flaky",
        "security_scan": {"found": False},
    }
