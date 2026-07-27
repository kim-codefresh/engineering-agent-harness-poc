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
import trigger_e2e as _trigger_e2e


def _comment_on_linear_execute(state, config):
    """Post mitigation details to Linear as a comment (stub — real impl uses Linear API)."""
    details = state.get("mitigation_details", "No mitigation details provided")
    ticket_id = state.get("ticket_id", "")
    print(f"[comment_on_linear] Would post to Linear {ticket_id}:")
    print(f"  {details[:200]}...")
    return {"comment_result": {"status": "posted", "ticket_id": ticket_id}}


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
    "trigger_e2e":               _Adapter(_trigger_e2e.execute),
    "comment_on_linear":         _CommentAdapter(),
}
