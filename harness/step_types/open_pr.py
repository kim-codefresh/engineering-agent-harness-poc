"""
open_pr — deterministic step type.

The agent (LLM) already decided WHAT to fix and wrote the description.
This step executes the action: create branch → commit file change → open draft PR.

Nothing here is decided by AI. All actions are deterministic HTTP calls to GitHub.
"""
import os
import time

from .github_api import (
    _require_kim_codefresh_repo,
    commit_file,
    get_file,
    github_request,
)


def execute(state: dict, config: dict) -> dict:
    # ── Safety gate — must be kim-codefresh repo ─────────────────────────────
    target_repo = state.get("target_repo") or os.getenv("GITHUB_REPO", "")
    repo = _require_kim_codefresh_repo(target_repo)

    fix      = state.get("fix", {})
    evidence = state.get("evidence", {})
    cve_id   = state.get("cve_id", "unknown-cve")

    package   = fix.get("package") or evidence.get("affected_package", "unknown")
    old_ver   = fix.get("old_version") or evidence.get("our_locked_version", "0.0.0")
    new_ver   = fix.get("new_version", "0.0.1")
    file_path = fix.get("file_path", "agent/requirements.txt")
    commit_msg = fix.get("commit_message", f"fix: bump {package} from {old_ver} to {new_ver} ({cve_id})")
    pr_description = fix.get("pr_description", (
        f"## {cve_id}\n\n"
        f"Bumps `{package}` from `{old_ver}` to `{new_ver}`.\n\n"
        f"{fix.get('diff_summary', '')}"
    ))

    base_branch = os.getenv("GITHUB_BASE_BRANCH", "feat/full-cve-flow-human-gates-lift-and-shift")
    branch = f"fix/{cve_id.lower().replace('/', '-')}-{int(time.time())}"

    print(f"[open_pr] Creating branch {branch} from {base_branch}")

    # 1. Get base branch SHA
    ref = github_request("GET", f"/repos/{repo}/git/ref/heads/{base_branch}")
    if not ref:
        raise RuntimeError(f"Base branch '{base_branch}' not found in {repo}")
    base_sha = ref["object"]["sha"]

    # 2. Create branch
    github_request("POST", f"/repos/{repo}/git/refs", {
        "ref": f"refs/heads/{branch}",
        "sha": base_sha,
    })
    print(f"[open_pr] Branch created: {branch}")

    # 3. Read the target file and apply the version bump
    current_content, file_sha = get_file(repo, file_path, base_branch)

    if current_content is not None:
        new_content = current_content.replace(
            f"{package}=={old_ver}",
            f"{package}=={new_ver}",
        )
        if new_content == current_content:
            # Package line not found in expected format — append it
            new_content = current_content.rstrip("\n") + f"\n{package}=={new_ver}\n"
            print(f"[open_pr] Package not found in {file_path}, appending line")
        else:
            print(f"[open_pr] Bumped {package} {old_ver} → {new_ver} in {file_path}")
    else:
        # File doesn't exist — create it
        new_content = f"{package}=={new_ver}\n"
        file_sha = None
        print(f"[open_pr] {file_path} not found, creating it")

    # 4. Commit the file change (CODE does this, not the agent)
    commit_file(repo, file_path, new_content, commit_msg, branch, file_sha)
    print(f"[open_pr] Committed: {commit_msg}")

    # 5. Open a draft PR (CODE does this, not the agent)
    pr = github_request("POST", f"/repos/{repo}/pulls", {
        "title": commit_msg,
        "body":  pr_description,
        "head":  branch,
        "base":  base_branch,
        "draft": True,
    })

    print(f"[open_pr] Draft PR opened: #{pr['number']} {pr['html_url']}")
    return {config.get("output_field", "pr"): {
        "number": pr["number"],
        "url":    pr["html_url"],
        "title":  pr["title"],
        "branch": branch,
        "status": "draft",
    }}
