"""
github_create_branch — deterministic CODE skill.

Creates a branch in the target repo via GitHub API.
Safety gate: only operates on kim-codefresh/ repos.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from step_types.github_api import _require_kim_codefresh_repo, github_request


def execute(state: dict, config: dict) -> dict:
    repo = _require_kim_codefresh_repo()
    branch_name = state.get("branch_name")
    base_branch = config.get("base_branch", "master")

    if not branch_name:
        raise ValueError("branch_name missing from state")

    print(f"[github_create_branch] Getting SHA of {base_branch}...")
    ref = github_request("GET", f"/repos/{repo}/git/ref/heads/{base_branch}")
    if not ref:
        raise RuntimeError(f"Base branch '{base_branch}' not found in {repo}")
    sha = ref["object"]["sha"]

    # Check if branch already exists
    existing = github_request("GET", f"/repos/{repo}/git/ref/heads/{branch_name}")
    if existing:
        print(f"[github_create_branch] Branch {branch_name} already exists, reusing")
        return {"branch_name": branch_name, "branch_sha": existing["object"]["sha"], "branch_created": False}

    print(f"[github_create_branch] Creating branch {branch_name} from {base_branch} ({sha[:8]})")
    github_request("POST", f"/repos/{repo}/git/refs", {
        "ref": f"refs/heads/{branch_name}",
        "sha": sha,
    })
    print(f"[github_create_branch] ✅ Branch created: {branch_name}")
    return {"branch_name": branch_name, "branch_sha": sha, "branch_created": True}
