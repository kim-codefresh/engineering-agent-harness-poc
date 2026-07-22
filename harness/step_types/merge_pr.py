"""
merge_pr — deterministic step type.

Merges an open PR via the GitHub API using a squash merge.
This is a risky, irreversible action — it must be code, never an agent decision.

The human already approved at the code_owner_review gate.
This step simply executes the merge instruction.
"""
from .github_api import _require_kim_codefresh_repo, github_request


def execute(state: dict, config: dict) -> dict:
    # ── Safety gate — must be kim-codefresh repo ─────────────────────────────
    repo = _require_kim_codefresh_repo()

    pr = state.get("pr", {})
    pr_number = pr.get("number")

    if not pr_number:
        raise ValueError("No PR number in state — cannot merge. Was open_pr run first?")

    print(f"[merge_pr] Merging PR #{pr_number} in {repo} via squash...")

    result = github_request("PUT", f"/repos/{repo}/pulls/{pr_number}/merge", {
        "commit_title":   f"fix: {pr.get('title', f'PR #{pr_number}')}",
        "commit_message": f"Merged by agent-harness after human approval.",
        "merge_method":   "squash",
    })

    if not result or not result.get("merged"):
        raise RuntimeError(
            f"PR #{pr_number} merge failed: {result.get('message', 'unknown error')}. "
            "Check that the PR is approved and all required checks have passed."
        )

    print(f"[merge_pr] PR #{pr_number} merged. SHA: {result.get('sha', '?')}")
    return {config.get("output_field", "pr"): {
        **pr,
        "status":    "merged",
        "merge_sha": result.get("sha"),
    }}
