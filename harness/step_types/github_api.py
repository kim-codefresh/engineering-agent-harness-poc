"""
Shared GitHub API helper for step types.

SAFETY RULE — enforced in code, not config:
  Every write operation MUST call _require_kim_codefresh_repo() first.
  This harness is only permitted to operate on repositories owned by kim-codefresh.
  Any attempt to target another organisation raises PermissionError immediately.
"""
import base64
import json
import os
import urllib.error
import urllib.request


def _require_kim_codefresh_repo(repo: str = None) -> str:
    """Hard safety gate — call before every GitHub write operation."""
    if not repo:
        repo = os.getenv("GITHUB_REPO", "")
    if not repo.startswith("kim-codefresh/"):
        raise PermissionError(
            f"SAFETY BLOCK: GITHUB_REPO='{repo}' is not under kim-codefresh. "
            "This harness may only write to kim-codefresh repositories. Aborting."
        )
    return repo


def github_request(method: str, path: str, body: dict = None) -> dict | None:
    """
    Make an authenticated GitHub API request.
    path must be a full /repos/... path or /... path relative to api.github.com.
    """
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise EnvironmentError("GITHUB_TOKEN not set")

    url = f"https://api.github.com{path}"
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "Authorization": f"token {token}",
            "Accept":        "application/vnd.github.v3+json",
            "Content-Type":  "application/json",
            "User-Agent":    "agent-harness/kim-codefresh",
        },
        data=json.dumps(body).encode() if body else None,
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise RuntimeError(f"GitHub API error {e.code}: {e.read().decode()}")


def get_file(repo: str, path: str, ref: str) -> tuple[str, str] | tuple[None, None]:
    """Returns (content, sha) or (None, None) if file doesn't exist."""
    data = github_request("GET", f"/repos/{repo}/contents/{path}?ref={ref}")
    if not data:
        return None, None
    content = base64.b64decode(data["content"]).decode()
    return content, data["sha"]


def commit_file(repo: str, path: str, content: str, message: str,
                branch: str, sha: str | None = None) -> None:
    body = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "branch":  branch,
    }
    if sha:
        body["sha"] = sha
    github_request("PUT", f"/repos/{repo}/contents/{path}", body)
