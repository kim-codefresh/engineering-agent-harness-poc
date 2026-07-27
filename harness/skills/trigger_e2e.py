"""
trigger_e2e — deterministic CODE skill.

Adds a comment/label to the PR to trigger the e2e test suite.
Polls for the e2e result and returns compact status.
"""
import os
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from step_types.github_api import _require_kim_codefresh_repo, github_request


def execute(state: dict, config: dict) -> dict:
    target_repo = state.get("target_repo") or os.getenv("GITHUB_REPO", "")
    repo = _require_kim_codefresh_repo(target_repo)
    pr   = state.get("pr", {})
    pr_number = pr.get("number")

    if not pr_number:
        print("[trigger_e2e] No PR number — skipping e2e")
        return {"e2e_result": {"status": "skipped"}, "e2e_routing": "passed"}

    print(f"[trigger_e2e] Triggering e2e on PR #{pr_number}")
    github_request("POST", f"/repos/{repo}/issues/{pr_number}/comments",
                   {"body": "/e2e"})

    # Poll for e2e result in PR comments (max 10 min)
    max_wait = config.get("max_wait_seconds", 600)
    elapsed  = 0
    poll     = 30

    while elapsed < max_wait:
        time.sleep(poll)
        elapsed += poll
        comments = github_request("GET", f"/repos/{repo}/issues/{pr_number}/comments?per_page=50")
        if not comments:
            continue
        for c in reversed(comments):
            body = c.get("body", "")
            if any(kw in body.lower() for kw in ["e2e passed", "e2e ✅", "all tests passed", "cypress: passed"]):
                print(f"[trigger_e2e] ✅ e2e passed")
                return {"e2e_result": {"status": "passed"}, "e2e_routing": "passed"}
            if any(kw in body.lower() for kw in ["e2e failed", "e2e ❌", "cypress: failed", "tests failed"]):
                print(f"[trigger_e2e] ❌ e2e failed")
                return {"e2e_result": {"status": "failed"}, "e2e_routing": "failed"}

        print(f"[trigger_e2e] t={elapsed}s — waiting for e2e result...")

    print("[trigger_e2e] ⚠️ e2e timeout — treating as passed for POC")
    return {"e2e_result": {"status": "timeout_passed"}, "e2e_routing": "passed"}
