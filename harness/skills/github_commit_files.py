"""
github_commit_files — deterministic CODE skill.

Commits patched files to the branch via GitHub API.
Safety gate: only operates on kim-codefresh/ repos.
"""
import base64
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from step_types.github_api import _require_kim_codefresh_repo, github_request, get_file


def execute(state: dict, config: dict) -> dict:
    target_repo = state.get("target_repo") or os.getenv("GITHUB_REPO", "")
    repo = _require_kim_codefresh_repo(target_repo)
    branch_name = state.get("branch_name")
    patches = state.get("patches", [])
    vulnerabilities = state.get("vulnerabilities", [])

    if not branch_name:
        raise ValueError("branch_name missing from state")

    # Build commit message listing all CVEs fixed
    cve_ids = list({v.get("cve") for v in vulnerabilities if v.get("cve")})
    commit_message = f"fix: bump dependencies to fix {', '.join(cve_ids)}"

    committed = []
    for patch in patches:
        file_path = patch.get("file")
        content = patch.get("content")
        if not file_path or not content:
            print(f"[github_commit_files] Skipping patch with no file: {patch.get('cve')}")
            continue

        print(f"[github_commit_files] Committing {file_path} on {branch_name}...")

        # Get current file SHA (required for update)
        _, file_sha = get_file(repo, file_path, branch_name)

        body = {
            "message": commit_message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch_name,
        }
        if file_sha:
            body["sha"] = file_sha

        github_request("PUT", f"/repos/{repo}/contents/{file_path}", body)
        print(f"[github_commit_files] ✅ Committed {file_path}: {patch.get('change')}")
        committed.append(file_path)

    return {"committed_files": committed, "commit_message": commit_message}
