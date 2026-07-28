import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills'))

from . import call_llm, gather_evidence, merge_pr, open_pr, record_outcome, run_tests

# Import skill modules as step type adapters
import parse_cve_ticket as _parse_cve_ticket
import fetch_advisory as _fetch_advisory
import find_and_patch_dependency as _find_patch
import github_create_branch as _create_branch
import github_commit_files as _commit_files
import github_open_pr as _open_pr_skill
import poll_pr_checks as _poll_checks
import recall_past_cve as _recall_cve
import run_openhands as _run_openhands
import run_local_checks as _run_local_checks
import auto_route as _auto_route
import trigger_e2e as _trigger_e2e


def _comment_on_linear_execute(state, config):
    """Post a comment to the Linear ticket using the GraphQL API."""
    import json, os, urllib.request

    linear_api_key = os.getenv("LINEAR_API_KEY", "")
    ticket_id      = state.get("ticket_id", "")
    thread_id      = state.get("thread_id", "")
    vulnerabilities = state.get("vulnerabilities", [])
    mitigation     = state.get("mitigation_details", "")
    exhaustion     = state.get("exhaustion_reason", "")
    harness_url    = os.getenv("HARNESS_URL", "http://agent-harness.agent-harness.svc.cluster.local:8000")

    # Build comment body
    vuln_line = ""
    if vulnerabilities:
        v = vulnerabilities[0]
        vuln_line = f"\n- **Package:** `{v.get('packages')}@{v.get('packageVersion')}` → fix: `{v.get('fix_version','?')}`"

    if mitigation:
        body = (f"🤖 **Agent Harness — Mitigation Proposal**\n\n"
                f"The agent could not auto-fix this CVE.{vuln_line}\n\n"
                f"**Proposed workaround:**\n{mitigation}\n\n"
                f"👉 [Review in Harness UI]({harness_url}) → Runs tab")
    elif exhaustion:
        body = (f"🤖 **Agent Harness — Escalation Required**\n\n"
                f"The agent exhausted its retries on CVE.{vuln_line}\n\n"
                f"**Reason:** {exhaustion}\n\n"
                f"👉 [Review in Harness UI]({harness_url}) → Runs tab · Options: retry or close")
    else:
        body = (f"🤖 **Agent Harness — Needs Attention**\n\n"
                f"Thread: `{thread_id}`{vuln_line}\n\n"
                f"👉 [Review in Harness UI]({harness_url}) → Runs tab")

    if not linear_api_key:
        print(f"[comment_on_linear] No LINEAR_API_KEY — skipping (would post: {body[:100]}...)")
        return {"comment_result": {"status": "skipped_no_key"}}

    # Find issue ID from identifier
    try:
        search = json.dumps({
            "query": "{ issues(first:20) { nodes { id identifier } } }"
        })
        req = urllib.request.Request("https://api.linear.app/graphql",
            data=search.encode(),
            headers={"Authorization": linear_api_key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        issue_id = None
        for issue in data.get("data", {}).get("issues", {}).get("nodes", []):
            if issue.get("identifier") == ticket_id:
                issue_id = issue["id"]
                break

        if not issue_id:
            print(f"[comment_on_linear] Could not find issue {ticket_id}")
            return {"comment_result": {"status": "issue_not_found"}}

        mutation = json.dumps({
            "query": "mutation($issueId: String!, $body: String!) { commentCreate(input: {issueId: $issueId, body: $body}) { success } }",
            "variables": {"issueId": issue_id, "body": body}
        })
        req2 = urllib.request.Request("https://api.linear.app/graphql",
            data=mutation.encode(),
            headers={"Authorization": linear_api_key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req2, timeout=5) as r:
            result = json.loads(r.read())
        success = result.get("data", {}).get("commentCreate", {}).get("success", False)
        print(f"[comment_on_linear] Posted to {ticket_id}: {success}")
        return {"comment_result": {"status": "posted" if success else "failed"}}
    except Exception as e:
        print(f"[comment_on_linear] Error: {e}")
        return {"comment_result": {"status": "error", "error": str(e)}}


class _Adapter:
    """Wraps a skill module's execute() to look like a step_type module."""
    def __init__(self, fn):
        self._fn = fn
    def execute(self, state, config):
        return self._fn(state, config)


class _CommentAdapter:
    def execute(self, state, config):
        return _comment_on_linear_execute(state, config)


REGISTRY = {
    # Existing step types
    "call_llm":        call_llm,
    "gather_evidence": gather_evidence,
    "open_pr":         open_pr,
    "merge_pr":        merge_pr,
    "record_outcome":  record_outcome,
    "run_tests":       run_tests,

    # CVE remediation skills
    "parse_cve_ticket":          _Adapter(_parse_cve_ticket.execute),
    "fetch_advisory":            _Adapter(_fetch_advisory.execute),
    "find_and_patch_dependency": _Adapter(_find_patch.execute),
    "github_create_branch":      _Adapter(_create_branch.execute),
    "github_commit_files":       _Adapter(_commit_files.execute),
    "github_open_pr":            _Adapter(_open_pr_skill.execute),
    "poll_pr_checks":            _Adapter(_poll_checks.execute),
    "recall_past_cve":           _Adapter(_recall_cve.execute),
    "run_openhands":             _Adapter(_run_openhands.execute),
    "run_local_checks":          _Adapter(_run_local_checks.execute),
    "auto_route":                _Adapter(_auto_route.execute),
    "trigger_e2e":               _Adapter(_trigger_e2e.execute),
    "comment_on_linear":         _CommentAdapter(),
}
